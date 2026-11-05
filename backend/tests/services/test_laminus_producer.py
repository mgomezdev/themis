"""BIZ-291: Laminus as a neutral `GcodeProducer` (identity, readiness, target support, profiles) over a fake sidecar."""
from __future__ import annotations

import httpx
import pytest

from app.services import catalog_service
from app.services.providers import contract as c
from app.services.providers.laminus.adapter import LaminusSlicingProvider, catalog_from_legacy
from app.services.providers.laminus.connection import PLUGIN_VERSION, LaminusConnection, LaminusSettings
from app.services.providers.laminus.producer import ENGINE_ID, PROVIDER_ID, LaminusGcodeProducer
from tests.services.test_slicing_provider_contract import _RAW_CATALOG, URL, sidecar  # noqa: F401  (fixture)

BED = (256.0, 256.0, 256.0)
TARGET = c.TargetPrinter(BED, "uuid-x1c")


def producer() -> LaminusGcodeProducer:
    return LaminusGcodeProducer(LaminusSlicingProvider(URL), version="9.9")


def test_identity_is_local_and_names_the_plugin_and_engine():
    ident = producer().identity
    assert (ident.provider_id, ident.version, ident.engine_id) == (PROVIDER_ID, "9.9", ENGINE_ID)
    assert ident.engine_version is None            # unknown until the sidecar answers


def test_status_ready_reports_the_engine_version_the_sidecar_gave(sidecar):  # noqa: F811
    st = producer().status()
    assert st.state is c.Readiness.READY and st.ready
    assert st.identity.engine_version == "2.3.2"


def test_status_not_ready_when_the_sidecar_answers_but_is_starting(sidecar):  # noqa: F811
    sidecar.handler = lambda request: httpx.Response(503)
    st = producer().status()
    assert st.state is c.Readiness.NOT_READY and not st.ready and "503" in st.detail


def test_status_unreachable_when_the_sidecar_does_not_answer(sidecar):  # noqa: F811
    def boom(request):
        raise httpx.ConnectError("refused")
    sidecar.handler = boom
    st = producer().status()
    assert st.state is c.Readiness.UNREACHABLE and not st.ready and st.detail


def test_status_passes_the_timeout_to_the_health_call(sidecar):  # noqa: F811
    producer().status(timeout=1.5)
    assert sidecar.client_kwargs[-1]["timeout"] == 1.5


def test_supports_target_needs_a_sensible_bed_and_a_mapped_machine_profile_the_catalog_knows(monkeypatch):
    p = producer()
    mapped = c.TargetPrinter(BED, "uuid-x1c", machine_profile="Printer A")
    monkeypatch.setattr(catalog_service, "_catalog", catalog_from_legacy(_RAW_CATALOG))
    assert p.supports_target(mapped) is True
    assert p.supports_target(c.TargetPrinter(BED, "uuid-x1c", machine_profile="Not In Catalog")) is False
    assert p.supports_target(c.TargetPrinter(BED, "uuid-x1c")) is False                     # nothing mapped
    assert p.supports_target(c.TargetPrinter((0.0, 256.0, 256.0), "uuid-x1c", machine_profile="Printer A")) is False
    monkeypatch.setattr(catalog_service, "_catalog", None)                                  # boot: can't check the name yet
    assert p.supports_target(c.TargetPrinter(BED, "uuid-x1c", machine_profile="Not In Catalog")) is True


def test_a_mapped_profile_missing_from_the_catalog_makes_the_default_ineligible(monkeypatch):
    p = producer()
    monkeypatch.setattr(catalog_service, "_catalog", catalog_from_legacy(_RAW_CATALOG))
    target = c.TargetPrinter(BED, "uuid-x1c", machine_profile="Gone Printer")
    out = c.resolve_work(c.WorkItem(c.InputKind.MODEL_3MF), target, {PROVIDER_ID: p},
                         {PROVIDER_ID: c.ProviderStatus(c.Readiness.READY)}, default=PROVIDER_ID)
    assert (out.action, out.reason) == (c.Action.BLOCKED, c.Reason.DEFAULT_INELIGIBLE)


