"""Generic Klipper registry selection, user configuration and exclusive plugin binding."""
from tests.printer_ips import ip_config
import pytest

from app.services import model_targets


async def models(client, plugin_id="moonraker") -> dict[tuple[str, str], dict]:
    rows = (await client.get("/api/v1/printer-models", params={"plugin_id": plugin_id})).json()
    return {(m["manufacturer_id"], m["model_id"]): m for m in rows}


async def add(client, model_uuid, **over):
    body = {"name": "K", "model_uuid": model_uuid, "connection_config": {"ip_address": "192.168.1.50", "port": 7125}, **over}
    return await client.post("/api/v1/printers", json=body)


async def test_the_plugin_is_manufacturer_neutral_and_offers_only_custom_klipper(client):
    reg = await models(client)
    assert set(reg) == {("generic", "custom_klipper")}
    r = await add(client, reg[("generic", "custom_klipper")]["id"])
    assert r.status_code == 201, r.text
    assert (r.json()["plugin_id"], r.json()["manufacturer_id"], r.json()["model_id"]) == ("moonraker", "generic", "custom_klipper")


async def test_the_types_catalog_offers_the_moonraker_form_per_model_with_a_custom_flag(client):
    types = {(t["manufacturer_id"], t["model_id"]): t for t in (await client.get("/api/v1/printers/types")).json() if t["plugin_id"] == "moonraker"}

    custom = types[("generic", "custom_klipper")]
    assert custom["custom"] is True
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


async def test_a_model_defaults_to_its_own_bed_when_the_user_gives_none(client):
    reg = await models(client)
    custom = await add(client, reg[("generic", "custom_klipper")]["id"])
    assert (custom.json()["bed_x_mm"], custom.json()["bed_y_mm"]) == (250.0, 250.0)        # the custom model's placeholder


@pytest.mark.parametrize("bad", [0, -5])
async def test_a_user_stated_bed_must_be_positive_on_create_and_edit(client, bad):
    custom = (await models(client))[("generic", "custom_klipper")]["id"]
    assert (await add(client, custom, bed_x_mm=bad)).status_code == 422
    assert (await add(client, custom, bed_y_mm=bad)).status_code == 422
    ok = (await add(client, custom, bed_x_mm=300, bed_y_mm=300)).json()
    assert (await client.patch(f"/api/v1/printers/{ok['id']}", json={"bed_x_mm": bad})).status_code == 422
    assert (await client.get(f"/api/v1/printers/{ok['id']}")).json()["bed_x_mm"] == 300


async def test_the_stored_toolhead_count_reaches_the_client_the_factory_builds(client):
    from app.models import Printer
    from app.services.printer_client_factory import create_client
    custom = (await models(client))[("generic", "custom_klipper")]["id"]
    created = (await add(client, custom, connection_config={"ip_address": "192.168.1.70", "toolheads": "3", "api_key": "k"})).json()

    built = create_client(Printer(plugin_id=created["plugin_id"], printer_type=created["printer_type"],
                                  connection_config=created["connection_config"]))

    assert [e for e in built._extruders] == ["extruder", "extruder1", "extruder2"] and built._api_key == "k"


async def test_the_legacy_printer_type_key_does_not_silently_pick_the_moonraker_provider(client):
    r = await client.post("/api/v1/printers", json={"name": "K", "printer_type": "moonraker", "connection_config": ip_config()})
    assert r.status_code == 422


async def test_a_printer_cannot_be_rebound_to_another_plugin(client):
    reg = await models(client)
    created = (await add(client, reg[("generic", "custom_klipper")]["id"])).json()
    refused = await client.patch(f"/api/v1/printers/{created['id']}", json={"plugin_id": "snapmaker"})
    assert refused.status_code == 409
    after = (await client.get(f"/api/v1/printers/{created['id']}")).json()
    assert (after["plugin_id"], after["model_uuid"]) == ("moonraker", created["model_uuid"])


def test_klipper_prints_raw_gcode_and_not_the_sliced_archive():
    assert model_targets.accepts_file("moonraker", "part.gcode") is True
    assert model_targets.accepts_file("moonraker", "part.gcode.3mf") is False
    assert model_targets.accepts_file("moonraker", "model.3mf") is True
