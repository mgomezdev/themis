"""The provider-neutral library API (BIZ-233): create/update/archive materials and spools, change weight and location, all
capability-gated (409 for providers that do not own their library, e.g. Spoolman) and never provider-specific."""
import pytest

from app.plugins.capabilities.filament_inventory import MANAGE_MATERIALS, MANAGE_SPOOLS, TRACKS_WEIGHT, WRITE_WEIGHT
from tests.api.test_inventory_api import _client_with
from tests.fake_providers import FakeInventoryProvider, FakeLibraryProvider
from tests.inventory_helpers import use_provider

BASE = "/api/v1/inventory"


@pytest.fixture
async def lib(client):
    fake = FakeLibraryProvider()
    await use_provider(fake, plugin_id="library_plugin")
    return fake


async def test_the_whole_library_lifecycle_through_the_neutral_api(client, lib):
    material = await client.post(f"{BASE}/materials", json={"name": "PLA Red", "material": "PLA", "color_hex": "ff0000", "vendor": "Acme", "diameter": 1.75})
    assert material.status_code == 201
    m = material.json()
    assert (m["name"], m["material"], m["color_hex"], m["vendor"], m["archived"]) == ("PLA Red", "PLA", "#FF0000", "Acme", False)   # normalised
    assert "raw" not in m

    spool = await client.post(f"{BASE}/spools", json={"material_ref": m["ref"], "location": "Shelf A", "initial_g": 1000})
    assert spool.status_code == 201
    s = spool.json()
    assert (s["material_ref"], s["location"], s["remaining_g"], s["archived"]) == (m["ref"], "Shelf A", 1000.0, False)
    assert s["material"]["name"] == "PLA Red" and s["label"]

    assert (await client.put(f"{BASE}/spools/{s['ref']}/remaining", json={"remaining_g": 412.5})).json()["remaining_g"] == 412.5
    moved = await client.patch(f"{BASE}/spools/{s['ref']}", json={"location": "Drawer 3", "label": "Red #1"})
    assert (moved.json()["location"], moved.json()["label"]) == ("Drawer 3", "Red #1")
    renamed = await client.patch(f"{BASE}/materials/{m['ref']}", json={"name": "PLA Crimson", "vendor": None})
    assert (renamed.json()["name"], renamed.json()["vendor"], renamed.json()["material"]) == ("PLA Crimson", None, "PLA")

    # the normal read API reflects all of it
    listed = {i["ref"]: i for i in (await client.get(f"{BASE}/spools")).json()["items"]}
    assert (listed[s["ref"]]["remaining_g"], listed[s["ref"]]["location"]) == (412.5, "Drawer 3")
    assert {i["ref"]: i["name"] for i in (await client.get(f"{BASE}/materials")).json()["items"]}[m["ref"]] == "PLA Crimson"

    # archive hides by default, include_archived shows, restore brings it back
    assert (await client.post(f"{BASE}/spools/{s['ref']}/archive")).json()["archived"] is True
    assert s["ref"] not in {i["ref"] for i in (await client.get(f"{BASE}/spools")).json()["items"]}
    assert s["ref"] in {i["ref"] for i in (await client.get(f"{BASE}/spools", params={"include_archived": True})).json()["items"]}
    assert (await client.post(f"{BASE}/materials/{m['ref']}/archive", json={"archived": True})).json()["archived"] is True
    assert m["ref"] not in {i["ref"] for i in (await client.get(f"{BASE}/materials")).json()["items"]}
    assert (await client.post(f"{BASE}/materials/{m['ref']}/archive", json={"archived": False})).json()["archived"] is False
    assert m["ref"] in {i["ref"] for i in (await client.get(f"{BASE}/materials")).json()["items"]}


@pytest.mark.parametrize("method, path, body", [
    ("POST", "/materials", {"name": "x"}), ("PATCH", "/materials/1", {"name": "x"}), ("POST", "/materials/1/archive", None),
])
async def test_material_management_needs_manage_materials(client, method, path, body):
    await use_provider(FakeInventoryProvider())                               # reads and weights, but no library
    resp = await client.request(method, BASE + path, json=body)
    assert resp.status_code == 409
    assert resp.json() == {"error": "capability_unavailable", "capability": "inventory.filament", "feature": MANAGE_MATERIALS}


@pytest.mark.parametrize("method, path, body", [
    ("POST", "/spools", {"material_ref": "1"}), ("PATCH", "/spools/1", {"location": "x"}), ("POST", "/spools/1/archive", None),
])
async def test_spool_management_needs_manage_spools(client, method, path, body):
    await use_provider(FakeInventoryProvider())
    resp = await client.request(method, BASE + path, json=body)
    assert resp.status_code == 409 and resp.json()["capability"] == MANAGE_SPOOLS