def test_profiles_come_from_the_cached_catalog_only(monkeypatch):
    p = producer()
    monkeypatch.setattr(catalog_service, "_catalog", None)
    assert p.profiles("process", TARGET) == []                 # nothing cached: no network fetch
    monkeypatch.setattr(catalog_service, "_catalog", catalog_from_legacy(_RAW_CATALOG))
    assert [x.name for x in p.profiles("process", TARGET)] == ["0.20mm Standard"]
    assert {x.name for x in p.profiles("filament", TARGET)} == {"PLA @A", "PETG @A"}
    assert all(x.kind == "filament" for x in p.profiles("filament", TARGET))
    assert p.profiles("machine", TARGET) == []                 # only process/filament are advertised


def test_curated_settings_are_the_orca_vocabulary():
    keys = {s.key for s in producer().curated_settings()}
    assert "layer_height" in keys


def test_the_machine_ref_is_laminus_kind_colon_slug_of_the_name():
    from app.services.providers.laminus.producer import machine_ref
    assert machine_ref("Creality Ender-3 V3 SE 0.4 nozzle") == "machine:creality-ender-3-v3-se-0.4-nozzle"
    assert machine_ref("Bambu Lab X1 Carbon 0.4 nozzle") == "machine:bambu-lab-x1-carbon-0.4-nozzle"
    assert machine_ref("  Printer (A) ") == "machine:printer-a"


def test_profiles_for_a_machine_come_from_laminus_by_its_derived_ref(sidecar, monkeypatch):  # noqa: F811
    monkeypatch.setattr(catalog_service, "_catalog", catalog_from_legacy(_RAW_CATALOG))
    seen = {}

    def handler(request):
        seen["path"], seen["kind"] = request.url.path, request.url.params.get("kind")
        return httpx.Response(200, json={"catalog_version": "v", "machine_ref": "machine:printer-a", "presets": [
            {"ref": "process:x", "uuid": "p-9", "name": "0.16mm Fine", "kind": "process", "compatible_models": ["A"]},
            {"ref": "process:y", "uuid": "p-8", "name": "0.28mm Draft", "kind": "process"}]})
    sidecar.handler = handler
    target = c.TargetPrinter(BED, "uuid-x1c", machine_profile="Printer A")
    assert [x.name for x in producer().profiles("process", target)] == ["0.16mm Fine", "0.28mm Draft"]
    assert (seen["path"], seen["kind"]) == ("/api/v1/profiles/machines/machine:printer-a/presets", "process")


def test_profiles_fall_back_to_the_catalog_filter_when_laminus_cannot_answer(sidecar, monkeypatch):  # noqa: F811
    monkeypatch.setattr(catalog_service, "_catalog", catalog_from_legacy(_RAW_CATALOG))
    sidecar.handler = lambda request: httpx.Response(404, json={"catalog_version": "v", "error": "unknown_machine_ref"})
    target = c.TargetPrinter(BED, "uuid-x1c", machine_profile="Printer A")
    assert [x.name for x in producer().profiles("filament", target)] == ["PLA @A"]       # PETG names no machine
    assert [x.name for x in producer().profiles("process", target)] == ["0.20mm Standard"]


def test_produce_rejects_gcode_inputs_and_honours_cancel(tmp_path):
    p = producer()
    req = c.ProduceRequest(tmp_path / "a.gcode", c.InputKind.GCODE, 1, TARGET)
    with pytest.raises(c.ProviderError) as e:
        p.produce(req, tmp_path)
    assert e.value.code is c.ErrorCode.UNSUPPORTED_INPUT
    token = c.CancelToken()
    token.cancel()
    with pytest.raises(c.ProviderError) as e:
        p.produce(c.ProduceRequest(tmp_path / "a.3mf", c.InputKind.MODEL_3MF, 1, TARGET), tmp_path, cancel=token)
    assert e.value.code is c.ErrorCode.CANCELLED


