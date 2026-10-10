"""/printer-models + model identity on printers (BIZ-262)."""
import pytest

from app import plugins
from app.plugins.manifest import Manufacturer, PrinterModel
from tests.plugins.dummy_plugin import make_manifest


@pytest.fixture
def two_vendor_plugin():
    saved = dict(plugins._REGISTRY)
    plugins.register_plugin(make_manifest("fake_printers", default_enabled=True, manufacturers=(
        Manufacturer("acme", "Acme", (PrinterModel("x1", "X1"), PrinterModel("x1_pro", "X1 Pro"))),
        Manufacturer("globex", "Globex", (PrinterModel("g1", "G1"),)))))
    yield
    plugins._REGISTRY.clear()
    plugins._REGISTRY.update(saved)


async def models(client, **params):
    r = await client.get("/api/v1/printer-models", params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def test_list_exposes_stable_uuids_for_every_declared_model(client, two_vendor_plugin):
    a = await models(client, plugin_id="fake_printers")
    b = await models(client, plugin_id="fake_printers")

    assert sorted(m["model_id"] for m in a) == ["g1", "x1", "x1_pro"]
    assert {m["id"] for m in a} == {m["id"] for m in b} and len({m["id"] for m in a}) == 3
    assert all(m["enabled"] and not m["dormant"] for m in a)


async def test_disabling_a_model_hides_it_from_the_enabled_filter_and_types_catalog(client, two_vendor_plugin):
    pro = next(m for m in await models(client, plugin_id="fake_printers") if m["model_id"] == "x1_pro")

    r = await client.patch(f"/api/v1/printer-models/{pro['id']}", json={"enabled": False})

    assert r.status_code == 200 and r.json()["enabled"] is False
    assert "x1_pro" not in [m["model_id"] for m in await models(client, plugin_id="fake_printers", enabled="true")]
    types = (await client.get("/api/v1/printers/types")).json()
    entry = next(t for t in types if t["model_id"] == "x1_pro")
    assert (entry["model_uuid"], entry["model_enabled"]) == (pro["id"], False)


async def test_patching_an_unknown_model_is_404(client):
    assert (await client.patch("/api/v1/printer-models/nope", json={"enabled": False})).status_code == 404


async def test_a_printer_created_from_the_triple_records_the_models_uuid(client, two_vendor_plugin):
    g1 = next(m for m in await models(client, plugin_id="fake_printers") if m["model_id"] == "g1")

    r = await client.post("/api/v1/printers", json={"name": "G", "plugin_id": "fake_printers", "manufacturer_id": "globex",
                                                    "model_id": "g1", "connection_config": {}})

    assert r.status_code == 201, r.text
    assert r.json()["model_uuid"] == g1["id"]
    assert (await client.get(f"/api/v1/printers/{r.json()['id']}")).json()["model_uuid"] == g1["id"]


async def test_a_printer_can_be_created_from_the_model_uuid_alone(client, two_vendor_plugin):
    x1 = next(m for m in await models(client, plugin_id="fake_printers") if m["model_id"] == "x1")

    r = await client.post("/api/v1/printers", json={"name": "X", "model_uuid": x1["id"], "connection_config": {}})

    assert r.status_code == 201, r.text
    assert (r.json()["plugin_id"], r.json()["manufacturer_id"], r.json()["model_id"]) == ("fake_printers", "acme", "x1")
    assert r.json()["model_uuid"] == x1["id"]


async def test_creating_a_printer_from_a_disabled_model_is_refused(client, two_vendor_plugin):
    x1 = next(m for m in await models(client, plugin_id="fake_printers") if m["model_id"] == "x1")
    await client.patch(f"/api/v1/printer-models/{x1['id']}", json={"enabled": False})

    r = await client.post("/api/v1/printers", json={"name": "X", "model_uuid": x1["id"], "connection_config": {}})

    assert r.status_code == 422 and "disabled" in r.json()["detail"]


async def test_an_unknown_model_uuid_is_422(client):
    r = await client.post("/api/v1/printers", json={"name": "X", "model_uuid": "nope", "connection_config": {}})
    assert r.status_code == 422


async def test_legacy_printer_type_rows_get_the_registry_uuid_of_their_mapped_model(client, create_printer):
    pid = await create_printer(printer_type="bambu")

    printer = (await client.get(f"/api/v1/printers/{pid}")).json()
    bambu_p1s = next(m for m in await models(client, plugin_id="bambu") if m["model_id"] == "p1s")

    assert printer["model_uuid"] == bambu_p1s["id"]


async def test_removing_the_plugin_keeps_the_uuid_on_the_printer_and_marks_the_model_dormant(client, two_vendor_plugin):
    r = await client.post("/api/v1/printers", json={"name": "G", "plugin_id": "fake_printers", "manufacturer_id": "globex",
                                                    "model_id": "g1", "connection_config": {}})
    uuid_before = r.json()["model_uuid"]

    plugins._REGISTRY.pop("fake_printers")
    listed = {m["id"]: m for m in await models(client, plugin_id="fake_printers")}

    assert listed[uuid_before]["dormant_reason"] == "plugin_removed" and listed[uuid_before]["printer_count"] == 1
    assert (await client.get(f"/api/v1/printers/{r.json()['id']}")).json()["model_uuid"] == uuid_before
    assert await models(client, plugin_id="fake_printers", usable="true") == []


async def test_changing_a_printers_model_moves_its_model_uuid_with_it(client, two_vendor_plugin):
    by_model = {m["model_id"]: m["id"] for m in await models(client, plugin_id="fake_printers")}
    created = (await client.post("/api/v1/printers", json={"name": "X", "model_uuid": by_model["x1"], "connection_config": {}})).json()

    r = await client.patch(f"/api/v1/printers/{created['id']}", json={"model_id": "x1_pro"})

    assert r.status_code == 200, r.text
    assert r.json()["model_uuid"] == by_model["x1_pro"]
    assert (await client.get(f"/api/v1/printers/{created['id']}")).json()["model_uuid"] == by_model["x1_pro"]


async def test_a_rejected_model_change_leaves_the_uuid_untouched(client, two_vendor_plugin):
    by_model = {m["model_id"]: m["id"] for m in await models(client, plugin_id="fake_printers")}
    created = (await client.post("/api/v1/printers", json={"name": "X", "model_uuid": by_model["x1"], "connection_config": {}})).json()

    r = await client.patch(f"/api/v1/printers/{created['id']}", json={"model_id": "nope"})

    assert r.status_code == 422
    assert (await client.get(f"/api/v1/printers/{created['id']}")).json()["model_uuid"] == by_model["x1"]


async def test_types_entries_for_every_declared_model_carry_a_registry_uuid(client, two_vendor_plugin):
    types = [t for t in (await client.get("/api/v1/printers/types")).json() if t["plugin_id"] == "fake_printers"]
    reg = {m["model_id"]: m["id"] for m in await models(client, plugin_id="fake_printers")}

    assert {t["model_id"]: t["model_uuid"] for t in types} == reg
    assert all(t["model_enabled"] is True for t in types)


async def test_model_registry_writes_need_the_printers_write_scope(client, session_factory, two_vendor_plugin):
    from app.models import ApiKey
    from app.services.api_key_service import generate_key, hash_key

    raw, prefix = generate_key()
    async with session_factory() as s:
        s.add(ApiKey(name="ro", key_prefix=prefix, key_hash=hash_key(raw), scopes=["printers:read"], enabled=True,
                     created_at="2026-01-01T00:00:00"))
        await s.commit()
    m = (await models(client, plugin_id="fake_printers"))[0]

    ro = {"X-Api-Key": raw}
    assert (await client.get("/api/v1/printer-models", headers=ro)).status_code == 200
    assert (await client.patch(f"/api/v1/printer-models/{m['id']}", json={"enabled": False}, headers=ro)).status_code == 403
