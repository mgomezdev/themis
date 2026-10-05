"""The provider-neutral `/api/v1/inventory/*` API (BIZ-215): shapes, capability gating (409 `capability_unavailable`),
scoping of low-stock state per provider, and scope enforcement. `raw` provider payloads must never leak."""
import copy

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import SCOPES
from app.main import app
from app.models import ApiKey, InventoryConfig
from app.plugins.kinds.filament_inventory import (
    LABEL_SCAN, PROFILE_LINKS_READ, PROFILE_LINKS_WRITE, REMOTE, TRACKS_WEIGHT, WRITE_WEIGHT, InvMaterial,
)
from app.services.api_key_service import generate_key, hash_key
from tests import spoolman_mock
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import enable_spoolman, spool, use_provider


@pytest.fixture(autouse=True)
def _restore_spoolman_mock():
    saved = (copy.deepcopy(spoolman_mock._FILAMENTS), copy.deepcopy(spoolman_mock._SPOOLS))
    yield
    spoolman_mock._FILAMENTS[:] = saved[0]
    spoolman_mock._SPOOLS[:] = saved[1]


def _fake(caps=frozenset({TRACKS_WEIGHT, WRITE_WEIGHT, PROFILE_LINKS_READ, PROFILE_LINKS_WRITE})) -> FakeInventoryProvider:
    return FakeInventoryProvider(
        materials=[InvMaterial(ref="1", name="PLA White", material="PLA", color_hex="#FFFFFF", vendor="Elegoo",
                               raw={"secret": "leak-me"})],
        spools=[spool("7", 250.0, name="PLA White", material="PLA", vendor="Elegoo", material_ref="1", location="Shelf A",
                      raw={"secret": "leak-me"})],
        capabilities=caps)


# --- no provider at all ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("method, path, body", [
    ("GET", "/api/v1/inventory/materials", None),
    ("GET", "/api/v1/inventory/spools", None),
    ("POST", "/api/v1/inventory/sync-now", None),
    ("POST", "/api/v1/inventory/resolve-label", {"text": "s-1"}),
    ("PATCH", "/api/v1/inventory/materials/1/profile-links", {"links": {}}),
])
async def test_routes_that_need_a_provider_answer_409_when_none_is_active(client, method, path, body):
    resp = await client.request(method, path, json=body)
    assert resp.status_code == 409
    assert (resp.json()["error"], resp.json()["kind"]) == ("capability_unavailable", "filament_inventory")


async def test_sync_status_and_settings_never_fail_without_a_provider(client):
    st = (await client.get("/api/v1/inventory/sync-status")).json()
    assert st == {"provider": None, "capabilities": [], "enabled": False, "interval_minutes": 15, "last_sync_at": None,
                  "last_attempt_at": None, "last_error": None, "last_error_code": None}
    assert (await client.get("/api/v1/inventory/settings")).json() == {
        "provider": None, "deduct_on_complete": True, "low_stock": {"default_g": None, "overrides": {}}}


# --- reads -------------------------------------------------------------------------------------------------------------

async def test_materials_and_spools_use_the_neutral_shape_and_never_expose_raw(client):
    await use_provider(_fake(), plugin_id="fake_inventory")

    materials = (await client.get("/api/v1/inventory/materials")).json()
    spools = (await client.get("/api/v1/inventory/spools")).json()

    assert (materials["provider"], materials["stale"]) == ("fake_inventory", False) and materials["as_of"]
    assert materials["items"] == [{"ref": "1", "name": "PLA White", "material": "PLA", "color_hex": "#FFFFFF", "vendor": "Elegoo",
                                   "density": None, "diameter": None, "profile_links": None, "archived": False}]
    (s,) = spools["items"]
    assert (s["ref"], s["material_ref"], s["remaining_g"], s["location"], s["label"], s["archived"], s["url"]) == (
        "7", "1", 250.0, "Shelf A", "Elegoo PLA White", False, None)
    assert s["material"]["name"] == "PLA White"
    assert "leak-me" not in materials.__str__() + spools.__str__() and "raw" not in s and "raw" not in s["material"]


