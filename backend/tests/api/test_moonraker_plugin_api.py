"""The Moonraker protocol-family plugin through the API (BIZ-148): several manufacturers on one plugin, registry UUID selection, the
generic custom Klipper model with user-owned dimensions, and a hardware model offered by two providers only by explicit choice."""
import pytest

from app import plugins
from app.plugins.manifest import Manufacturer, PrinterModel
from app.services import model_targets
from tests.plugins.dummy_plugin import make_manifest


async def models(client, plugin_id="moonraker") -> dict[tuple[str, str], dict]:
    rows = (await client.get("/api/v1/printer-models", params={"plugin_id": plugin_id})).json()
    return {(m["manufacturer_id"], m["model_id"]): m for m in rows}


async def add(client, model_uuid, **over):
    body = {"name": "K", "model_uuid": model_uuid, "connection_config": {"ip_address": "192.168.1.50", "port": 7125}, **over}
    return await client.post("/api/v1/printers", json=body)


async def test_the_plugin_serves_several_manufacturers_and_a_generic_model_by_registry_uuid(client):
    reg = await models(client)

    assert {k[0] for k in reg} == {"voron", "sovol", "generic"}
    voron, sovol = await add(client, reg[("voron", "v2_4_300")]["id"]), await add(client, reg[("sovol", "sv08")]["id"])
    assert voron.status_code == 201 and sovol.status_code == 201, (voron.text, sovol.text)
    for r, key in ((voron, ("voron", "v2_4_300")), (sovol, ("sovol", "sv08"))):
        p = r.json()
        assert (p["plugin_id"], p["manufacturer_id"], p["model_id"], p["model_uuid"]) == ("moonraker", *key, reg[key]["id"])
        assert p["printer_type"] == "moonraker"
    assert (voron.json()["bed_x_mm"], sovol.json()["bed_x_mm"]) == (300.0, 350.0)             # the declared bed of each


async def test_the_types_catalog_offers_the_moonraker_form_per_model_with_a_custom_flag(client):
    types = {(t["manufacturer_id"], t["model_id"]): t for t in (await client.get("/api/v1/printers/types")).json() if t["plugin_id"] == "moonraker"}

    custom = types[("generic", "custom_klipper")]
    assert custom["custom"] is True and types[("voron", "v2_4_300")]["custom"] is False
    names = [f["name"] for f in custom["connection_fields"]]
    assert names == ["ip_address", "port", "api_key", "toolheads"]
    assert next(f for f in custom["connection_fields"] if f["name"] == "api_key")["field_type"] == "password"


async def test_a_custom_klipper_printer_persists_the_dimensions_and_toolheads_the_user_stated(client):
    custom = (await models(client))[("generic", "custom_klipper")]

    r = await add(client, custom["id"], bed_x_mm=410.0, bed_y_mm=205.5,
                  connection_config={"ip_address": "192.168.1.60", "toolheads": 2})

    assert r.status_code == 201, r.text
    got = (await client.get(f"/api/v1/printers/{r.json()['id']}")).json()
    assert (got["bed_x_mm"], got["bed_y_mm"]) == (410.0, 205.5)                  # user-owned, not the model's placeholder 250
    assert got["connection_config"]["toolheads"] == 2
    patched = await client.patch(f"/api/v1/printers/{got['id']}", json={"name": "Renamed"})
    assert (patched.json()["bed_x_mm"], patched.json()["connection_config"]["toolheads"]) == (410.0, 2)     # survives an unrelated edit


async def test_a_declared_model_defaults_to_its_own_bed_when_the_user_gives_none(client):
    r = await add(client, (await models(client))[("generic", "custom_klipper")]["id"])
    assert (r.json()["bed_x_mm"], r.json()["bed_y_mm"]) == (250.0, 250.0)


async def test_the_legacy_printer_type_key_does_not_silently_pick_the_moonraker_provider(client):
    r = await client.post("/api/v1/printers", json={"name": "K", "printer_type": "moonraker", "connection_config": {}})
    assert r.status_code == 422


@pytest.fixture
def second_provider():
    saved = dict(plugins._REGISTRY)
    plugins.register_plugin(make_manifest("klipper_alt", default_enabled=True, manufacturers=(
        Manufacturer("voron", "Voron Design", (PrinterModel("v2_4_300", "2.4 (300 mm)", bed_mm=(300, 300)),)),)))
    yield
    plugins._REGISTRY.clear()
    plugins._REGISTRY.update(saved)


async def test_the_same_hardware_model_under_two_providers_is_two_registry_identities_chosen_explicitly(client, second_provider):
    a, b = await models(client, "moonraker"), await models(client, "klipper_alt")
    assert a[("voron", "v2_4_300")]["id"] != b[("voron", "v2_4_300")]["id"]

    via_moonraker = await add(client, a[("voron", "v2_4_300")]["id"])
    via_alt = await add(client, b[("voron", "v2_4_300")]["id"])
    by_triple = await client.post("/api/v1/printers", json={"name": "T", "plugin_id": "klipper_alt", "manufacturer_id": "voron",
                                                            "model_id": "v2_4_300", "connection_config": {}})

    assert via_moonraker.json()["plugin_id"] == "moonraker" and via_alt.json()["plugin_id"] == "klipper_alt"
    assert by_triple.json()["model_uuid"] == b[("voron", "v2_4_300")]["id"]


async def test_a_printer_is_never_silently_rebound_to_another_provider(client, second_provider):
    a = await models(client, "moonraker")
    created = (await add(client, a[("voron", "v2_4_300")]["id"])).json()

    # the plugin is not editable, and a model the bound plugin does not declare cannot be moved to
    assert (await client.patch(f"/api/v1/printers/{created['id']}", json={"plugin_id": "klipper_alt"})).json()["plugin_id"] == "moonraker"
    refused = await client.patch(f"/api/v1/printers/{created['id']}", json={"manufacturer_id": "voron", "model_id": "trident_300x"})
    assert refused.status_code == 422
    after = (await client.get(f"/api/v1/printers/{created['id']}")).json()
    assert (after["plugin_id"], after["model_uuid"]) == ("moonraker", a[("voron", "v2_4_300")]["id"])
    moved = await client.patch(f"/api/v1/printers/{created['id']}", json={"model_id": "trident_300"})       # within the SAME provider: fine
    assert moved.json()["model_uuid"] == a[("voron", "trident_300")]["id"] and moved.json()["plugin_id"] == "moonraker"


def test_klipper_prints_raw_gcode_and_not_the_sliced_archive():
    assert model_targets.accepts_file("moonraker", "part.gcode") is True
    assert model_targets.accepts_file("moonraker", "part.gcode.3mf") is False
    assert model_targets.accepts_file("moonraker", "model.3mf") is True