async def test_setting_the_weight_needs_write_weight_and_a_library_can_lack_it(client):
    await use_provider(FakeLibraryProvider(capabilities=frozenset({TRACKS_WEIGHT, MANAGE_SPOOLS, MANAGE_MATERIALS})))
    resp = await client.put(f"{BASE}/spools/1/remaining", json={"remaining_g": 5})
    assert resp.status_code == 409 and resp.json()["capability"] == WRITE_WEIGHT


async def test_spoolman_stays_a_valid_provider_without_library_management(client, spoolman_upstream):
    from tests.inventory_helpers import enable_spoolman
    await enable_spoolman()
    assert (await client.get(f"{BASE}/materials")).status_code == 200                     # reads work
    resp = await client.post(f"{BASE}/materials", json={"name": "x"})
    assert resp.status_code == 409 and resp.json()["capability"] == MANAGE_MATERIALS       # nothing Local-specific, just a capability
    weight = await client.put(f"{BASE}/spools/1/remaining", json={"remaining_g": 640})      # Spoolman does support weights
    assert weight.status_code == 200 and weight.json()["remaining_g"] == 640.0


async def test_no_provider_at_all_is_a_409_too(client):
    assert (await client.post(f"{BASE}/materials", json={"name": "x"})).status_code == 409
    assert (await client.post(f"{BASE}/spools", json={"material_ref": "1"})).status_code == 409


# --- validation + provider statuses --------------------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    {}, {"name": ""}, {"name": "x", "color_hex": "red"}, {"name": "x", "density": 0}, {"name": "x", "diameter": -1},
    {"name": "x" * 201},
])
async def test_invalid_materials_are_422(client, lib, body):
    assert (await client.post(f"{BASE}/materials", json=body)).status_code == 422
    assert lib.materials == {}


@pytest.mark.parametrize("body", [{}, {"material_ref": ""}, {"material_ref": "1", "initial_g": -1}, {"material_ref": "1", "remaining_g": 1e9},
                                  {"material_ref": "1", "initial_g": 100, "remaining_g": 200}])
async def test_invalid_spools_are_422_and_create_nothing(client, lib, body):
    before = set(lib.spools)
    assert (await client.post(f"{BASE}/spools", json=body)).status_code == 422
    assert set(lib.spools) == before


async def test_empty_patches_and_nulling_a_name_are_422_and_change_nothing(client, lib):
    m = (await client.post(f"{BASE}/materials", json={"name": "PLA"})).json()
    assert (await client.patch(f"{BASE}/materials/{m['ref']}", json={})).status_code == 422
    assert (await client.patch(f"{BASE}/materials/{m['ref']}", json={"name": None})).status_code == 422
    assert lib.materials[m["ref"]].name == "PLA"
    s = (await client.post(f"{BASE}/spools", json={"material_ref": m["ref"]})).json()
    assert (await client.patch(f"{BASE}/spools/{s['ref']}", json={})).status_code == 422
    assert (await client.put(f"{BASE}/spools/{s['ref']}/remaining", json={"remaining_g": -1})).status_code == 422


async def test_the_providers_not_found_status_passes_through(client, lib):
    assert (await client.patch(f"{BASE}/materials/999", json={"name": "x"})).status_code == 404
    assert (await client.post(f"{BASE}/materials/999/archive")).status_code == 404
    assert (await client.post(f"{BASE}/spools", json={"material_ref": "999"})).status_code == 404
    assert (await client.patch(f"{BASE}/spools/999", json={"location": "x"})).status_code == 404
    assert (await client.post(f"{BASE}/spools/999/archive")).status_code == 404
    assert (await client.put(f"{BASE}/spools/999/remaining", json={"remaining_g": 1})).status_code == 404


@pytest.mark.parametrize("method, path, body", [
    ("POST", "/materials", {"name": "x"}), ("PATCH", "/materials/1", {"name": "x"}), ("POST", "/materials/1/archive", None),
    ("POST", "/spools", {"material_ref": "1"}), ("PATCH", "/spools/1", {"location": "x"}), ("POST", "/spools/1/archive", None),
    ("PUT", "/spools/1/remaining", {"remaining_g": 1}),
])
async def test_a_failing_provider_is_a_503_never_a_500_on_every_write_path(client, lib, method, path, body):
    lib.fail_with = RuntimeError("provider exploded")
    resp = await client.request(method, BASE + path, json=body)
    assert resp.status_code == 503 and "provider exploded" in resp.json()["detail"]


async def test_provider_statuses_pass_through_only_for_404_409_422_everything_else_is_503(client, lib):
    from app.plugins.capabilities.filament_inventory import InventoryProviderError
    for status, expected in [(422, 422), (404, 404), (409, 409), (401, 503), (403, 503), (500, 503), (502, 503)]:
        lib.fail_with = InventoryProviderError(f"upstream said {status}", code=str(status), status=status)
        resp = await client.post(f"{BASE}/materials", json={"name": "x"})
        assert resp.status_code == expected, status
        assert "upstream said" in resp.json()["detail"]
    lib.fail_with = InventoryProviderError("conflict", code="409", status=409)
    body = (await client.post(f"{BASE}/materials", json={"name": "x"})).json()
    assert "error" not in body                                    # a provider 409 is distinguishable from capability_unavailable