async def test_a_failing_provider_is_a_503_with_its_message_and_never_a_500(client):
    fake = _fake()
    fake.fail_with = RuntimeError("provider exploded")
    await use_provider(fake)
    for path in ("/api/v1/inventory/materials", "/api/v1/inventory/spools"):
        resp = await client.get(path)
        assert resp.status_code == 503 and "provider exploded" in resp.json()["detail"]


# --- sync-now / resolve-label / profile links: capability gated ---------------------------------------------------------

async def test_sync_now_needs_a_remote_provider(client):
    await use_provider(_fake())                                           # not REMOTE: nothing to sync
    resp = await client.post("/api/v1/inventory/sync-now")
    assert resp.status_code == 409 and resp.json() == {"error": "capability_unavailable", "kind": "filament_inventory", "capability": REMOTE}


async def test_resolve_label_needs_label_scan(client):
    await use_provider(_fake())
    resp = await client.post("/api/v1/inventory/resolve-label", json={"text": "s-1"})
    assert resp.status_code == 409 and resp.json()["capability"] == LABEL_SCAN


async def test_profile_links_need_the_write_capability_and_round_trip_with_it(client):
    fake = _fake(caps=frozenset({TRACKS_WEIGHT}))
    await use_provider(fake)
    denied = await client.patch("/api/v1/inventory/materials/1/profile-links", json={"links": {"P": ["x"]}})
    assert denied.status_code == 409 and denied.json()["capability"] == PROFILE_LINKS_WRITE and fake.materials["1"].profile_links is None

    fake2 = _fake()
    await use_provider(fake2)
    ok = await client.patch("/api/v1/inventory/materials/1/profile-links", json={"links": {"P": ["x", "y"]}})
    assert ok.status_code == 200 and ok.json()["profile_links"] == {"P": ["x", "y"]}
    assert fake2.materials["1"].profile_links == {"P": ["x", "y"]}
    missing = await client.patch("/api/v1/inventory/materials/99/profile-links", json={"links": {}})
    assert missing.status_code == 404                                     # the provider's own status passes through


# --- Spoolman as the provider (full capability set) ------------------------------------------------------------------------

async def test_spoolman_provider_end_to_end_through_the_neutral_api(client, spoolman_upstream):
    await enable_spoolman(api_key="s3cret")

    materials = (await client.get("/api/v1/inventory/materials")).json()
    spools = (await client.get("/api/v1/inventory/spools")).json()
    sync = await client.post("/api/v1/inventory/sync-now")
    status = (await client.get("/api/v1/inventory/sync-status")).json()
    label = (await client.post("/api/v1/inventory/resolve-label", json={"text": "web+spoolman:s-5"})).json()
    junk = (await client.post("/api/v1/inventory/resolve-label", json={"text": "hello"})).json()
    patched = await client.patch("/api/v1/inventory/materials/2/profile-links", json={"links": {"P1S": ["Bambu PLA @P1S"]}})

    assert materials["provider"] == "spoolman" and [m["ref"] for m in materials["items"]] == ["1", "2"]
    assert spools["items"][0]["url"] == "http://spoolman.test/spool/show/1" and spools["items"][0]["remaining_g"] == 800.0
    assert (sync.status_code, sync.json()) == (200, {"material_count": 2, "spool_count": 2})
    assert status["provider"] == "spoolman" and status["enabled"] is True and "REMOTE" in status["capabilities"]
    assert status["last_sync_at"] is not None and status["last_error"] is None
    assert (label, junk) == ({"spool_ref": "5"}, {"spool_ref": None})
    assert patched.status_code == 200 and patched.json()["profile_links"] == {"P1S": ["Bambu PLA @P1S"]}


async def test_sync_now_failure_is_a_503_and_is_recorded_in_the_status(client, spoolman_upstream):
    await enable_spoolman()
    spoolman_upstream.handler = lambda request: httpx.Response(500, text="boom")
    resp = await client.post("/api/v1/inventory/sync-now")
    assert resp.status_code == 503
    st = (await client.get("/api/v1/inventory/sync-status")).json()
    assert st["last_error_code"] == "500" and st["last_sync_at"] is None


# --- settings --------------------------------------------------------------------------------------------------------------

async def test_deduct_on_complete_round_trips_and_omitted_fields_are_unchanged(client, session_factory):
    assert (await client.put("/api/v1/inventory/settings", json={"deduct_on_complete": False})).json()["deduct_on_complete"] is False
    again = (await client.put("/api/v1/inventory/settings", json={})).json()
    assert again["deduct_on_complete"] is False                            # {} changed nothing
    async with session_factory() as s:
        assert (await s.get(InventoryConfig, 1)).deduct_on_complete is False