def test_produce_without_a_machine_profile_is_an_unsupported_target(tmp_path):
    with pytest.raises(c.ProviderError) as e:
        producer().produce(c.ProduceRequest(tmp_path / "a.3mf", c.InputKind.MODEL_3MF, 1, TARGET), tmp_path)
    assert e.value.code is c.ErrorCode.UNSUPPORTED_TARGET


MAPPED = c.TargetPrinter(BED, "uuid-x1c", machine_profile="Printer A")
SETTINGS = c.SliceSettings(process_profile="0.20mm Standard", filaments=(c.FilamentChoice(1, "PLA", "PLA @A"),))


def _produce(tmp_path, *, settings=SETTINGS, want=c.ArtifactKind.GCODE, cancel=None, prod=None):
    src = tmp_path / "m.3mf"
    src.write_bytes(b"PK")
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    req = c.ProduceRequest(src, c.InputKind.MODEL_3MF, 2, MAPPED, settings, want)
    return (prod or producer()).produce(req, out, cancel=cancel), out


def _code(fn) -> c.ErrorCode:
    with pytest.raises(c.ProviderError) as e:
        fn()
    return e.value.code


@pytest.fixture
def primed(monkeypatch):
    monkeypatch.setattr(catalog_service, "_catalog", catalog_from_legacy(_RAW_CATALOG))


def test_produce_resolves_names_to_refs_and_maps_plate_type_and_overrides(sidecar, primed, tmp_path):  # noqa: F811
    settings = c.SliceSettings(process_profile="0.20mm Standard", filaments=(c.FilamentChoice(1, "PLA", "PLA @A"),),
                               overrides={"layer_height": "0.12"}, plate_type="Textured PEI Plate")
    art, out = _produce(tmp_path, settings=settings)
    assert art.kind is c.ArtifactKind.GCODE and art.produced_by.provider_id == PROVIDER_ID
    assert art.path == out / "out.gcode" and art.path.read_bytes() == b"G1 X0\n"
    body = next(r for r in sidecar.requests if r.url.path == "/api/v1/jobs").content.decode(errors="ignore")
    for needle in ("m-1", "p-1", '["f-1"]', 'name="plate"\r\n\r\n2', '"curr_bed_type": "Textured PEI Plate"', '"layer_height": "0.12"'):
        assert needle in body
    assert "export_3mf" not in body


def test_produce_a_gcode_3mf_asks_for_the_archive(sidecar, primed, tmp_path):  # noqa: F811
    _produce(tmp_path, want=c.ArtifactKind.GCODE_3MF)
    body = next(r for r in sidecar.requests if r.url.path == "/api/v1/jobs").content.decode(errors="ignore")
    assert "m.gcode.3mf" in body


def test_produce_an_unknown_profile_is_invalid_settings_with_the_actionable_text(sidecar, primed, tmp_path):  # noqa: F811
    bad = c.SliceSettings(process_profile="0.08mm Nope", filaments=(c.FilamentChoice(1, "PLA", "PLA @A"),))
    with pytest.raises(c.ProviderError) as e:
        _produce(tmp_path, settings=bad)
    assert e.value.code is c.ErrorCode.INVALID_SETTINGS
    assert "process preset '0.08mm Nope'" in e.value.message and "Refresh the profile sync" in e.value.message
    assert not [r for r in sidecar.requests if r.url.path == "/api/v1/jobs"]


def test_produce_a_non_curated_override_is_invalid_settings(sidecar, primed, tmp_path):  # noqa: F811
    bad = c.SliceSettings(process_profile="0.20mm Standard", filaments=(c.FilamentChoice(1, "PLA", "PLA @A"),),
                          overrides={"wipe_tower_x": "5"})
    assert _code(lambda: _produce(tmp_path, settings=bad)) is c.ErrorCode.INVALID_SETTINGS
    assert sidecar.requests == []