async def test_a_provider_that_advertises_a_capability_but_refuses_the_call_is_a_409_capability_error(client, lib):
    from app.plugins.capabilities.filament_inventory import NotSupported
    lib.fail_with = NotSupported(MANAGE_MATERIALS)
    resp = await client.post(f"{BASE}/materials", json={"name": "x"})
    assert resp.status_code == 409 and resp.json() == {"error": "capability_unavailable", "kind": "filament_inventory",
                                                       "capability": MANAGE_MATERIALS}


async def test_setting_the_weight_survives_a_failed_read_back(client, lib):
    m = (await client.post(f"{BASE}/materials", json={"name": "PLA"})).json()
    s = (await client.post(f"{BASE}/spools", json={"material_ref": m["ref"], "initial_g": 900})).json()

    async def broken_read(ref):
        raise RuntimeError("read failed")
    lib.get_spool = broken_read
    resp = await client.put(f"{BASE}/spools/{s['ref']}/remaining", json={"remaining_g": 333})

    assert resp.status_code == 200 and resp.json()["remaining_g"] == 333.0       # the write happened; we answer with what we wrote
    assert lib.spools[s["ref"]].remaining_g == 333.0


async def test_input_is_normalised_before_it_reaches_the_provider(client, lib):
    blank = await client.post(f"{BASE}/materials", json={"name": "   "})
    assert blank.status_code == 422 and lib.materials == {}
    m = (await client.post(f"{BASE}/materials", json={"name": "  PETG  ", "vendor": "   ", "material": ""})).json()
    assert (m["name"], m["vendor"], m["material"]) == ("PETG", None, None)       # stripped; empty means "not set"
    s = (await client.post(f"{BASE}/spools", json={"material_ref": m["ref"], "location": "  "})).json()
    assert s["location"] is None
    assert (await client.patch(f"{BASE}/spools/{s['ref']}", json={"label": "  "})).status_code == 422    # a label cannot be cleared
    assert (await client.patch(f"{BASE}/spools/{s['ref']}", json={"label": None})).status_code == 422
    assert (await client.patch(f"{BASE}/spools/{s['ref']}", json={"location": "Shelf"})).json()["location"] == "Shelf"
    assert (await client.patch(f"{BASE}/spools/{s['ref']}", json={"location": None})).json()["location"] is None   # null clears


async def test_lists_are_in_natural_ref_order_whatever_the_provider_returns(client, lib):
    from tests.inventory_helpers import spool
    lib.spools = {r: spool(r, 1.0, name=f"s{r}") for r in ("10", "2", "33", "9")}
    refs = [i["ref"] for i in (await client.get(f"{BASE}/spools")).json()["items"]]
    assert refs == ["2", "9", "10", "33"]


async def test_archived_items_are_hidden_by_default_but_stay_editable_and_weighable(client, lib):
    m = (await client.post(f"{BASE}/materials", json={"name": "PLA"})).json()
    s = (await client.post(f"{BASE}/spools", json={"material_ref": m["ref"], "initial_g": 500})).json()
    await client.post(f"{BASE}/spools/{s['ref']}/archive")
    assert (await client.patch(f"{BASE}/spools/{s['ref']}", json={"location": "Box"})).status_code == 200
    assert (await client.put(f"{BASE}/spools/{s['ref']}/remaining", json={"remaining_g": 50})).status_code == 200
    await client.post(f"{BASE}/materials/{m['ref']}/archive")
    assert (await client.post(f"{BASE}/spools", json={"material_ref": m["ref"]})).status_code == 422      # no spool on an archived material
    assert s["ref"] not in {i["ref"] for i in (await client.get(f"{BASE}/spools")).json()["items"]}


async def test_spools_expose_their_initial_weight(client, lib):
    m = (await client.post(f"{BASE}/materials", json={"name": "PLA"})).json()
    s = (await client.post(f"{BASE}/spools", json={"material_ref": m["ref"], "initial_g": 750})).json()
    assert (s["initial_g"], s["remaining_g"]) == (750.0, 750.0)


async def test_library_writes_need_the_inventory_write_scope(client, session_factory, lib):
    reader = await _client_with(session_factory, ["inventory:read"])
    async with reader:
        assert (await reader.get(f"{BASE}/materials")).status_code == 200
        for method, path, body in [("POST", "/materials", {"name": "x"}), ("PATCH", "/materials/1", {"name": "x"}),
                                   ("POST", "/materials/1/archive", None), ("POST", "/spools", {"material_ref": "1"}),
                                   ("PATCH", "/spools/1", {"location": "x"}), ("POST", "/spools/1/archive", None),
                                   ("PUT", "/spools/1/remaining", {"remaining_g": 1})]:
            assert (await reader.request(method, BASE + path, json=body)).status_code == 403, (method, path)
