"""GET /printers/types lists every registered plugin's declared models; POST/PATCH /printers store and validate the
plugin_id / manufacturer_id / model_id identity, with legacy printer_type mapped onto it (BIZ-262)."""
import pytest
from sqlalchemy import text

from app import plugins
from app.plugins.manifest import Manufacturer, PrinterModel
from tests.plugins.dummy_plugin import make_manifest

FAKE = "fake_printer"
TYPE_KEYS = {"plugin_id", "manufacturer_id", "manufacturer_name", "model_id", "display_name",
             "bed_mm", "toolheads", "connection_fields", "plugin_enabled"}


@pytest.fixture(autouse=True)
def _fake_printer_plugin():
    saved = dict(plugins._REGISTRY)
    plugins.register_plugin(make_manifest(FAKE, default_enabled=True, manufacturers=(
        Manufacturer("zeta", "Zeta Works", (PrinterModel("z9", "Z9", bed_mm=(300, 200), toolheads=2),)),
        Manufacturer("acme", "Acme 3D", (PrinterModel("a2", "Acme A2"),
                                         PrinterModel("a1", "Acme A1", bed_mm=(220, 220)))),
    )))
    yield
    plugins._REGISTRY.clear()
    plugins._REGISTRY.update(saved)


async def _post(client, **body):
    base = {"name": "Shop printer", "connection_config": {}, "orca_printer_profiles": [],
            "current_orca_printer_profile": None}
    base.update(body)
    return await client.post("/api/v1/printers", json=base)


async def _identity(session_factory, printer_id: int) -> tuple:
    async with session_factory() as s:
        row = (await s.execute(text("SELECT plugin_id, manufacturer_id, model_id FROM printers WHERE id = :i"),
                               {"i": printer_id})).one()
    return tuple(row)


async def _printer_count(session_factory) -> int:
    async with session_factory() as s:
        return (await s.execute(text("SELECT COUNT(*) FROM printers"))).scalar()


# --- GET /printers/types -------------------------------------------------------------------------------------

async def test_types_lists_each_declared_model_with_the_contract_keys(client):
    resp = await client.get("/api/v1/printers/types")
    assert resp.status_code == 200
    entries = [e for e in resp.json() if e["plugin_id"] == FAKE]
    assert sorted(e["model_id"] for e in entries) == ["a1", "a2", "z9"]
    for e in entries:
        assert set(e) == TYPE_KEYS
        assert isinstance(e["connection_fields"], list)

    a1 = next(e for e in entries if e["model_id"] == "a1")
    assert (a1["manufacturer_id"], a1["manufacturer_name"], a1["display_name"]) == ("acme", "Acme 3D", "Acme A1")
    assert (a1["bed_mm"], a1["toolheads"]) == ([220, 220], 1)
    z9 = next(e for e in entries if e["model_id"] == "z9")
    assert (z9["manufacturer_name"], z9["bed_mm"], z9["toolheads"]) == ("Zeta Works", [300, 200], 2)


async def test_types_entries_are_sorted_by_manufacturer_name_then_display_name(client):
    resp = await client.get("/api/v1/printers/types")
    keys = [(e["manufacturer_name"], e["display_name"]) for e in resp.json()]
    assert len(keys) > 3 and keys == sorted(keys)


async def test_types_plugin_enabled_follows_the_plugin_config_flag(client):
    assert (await client.put(f"/api/v1/plugins/{FAKE}", json={"enabled": True})).status_code == 200
    on = [e for e in (await client.get("/api/v1/printers/types")).json() if e["plugin_id"] == FAKE]
    assert on and all(e["plugin_enabled"] is True for e in on)

    assert (await client.put(f"/api/v1/plugins/{FAKE}", json={"enabled": False})).status_code == 200
    off = [e for e in (await client.get("/api/v1/printers/types")).json() if e["plugin_id"] == FAKE]
    assert len(off) == 3 and all(e["plugin_enabled"] is False for e in off)   # disabled plugins are still listed


# --- POST /printers ------------------------------------------------------------------------------------------

async def test_create_stores_and_echoes_the_plugin_identity(client, session_factory):
    resp = await _post(client, plugin_id=FAKE, manufacturer_id="acme", model_id="a1")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["plugin_id"], body["manufacturer_id"], body["model_id"]) == (FAKE, "acme", "a1")
    assert await _identity(session_factory, body["id"]) == (FAKE, "acme", "a1")


async def test_create_with_an_unknown_plugin_is_rejected_and_stores_nothing(client, session_factory):
    resp = await _post(client, plugin_id="nope_nope", manufacturer_id="acme", model_id="a1")
    assert 400 <= resp.status_code < 500
    assert await _printer_count(session_factory) == 0


async def test_create_with_a_model_the_plugin_does_not_declare_is_rejected(client, session_factory):
    wrong_manufacturer = await _post(client, plugin_id=FAKE, manufacturer_id="acme", model_id="z9")
    unknown_model = await _post(client, plugin_id=FAKE, manufacturer_id="acme", model_id="nope")
    assert 400 <= wrong_manufacturer.status_code < 500
    assert 400 <= unknown_model.status_code < 500
    assert await _printer_count(session_factory) == 0


@pytest.mark.parametrize("legacy,expected", [
    ("bambu", ("bambu", "bambu", "p1s")),
    ("elegoo_centauri", ("elegoo_centauri", "elegoo", "centauri")),
    ("snapmaker_extended", ("snapmaker", "snapmaker", "u1_extended")),
    ("mock", ("mock", "mock", "mock")),
])
async def test_legacy_printer_type_only_is_mapped_to_the_identity(client, session_factory, legacy, expected):
    resp = await _post(client, printer_type=legacy)
    assert resp.status_code == 201, resp.text
    assert await _identity(session_factory, resp.json()["id"]) == expected
    assert resp.json()["plugin_id"] == expected[0]


async def test_unknown_legacy_printer_type_is_rejected_and_stores_nothing(client, session_factory):
    resp = await _post(client, printer_type="not_a_printer")
    assert 400 <= resp.status_code < 500
    assert await _printer_count(session_factory) == 0


# --- default bed dimensions ----------------------------------------------------------------------------------

@pytest.mark.parametrize("manufacturer_id,model_id,bed", [("acme", "a1", (220, 220)), ("zeta", "z9", (300, 200))])
async def test_bed_dimensions_default_to_the_declared_model_when_omitted(client, session_factory, manufacturer_id, model_id, bed):
    resp = await _post(client, plugin_id=FAKE, manufacturer_id=manufacturer_id, model_id=model_id)
    assert resp.status_code == 201, resp.text
    async with session_factory() as s:
        x, y = (await s.execute(text("SELECT bed_x_mm, bed_y_mm FROM printers WHERE id = :i"),
                                {"i": resp.json()["id"]})).one()
    assert (x, y) == bed


# --- PATCH /printers/{id} ------------------------------------------------------------------------------------

async def test_patch_validates_the_identity_against_the_plugin(client, session_factory):
    pid = (await _post(client, plugin_id=FAKE, manufacturer_id="acme", model_id="a1")).json()["id"]

    bad = await client.patch(f"/api/v1/printers/{pid}", json={"model_id": "z9"})
    assert 400 <= bad.status_code < 500
    assert await _identity(session_factory, pid) == (FAKE, "acme", "a1")     # unchanged on rejection

    good = await client.patch(f"/api/v1/printers/{pid}", json={"manufacturer_id": "acme", "model_id": "a2"})
    assert good.status_code == 200, good.text
    assert await _identity(session_factory, pid) == (FAKE, "acme", "a2")
