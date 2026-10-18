"""System-level plugin scenarios (BIZ-304): interactions across the host, the registry, the HTTP API and core-owned records, using
fake plugins only (no printers, no network). Single-boundary behaviour is held by each component's own tests; the full scenario ->
test mapping is docs/plugin-test-matrix.md, and `test_plugin_matrix_doc.py` fails when a row names a test that does not exist."""
import pytest
from pydantic import BaseModel
from sqlalchemy import select, text

from app import plugins
from app.models import Job, JobSpoolSnapshot, Printer
from app.plugins import migrations as plugin_migrations
from app.plugins.capabilities.filament_inventory import CAPABILITY, InvMaterial
from app.plugins.host import plugin_host
from app.plugins.manifest import HOST_API, PluginManifest, Provide, Requirement
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import spool, use_provider
from tests.plugins.dummy_plugin import DummyProvider, DummySettings, make_manifest, migration

SECRET = "tok-matrix-4f9a"


def fragile(settings: DummySettings):
    """Builds only while no token is configured; with one it fails the way a bad upstream login would, embedding the token."""
    if settings.token:
        raise RuntimeError(f"cannot reach upstream with token {settings.token}")
    return DummyProvider(settings)


class _Duck:
    async def test_connection(self):
        return {}


class _NoSettings(BaseModel):
    pass


async def _put(client, plugin_id: str, **body):
    resp = await client.put(f"/api/v1/plugins/{plugin_id}", json=body)
    assert resp.status_code == 200, resp.text
    return resp


async def _listed(client) -> dict[str, dict]:
    return {p["id"]: p for p in (await client.get("/api/v1/plugins")).json()["plugins"]}


# ---- broken plugins never stop Themis or the healthy ones, and every failure is observable and clean ---------------------------

async def test_a_mix_of_broken_plugins_never_stops_boot_or_the_healthy_plugin_and_each_failure_is_reported_cleanly(client, session_factory):
    healthy = make_manifest("mx_ok")
    factory_fails = make_manifest("mx_factory", factory=fragile)
    contract_fails = PluginManifest(id="mx_contract", name="mx_contract", version="1", host_api=HOST_API, settings_model=_NoSettings,
                                    factory=lambda _s: _Duck(), provides={CAPABILITY: Provide()})
    needs_missing = make_manifest("mx_needs", requires=(Requirement("mx_nothing.cap"),))
    migration_fails = make_manifest("mx_migrates", migrations=(migration(1, "SELECT * FROM table_that_does_not_exist", "SELECT 1"),))
    for m in (healthy, factory_fails, contract_fails, needs_missing, migration_fails):
        plugins.register_plugin(m)
    async with session_factory() as s:
        await plugin_migrations.run_plugin_migrations(await s.connection(), [migration_fails])       # what startup does after loading code
        await s.commit()

    await _put(client, "mx_ok", enabled=True)
    await _put(client, "mx_factory", enabled=True, secrets={"token": SECRET})
    await _put(client, "mx_contract", enabled=True)
    await _put(client, "mx_needs", enabled=True)
    await _put(client, "mx_migrates", enabled=True)
    for cap, pid in (("mx_ok.ping", "mx_ok"), ("mx_factory.ping", "mx_factory"), (CAPABILITY, "mx_contract"),
                     ("mx_needs.ping", "mx_needs"), ("mx_migrates.ping", "mx_migrates")):
        await client.put(f"/api/v1/capabilities/{cap}/provider", json={"plugin_id": pid})

    # the expected failures...
    resp = await client.get("/api/v1/plugins")
    listed = {p["id"]: p for p in resp.json()["plugins"]}
    assert "could not build provider" in listed["mx_factory"]["error"] and not listed["mx_factory"]["active"]
    assert "missing async method(s)" in listed["mx_contract"]["error"] and not listed["mx_contract"]["active"]
    assert listed["mx_needs"]["provides"][0]["status"] == "waiting" and listed["mx_needs"]["provides"][0]["waiting_on"] == ["mx_nothing.cap"]
    assert "migration v1" in listed["mx_migrates"]["error"] and not listed["mx_migrates"]["active"]
    # ...are observable without leaking a secret or a traceback...
    assert SECRET not in resp.text and "Traceback" not in resp.text and "***" in listed["mx_factory"]["error"]
    # ...and nothing else stopped: the healthy plugin serves, and so does a bundled one.
    assert listed["mx_ok"]["active"] and (await plugin_host.call("mx_ok.ping", "ping")).value == "pong"
    assert (await client.get("/api/v1/health")).status_code == 200

    # recovery needs no restart: clearing the bad token rebuilds the failed plugin and its error goes away
    await _put(client, "mx_factory", secrets={"token": ""})
    again = (await _listed(client))["mx_factory"]
    assert again["error"] is None and again["active"]


