"""The envelope's shape and the catalog's publication rules (BIZ-249): malformed envelopes are rejected before anyone sees them."""
import pytest
from pydantic import ValidationError

from app.eventing import EventDef, EventEnvelope, EventError, redact_error
from app.eventing.envelope import MAX_PAYLOAD_BYTES
from app.eventing.registry import CORE_EVENTS, event_catalog, validate_publication
from app.plugins import PluginError
from tests.eventing.fakes import definer_manifest, register


def env(**kw) -> EventEnvelope:
    return EventEnvelope(**{"name": "job.blocked", **kw})


def test_a_new_envelope_has_a_unique_id_a_utc_timestamp_and_the_core_source():
    a, b = env(), env()
    assert a.id != b.id and len(a.id) == 32
    assert a.occurred_at.endswith("Z") and a.source == "core" and a.schema_version == 1


@pytest.mark.parametrize("bad", [
    {"name": "Job.Complete"}, {"name": "nodots"}, {"name": "a..b"}, {"schema_version": 0}, {"source": ""},
    {"entities": {"banana_id": 1}}, {"payload": {"x": object()}}, {"payload": {"x": "a" * (MAX_PAYLOAD_BYTES + 1)}},
    {"unknown_field": 1}, {"entities": {"job_id": 1.5}},
])
def test_malformed_envelopes_are_rejected(bad):
    with pytest.raises(ValidationError):
        env(**bad)


def test_an_envelope_is_immutable():
    with pytest.raises(ValidationError):
        env().name = "job.failed"


def test_core_catalog_marks_job_completion_durable_and_the_rest_best_effort():
    cat = event_catalog()
    assert cat["job.complete"].durability == "durable"
    assert {n for n, d in CORE_EVENTS.items() if d.durability == "durable"} == {"job.complete"}


def test_unknown_event_and_wrong_schema_version_are_rejected():
    with pytest.raises(EventError, match="unknown event"):
        validate_publication(env(name="job.exploded"))
    with pytest.raises(EventError, match="schema_version"):
        validate_publication(env(schema_version=2))


def test_only_the_owner_may_publish_an_event(session_factory):
    register(definer_manifest("pub_one"))
    ok = EventEnvelope(name="pub_one.ready", source="pub_one", payload={"item": "x"})
    assert validate_publication(ok, as_plugin="pub_one").name == "pub_one.ready"
    with pytest.raises(EventError, match="may not publish"):
        validate_publication(ok, as_plugin="someone_else")
    with pytest.raises(EventError, match="may not publish"):
        validate_publication(env(source="core"), as_plugin="pub_one")                  # a plugin publishing a core event
    with pytest.raises(EventError, match="belongs to"):
        validate_publication(EventEnvelope(name="pub_one.ready", source="core", payload={"item": "x"}))
    with pytest.raises(EventError, match="belongs to"):
        validate_publication(env(source="pub_one"), as_plugin="pub_one")


def test_a_plugin_event_payload_is_validated_against_its_model(session_factory):
    register(definer_manifest("pub_one"))
    with pytest.raises(EventError, match="invalid payload.*item"):
        validate_publication(EventEnvelope(name="pub_one.ready", source="pub_one", payload={}), as_plugin="pub_one")


def test_manifest_event_declarations_are_validated():
    base = dict(id="ev_bad", name="x", version="1.0.0")
    from app.plugins.manifest import HOST_API, PluginManifest
    from app.eventing import EventSubscription
    from tests.plugins.dummy_plugin import DummyProvider, DummySettings
    common = dict(host_api=HOST_API, settings_model=DummySettings, factory=DummyProvider, **base)
    with pytest.raises(PluginError, match="cannot redefine the core event"):
        PluginManifest(**common, defines_events=(EventDef("job.complete"),))
    with pytest.raises(PluginError, match="starting 'ev_bad.'"):
        PluginManifest(**common, defines_events=(EventDef("other.thing"),))
    with pytest.raises(PluginError, match="unknown durability"):
        PluginManifest(**common, defines_events=(EventDef("ev_bad.x", durability="always"),))      # type: ignore[arg-type]
    with pytest.raises(PluginError, match="duplicate subscription"):
        PluginManifest(**common, subscribes=(EventSubscription("job.failed", "h"), EventSubscription("job.failed", "h")))
    with pytest.raises(PluginError, match="bad or duplicate subscription"):
        PluginManifest(**common, subscribes=(EventSubscription("Not An Event", "h"),))
    with pytest.raises(PluginError, match="positive timeout"):
        PluginManifest(**common, subscribes=(EventSubscription("job.failed", "h", timeout=0),))


def test_redact_error_masks_secrets_url_credentials_and_pairs_and_caps_the_length():
    out = redact_error(RuntimeError("POST https://bob:hunter2@host/x failed password=abc123 token: zzz plain s3cr3t-value"),
                       secrets=("s3cr3t-value",))
    for leaked in ("hunter2", "abc123", "zzz", "s3cr3t-value"):
        assert leaked not in out
    assert out.startswith("RuntimeError:") and "https://***@host/x" in out
    assert len(redact_error("x" * 5000)) == 300