def test_produce_with_an_unreadable_catalog_is_unreachable(sidecar, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(catalog_service, "_catalog", None)
    sidecar.handler = lambda request: httpx.Response(500)
    assert _code(lambda: _produce(tmp_path)) is c.ErrorCode.UNREACHABLE


def test_produce_a_failed_slice_is_failed_with_the_engine_message(sidecar, primed, tmp_path):  # noqa: F811
    sidecar.status = "failed"
    with pytest.raises(c.ProviderError) as e:
        _produce(tmp_path)
    assert e.value.code is c.ErrorCode.FAILED and "bad geometry" in e.value.message


def test_produce_past_the_poll_ceiling_is_a_timeout(sidecar, primed, tmp_path):  # noqa: F811
    def pending(request):
        if request.url.path == "/api/v1/jobs/job-1":
            return httpx.Response(200, json={"status": "slicing", "artifact": {"name": None}})
        return httpx.Response(202, json={"id": "job-1"})
    sidecar.handler = pending
    prod = LaminusGcodeProducer(LaminusSlicingProvider(URL, poll_timeout_s=0.0), version="9.9")
    assert _code(lambda: _produce(tmp_path, prod=prod)) is c.ErrorCode.TIMEOUT


def test_produce_stops_polling_when_cancelled_and_returns_nothing(sidecar, primed, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr("app.services.providers.laminus.sidecar_client.time.sleep", lambda s: None)
    token, polls = c.CancelToken(), []

    def pending(request):
        if request.url.path == "/api/v1/jobs/job-1":
            polls.append(1)
            token.cancel()                                   # the job is cancelled while Laminus slices
            return httpx.Response(200, json={"status": "slicing", "artifact": {"name": None}})
        return httpx.Response(202, json={"id": "job-1"})
    sidecar.handler = pending
    assert _code(lambda: _produce(tmp_path, cancel=token)) is c.ErrorCode.CANCELLED
    assert len(polls) == 1 and list((tmp_path / "out").iterdir()) == []


def test_produce_cancelled_while_downloading_deletes_the_result(sidecar, primed, tmp_path):  # noqa: F811
    token = c.CancelToken()

    def cancel_on_download(request):
        if request.url.path.endswith("/artifact"):
            token.cancel()                                   # cancelled after the slice finished, mid-download
        return _route(request, sidecar)
    sidecar.handler = cancel_on_download
    assert _code(lambda: _produce(tmp_path, cancel=token)) is c.ErrorCode.CANCELLED
    assert list((tmp_path / "out").iterdir()) == []


def _route(request, sc):
    path = request.url.path
    if path == "/api/v1/jobs":
        return httpx.Response(202, json={"id": "job-1"})
    if path == "/api/v1/jobs/job-1":
        return httpx.Response(200, json={"status": "completed", "artifact": {"name": "out.gcode", "available": True}})
    if path == "/api/v1/jobs/job-1/artifact":
        return httpx.Response(200, content=sc.gcode)
    return httpx.Response(404)


def test_connection_builds_the_producer_with_the_plugin_options_and_none_when_unconfigured():
    assert LaminusConnection(LaminusSettings(url=None)).gcode_producer() is None
    conn = LaminusConnection(LaminusSettings(url=URL, request_timeout_s=11.0, poll_timeout_s=12.0))
    prod = conn.gcode_producer()
    assert isinstance(prod, LaminusGcodeProducer) and prod.identity.version == PLUGIN_VERSION
    assert prod._slicing._request_timeout_s == 11.0 and prod._slicing._poll_timeout_s == 12.0


def test_plugin_version_matches_the_package_manifest():
    from pathlib import Path
    toml = (Path(__file__).parents[2] / "app/plugins/laminus/themis-plugin.toml").read_text()
    assert f'version = "{PLUGIN_VERSION}"' in toml


def test_the_plugin_instance_satisfies_the_gcode_produce_protocol():
    from app.plugins.capabilities.gcode_produce import SlicingPlugin
    assert isinstance(LaminusConnection(LaminusSettings(url=URL)), SlicingPlugin)
    assert not isinstance(object(), SlicingPlugin)