async def test_a_failing_call_into_one_provider_does_not_touch_the_others_capabilities(client):
    plugins.register_plugin(make_manifest("mx_flaky"))
    plugins.register_plugin(make_manifest("mx_steady"))
    await _put(client, "mx_flaky", enabled=True, settings={"mode": "raise"})
    await _put(client, "mx_steady", enabled=True)
    for pid in ("mx_flaky", "mx_steady"):
        await client.put(f"/api/v1/capabilities/{pid}.ping/provider", json={"plugin_id": pid})

    bad = await plugin_host.call("mx_flaky.ping", "ping")
    good = await plugin_host.call("mx_steady.ping", "ping")

    assert (bad.ok, bad.reason) == (False, "error") and good.ok
    listed = await _listed(client)
    assert listed["mx_flaky"]["error"] and listed["mx_steady"]["error"] is None


# ---- provider switching leaves core-owned records alone -------------------------------------------------------------------------

async def test_switching_the_inventory_provider_needs_no_core_change_and_preserves_jobs_printers_and_references(
        client, session_factory, create_job):
    job_id = await create_job()
    async with session_factory() as s:
        s.add(JobSpoolSnapshot(job_id=job_id, provider="mx_prov_a", spool_ref="7", pre_weight_g=900.0, source="live",
                               taken_at="2026-10-01T10:00:00"))
        await s.commit()
        before = [(r.id, r.name, r.plugin_id) for r in (await s.execute(select(Printer))).scalars()]
        jobs_before = (await s.execute(select(Job.id, Job.status))).all()

    a = FakeInventoryProvider(materials=[InvMaterial(ref="1", name="A white")], spools=[spool("7", 800.0, name="A white")])
    b = FakeInventoryProvider(materials=[InvMaterial(ref="9", name="B black")], spools=[spool("42", 500.0, name="B black", material_ref="9")])
    await use_provider(a, "mx_prov_a")
    assert "A white" in (await client.get("/api/v1/inventory/spools")).text
    await use_provider(b, "mx_prov_b")                                                            # the switch: no core code involved
    switched = await client.get("/api/v1/inventory/spools")
    assert "B black" in switched.text and "A white" not in switched.text
    await use_provider(a, "mx_prov_a")
    assert "A white" in (await client.get("/api/v1/inventory/spools")).text                       # and back

    async with session_factory() as s:                                                            # core-owned records are untouched
        assert [(r.id, r.name, r.plugin_id) for r in (await s.execute(select(Printer))).scalars()] == before
        assert (await s.execute(select(Job.id, Job.status))).all() == jobs_before
        snap = (await s.execute(select(JobSpoolSnapshot))).scalar_one()
        assert (snap.provider, snap.spool_ref, snap.pre_weight_g) == ("mx_prov_a", "7", 900.0)    # still names the old provider's ref


async def test_with_no_provider_the_neutral_api_says_so_and_the_rest_of_the_app_still_answers(client):
    plugins.register_plugin(make_manifest("mx_idle"))
    resp = await client.get("/api/v1/inventory/spools")

    assert resp.status_code == 409 and resp.json()["error"] == "capability_unavailable"
    assert (await client.get("/api/v1/printers")).status_code == 200
    assert (await client.get("/api/v1/jobs")).status_code == 200


async def test_a_disabled_plugin_is_listed_disabled_so_its_navigation_goes_and_its_calls_stop(client):
    plugins.register_plugin(make_manifest("mx_toggle"))
    await _put(client, "mx_toggle", enabled=True)
    await client.put("/api/v1/capabilities/mx_toggle.ping/provider", json={"plugin_id": "mx_toggle"})
    assert (await _listed(client))["mx_toggle"]["enabled"] and (await plugin_host.call("mx_toggle.ping", "ping")).ok

    await _put(client, "mx_toggle", enabled=False)

    entry = (await _listed(client))["mx_toggle"]
    assert entry["enabled"] is False and entry["active"] is False                                # the frontend drops its nav on this
    off = await plugin_host.call("mx_toggle.ping", "ping")
    assert (off.ok, off.reason) == (False, "inactive")
