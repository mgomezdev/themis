"""`GET /api/v1/capabilities` and `PUT /api/v1/capabilities/{cap}/provider`."""
import pytest

from app import plugins
from app.plugins.manifest import Requirement
from tests.plugins.dummy_plugin import make_manifest


def _inv(body):
    return next(c for c in body["capabilities"] if c["id"] == "inventory.filament")


async def test_list_includes_the_core_capability_with_its_providers(client):
    inv = _inv((await client.get("/api/v1/capabilities")).json())
    assert inv["definer"] is None and inv["selected"] is None and inv["status"] == "none_selected" and inv["explicit"] is False
    assert {p["plugin_id"] for p in inv["providers"]} == {"spoolman", "local_inventory"}
    assert inv["version"] == 1 and "WRITE_WEIGHT" in inv["features"]


async def test_put_provider_selects_enables_and_is_explicit(client):
    r = await client.put("/api/v1/capabilities/inventory.filament/provider", json={"plugin_id": "local_inventory"})
    assert r.status_code == 200 and r.json() == {"capability": "inventory.filament", "plugin_id": "local_inventory", "explicit": True}
    inv = _inv((await client.get("/api/v1/capabilities")).json())
    assert (inv["selected"], inv["explicit"], inv["status"]) == ("local_inventory", True, "serving")
    assert (await client.get("/api/v1/plugins/local_inventory")).json()["enabled"] is True


@pytest.mark.parametrize("cap,pid", [("nope.nothing", "spoolman"), ("inventory.filament", "ghost_plugin")])
async def test_put_provider_rejects_unknown_capability_and_non_provider_and_changes_nothing(client, cap, pid):
    r = await client.put(f"/api/v1/capabilities/{cap}/provider", json={"plugin_id": pid})
    assert r.status_code == 422
    assert _inv((await client.get("/api/v1/capabilities")).json())["selected"] is None


async def test_put_provider_none_is_remembered(client):
    await client.put("/api/v1/capabilities/inventory.filament/provider", json={"plugin_id": "local_inventory"})
    r = await client.put("/api/v1/capabilities/inventory.filament/provider", json={"plugin_id": None})
    assert r.status_code == 200 and r.json()["plugin_id"] is None
    inv = _inv((await client.get("/api/v1/capabilities")).json())
    assert (inv["selected"], inv["explicit"], inv["status"]) == (None, True, "none_selected")


async def test_put_provider_that_would_form_a_requirement_cycle_is_422_and_changes_nothing(client):
    a = make_manifest("plug_a", cap="plug_a.x", requires=(Requirement("plug_b.x"),))
    b = make_manifest("plug_b", cap="plug_b.x", requires=(Requirement("plug_a.x"),))
    plugins.register_plugin(a)
    plugins.register_plugin(b)
    assert (await client.put("/api/v1/capabilities/plug_b.x/provider", json={"plugin_id": "plug_b"})).status_code == 200
    r = await client.put("/api/v1/capabilities/plug_a.x/provider", json={"plugin_id": "plug_a"})
    assert r.status_code == 422 and "cycle" in r.json()["detail"]
    cap = next(c for c in (await client.get("/api/v1/capabilities")).json()["capabilities"] if c["id"] == "plug_a.x")
    assert cap["selected"] is None


async def test_plugin_defined_capabilities_are_listed_with_their_definer_and_waiting_consumers(client):
    base = make_manifest("plug_a", cap="plug_a.ping")
    consumer = make_manifest("consumer", cap="consumer.use", requires=(Requirement("plug_a.ping"),))
    plugins.register_plugin(base)
    plugins.register_plugin(consumer)
    await client.put("/api/v1/capabilities/consumer.use/provider", json={"plugin_id": "consumer"})
    caps = {c["id"]: c for c in (await client.get("/api/v1/capabilities")).json()["capabilities"]}
    assert caps["plug_a.ping"]["definer"] == "plug_a" and caps["plug_a.ping"]["requires_by"] == [{"plugin_id": "consumer", "min_version": 1}]
    assert (caps["consumer.use"]["status"], caps["consumer.use"]["waiting_on"]) == ("waiting", ["plug_a.ping"])
    plug = next(p for p in (await client.get("/api/v1/plugins")).json()["plugins"] if p["id"] == "consumer")
    assert plug["provides"][0]["status"] == "waiting" and plug["provides"][0]["waiting_on"] == ["plug_a.ping"] and plug["active"] is False


async def test_a_stored_selection_whose_definer_is_gone_is_listed_as_dormant(client):
    from app.plugins.host import plugin_host
    plugins.register_plugin(make_manifest("plug_a", cap="plug_a.ping"))
    await client.put("/api/v1/capabilities/plug_a.ping/provider", json={"plugin_id": "plug_a"})
    plugins._REGISTRY.pop("plug_a")
    await plugin_host.reload()
    dormant = next(c for c in (await client.get("/api/v1/capabilities")).json()["capabilities"] if c["id"] == "plug_a.ping")
    assert (dormant["status"], dormant["selected"], dormant["providers"]) == ("dormant", "plug_a", [])


async def test_the_old_extension_slots_route_is_gone(client):
    assert (await client.put("/api/v1/extension-slots/filament_inventory", json={"plugin_id": None})).status_code in (404, 405)


async def test_capability_routes_need_the_settings_scopes(client, session_factory):
    from tests.api.test_inventory_api import _client_with
    reader = await _client_with(session_factory, ["settings:read"])
    nothing = await _client_with(session_factory, ["inventory:read"])
    async with reader, nothing:
        assert (await reader.get("/api/v1/capabilities")).status_code == 200
        assert (await reader.put("/api/v1/capabilities/inventory.filament/provider", json={"plugin_id": None})).status_code == 403
        assert (await nothing.get("/api/v1/capabilities")).status_code == 403


async def test_a_dormant_selection_can_be_cleared_through_the_api(client):
    from app.plugins.host import plugin_host
    plugins.register_plugin(make_manifest("plug_a", cap="plug_a.ping"))
    await client.put("/api/v1/capabilities/plug_a.ping/provider", json={"plugin_id": "plug_a"})
    plugins._REGISTRY.pop("plug_a")
    await plugin_host.reload()
    cleared = await client.put("/api/v1/capabilities/plug_a.ping/provider", json={"plugin_id": None})
    assert cleared.status_code == 200 and cleared.json()["plugin_id"] is None
    assert "plug_a.ping" not in {c["id"] for c in (await client.get("/api/v1/capabilities")).json()["capabilities"]}