async def test_low_stock_thresholds_are_stored_namespaced_and_scoped_to_the_active_provider(client, session_factory):
    await use_provider(_fake(), plugin_id="provider_a")
    put = await client.put("/api/v1/inventory/settings", json={"low_stock": {"default_g": 120, "overrides": {"1": 30.5}}})
    assert put.json()["low_stock"] == {"default_g": 120.0, "overrides": {"1": 30.5}}
    async with session_factory() as s:
        assert (await s.get(InventoryConfig, 1)).low_stock_overrides == {"provider_a:1": 30.5}

    await use_provider(_fake(), plugin_id="provider_b")                        # switch providers
    assert (await client.get("/api/v1/inventory/settings")).json()["low_stock"]["overrides"] == {}
    await client.put("/api/v1/inventory/settings", json={"low_stock": {"default_g": 50, "overrides": {"1": 5}}})
    async with session_factory() as s:
        assert (await s.get(InventoryConfig, 1)).low_stock_overrides == {"provider_a:1": 30.5, "provider_b:1": 5.0}

    await use_provider(_fake(), plugin_id="provider_a")                        # and back: a's thresholds are intact
    assert (await client.get("/api/v1/inventory/settings")).json()["low_stock"]["overrides"] == {"1": 30.5}


async def test_low_stock_thresholds_need_a_provider_that_tracks_weight(client, session_factory):
    await use_provider(_fake(caps=frozenset()))
    resp = await client.put("/api/v1/inventory/settings", json={"low_stock": {"default_g": 10}})
    assert resp.status_code == 409 and resp.json()["capability"] == TRACKS_WEIGHT
    assert (await client.get("/api/v1/inventory/settings")).json()["low_stock"]["default_g"] is None
    ok = await client.put("/api/v1/inventory/settings", json={"deduct_on_complete": False})   # non-threshold settings still save
    assert ok.status_code == 200


@pytest.mark.parametrize("low_stock", [
    {"default_g": -1}, {"default_g": 1e9}, {"overrides": {"1": -5}}, {"overrides": {"1": 1e9}},
    {"overrides": {"": 5}}, {"overrides": {"spoolman:3": 5}},
])
async def test_invalid_thresholds_are_422_and_change_nothing(client, low_stock):
    await use_provider(_fake())
    await client.put("/api/v1/inventory/settings", json={"low_stock": {"default_g": 50, "overrides": {}}})
    assert (await client.put("/api/v1/inventory/settings", json={"low_stock": low_stock})).status_code == 422
    assert (await client.get("/api/v1/inventory/settings")).json()["low_stock"]["default_g"] == 50.0


# --- scopes ----------------------------------------------------------------------------------------------------------------

async def _client_with(session_factory, scopes: list[str]) -> AsyncClient:
    raw, prefix = generate_key()
    async with session_factory() as s:
        s.add(ApiKey(name="probe", key_prefix=prefix, key_hash=hash_key(raw), scopes=scopes, enabled=True,
                     created_at="2026-01-01T00:00:00"))
        await s.commit()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-Api-Key": raw})


async def test_inventory_routes_need_the_inventory_scopes_not_the_spoolman_ones(client, session_factory):
    await use_provider(_fake())
    legacy_only = await _client_with(session_factory, ["spoolman:read", "spoolman:write", "settings:read", "settings:write"])
    reader = await _client_with(session_factory, ["inventory:read"])
    async with legacy_only, reader:
        assert (await legacy_only.get("/api/v1/inventory/materials")).status_code == 403
        assert (await reader.get("/api/v1/inventory/materials")).status_code == 200
        assert (await reader.put("/api/v1/inventory/settings", json={"deduct_on_complete": False})).status_code == 403
        assert (await reader.post("/api/v1/inventory/sync-now")).status_code == 403          # it records state and can alert
        assert (await reader.patch("/api/v1/inventory/materials/1/profile-links", json={"links": {}})).status_code == 403


def test_the_inventory_scopes_exist():
    assert {"inventory:read", "inventory:write"} <= SCOPES
