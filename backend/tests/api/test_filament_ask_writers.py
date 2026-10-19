"""One test per writer of the filament ask tuple (BIZ-314): jobs, model targets, project items, order parts, and the job-list chips.
Undefined is stored as NULL, never the string "any"; `any` is a real subtype value; a type needs a subtype."""
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import JobModelTarget, JobPrinterConfig, ProjectItem

TUPLE = {"filament_manufacturer": "Acme", "filament_type": "PLA", "filament_subtype": "silk", "filament_color": "#ff0000"}


@pytest.fixture
async def project(client) -> int:
    resp = await client.post("/api/v1/projects", json={"name": "Assembly"})
    assert resp.status_code == 201
    return resp.json()["id"]


async def _configs(session_factory, job_id):
    async with session_factory() as s:
        return (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))).scalars().all()


def _cols(row):
    return (row.ask_manufacturer, row.ask_type, row.ask_subtype, row.ask_color)


async def test_a_job_config_stores_the_tuple_and_details_return_it(client, create_job, session_factory):
    job_id = await create_job(**TUPLE)
    (cfg,) = await _configs(session_factory, job_id)
    assert _cols(cfg) == ("Acme", "PLA", "silk", "#ff0000")
    (out,) = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()["printer_configs"]
    assert {k: out[k] for k in TUPLE} == TUPLE


async def test_blank_and_legacy_any_are_stored_as_null_not_the_string_any(client, create_job, session_factory):
    job_id = await create_job(filament_type="any", filament_color="ANY", filament_manufacturer="", filament_subtype="")
    (cfg,) = await _configs(session_factory, job_id)
    assert _cols(cfg) == (None, None, None, None)


async def test_subtype_any_is_kept_as_a_value(client, create_job, session_factory):
    (cfg,) = await _configs(session_factory, await create_job(filament_type="PETG", filament_subtype="any"))
    assert _cols(cfg) == (None, "PETG", "any", None)


async def test_a_type_without_a_subtype_is_refused_and_creates_no_job(client, upload_3mf, create_printer, session_factory):
    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post("/api/v1/jobs", json={"uploaded_file_id": await upload_3mf(), "plate_number": 1, "printer_configs": [
            {"printer_id": await create_printer(), "print_profile": "0.20mm", "filament_type": "PLA"}]})
    assert resp.status_code == 422 and "subtype" in resp.text
    assert (await client.get("/api/v1/jobs")).json() == []


async def test_editing_a_job_replaces_the_tuple(client, create_job, session_factory):
    job_id = await create_job(**TUPLE)
    (cfg,) = await _configs(session_factory, job_id)
    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.patch(f"/api/v1/jobs/{job_id}/configs", json={"printer_configs": [
            {"printer_id": cfg.printer_id, "print_profile": "0.20mm", "filament_type": "PETG", "filament_subtype": "standard"}]})
    assert resp.status_code == 200, resp.text
    (cfg,) = await _configs(session_factory, job_id)
    assert _cols(cfg) == (None, "PETG", "standard", None)                      # the old manufacturer / colour did not linger


async def test_a_model_target_stores_the_tuple_and_materializes_it_into_configs(client, upload_3mf, create_printer, model_uuid_of, session_factory):
    pid = await create_printer()
    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post("/api/v1/jobs", json={"uploaded_file_id": await upload_3mf(), "model_targets": [
            {"model_uuid": await model_uuid_of(pid), "print_profile": "0.20mm", **TUPLE}]})
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]
    async with session_factory() as s:
        target = (await s.execute(select(JobModelTarget).where(JobModelTarget.job_id == job_id))).scalar_one()
    assert _cols(target) == ("Acme", "PLA", "silk", "#ff0000")
    (cfg,) = await _configs(session_factory, job_id)
    assert _cols(cfg) == _cols(target)                                          # materialized onto the matching printer
    (out,) = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()["model_targets"]
    assert {k: out[k] for k in TUPLE} == TUPLE


async def test_a_catalog_color_in_a_filament_map_is_a_tuple_ask(client, create_job, session_factory):
    entry = {"model_filament": 1, "filament_type": "PLA", "filament_subtype": "plus", "filament_color": "#00ff00"}
    job_id = await create_job(filament_map=[entry, {"model_filament": 2, "tool_index": 1}])
    (cfg,) = await _configs(session_factory, job_id)
    assert cfg.filament_map[0]["filament_manufacturer"] is None
    assert {k: cfg.filament_map[0][k] for k in ("filament_type", "filament_subtype", "filament_color")} == {
        k: entry[k] for k in ("filament_type", "filament_subtype", "filament_color")}
    assert cfg.filament_map[1]["tool_index"] == 1


async def test_project_items_store_the_tuple_and_a_patch_changes_only_what_it_sends(client, project, upload_3mf, session_factory):
    f = await upload_3mf()
    created = (await client.post(f"/api/v1/projects/{project}/items", json={"file_id": f, **TUPLE})).json()
    assert {k: created[k] for k in TUPLE} == TUPLE

    patched = (await client.put(f"/api/v1/projects/{project}/items/{created['id']}", json={"filament_subtype": "plus"})).json()
    assert (patched["filament_manufacturer"], patched["filament_type"], patched["filament_subtype"], patched["filament_color"]) == (
        "Acme", "PLA", "plus", "#ff0000")

    cleared = (await client.put(f"/api/v1/projects/{project}/items/{created['id']}", json={"filament_type": None})).json()
    assert (cleared["filament_type"], cleared["filament_subtype"]) == (None, None)       # the subtype went with its type
    async with session_factory() as s:
        row = await s.get(ProjectItem, created["id"])
    assert _cols(row) == ("Acme", None, None, "#ff0000")


async def test_a_project_item_patch_that_would_leave_a_type_without_a_subtype_is_refused(client, project, upload_3mf, session_factory):
    f = await upload_3mf()
    item = (await client.post(f"/api/v1/projects/{project}/items", json={"file_id": f})).json()
    resp = await client.put(f"/api/v1/projects/{project}/items/{item['id']}", json={"filament_type": "PLA"})
    assert resp.status_code == 422 and "subtype" in resp.text
    assert (await client.get(f"/api/v1/projects/{project}/items")).json()[0]["filament_type"] is None     # nothing changed


async def test_order_parts_store_the_tuple(client):
    resp = await client.post("/api/v1/orders", json={"order_type": "internal", "customer": "c", "title": "t", "parts": [
        {"name": "bracket", "qty": 2, **TUPLE}, {"name": "plain", "filament_color": "any"}]})
    assert resp.status_code in (200, 201), resp.text
    parts = (await client.get(f"/api/v1/orders/{resp.json()['id']}")).json()["parts"]
    assert {k: parts[0][k] for k in TUPLE} == TUPLE
    assert (parts[1]["filament_type"], parts[1]["filament_color"]) == (None, None)


async def test_the_job_list_shows_type_chips_with_a_meaningful_subtype(client, create_job):
    await create_job(filament_type="PLA", filament_subtype="silk")
    await create_job(filament_type="PETG", filament_subtype="any")
    await create_job(filament_type="ABS", filament_subtype="standard")
    await create_job()
    chips = sorted(sum((j["materials"] for j in (await client.get("/api/v1/jobs")).json()), []))
    assert chips == ["ABS", "PETG", "PLA silk"]



# --- the job detail says why a printer does not match (BIZ-317) --------------------------------------------------------------------

PLA_RED = [{"slot": 0, "type": "PLA", "subtype": "standard", "color": "#FF0000", "manufacturer": "Acme"},
           {"slot": 1, "type": "PETG", "subtype": "standard", "color": "#00FF00"}]


async def _match(client, create_job, create_printer, loaded, **ask):
    pid = await create_printer(loaded_filaments=loaded)
    job_id = await create_job(printer_id=pid, **ask)
    (cfg,) = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()["printer_configs"]
    return cfg["filament_match"]


async def test_a_matching_printer_reports_the_slot_it_would_print_from(client, create_job, create_printer):
    m = await _match(client, create_job, create_printer, PLA_RED, filament_type="PETG", filament_subtype="any")
    assert m == {"eligible": True, "slots": [1], "reasons": []}


async def test_a_printer_that_does_not_match_lists_every_slot_and_what_was_wanted(client, create_job, create_printer):
    m = await _match(client, create_job, create_printer, PLA_RED, filament_type="PLA", filament_subtype="silk", filament_color="#FF0000")
    assert m == {"eligible": False, "slots": [], "reasons": [
        "T0: Acme PLA standard #FF0000; wanted PLA silk #FF0000", "T1: PETG standard #00FF00; wanted PLA silk #FF0000"]}


async def test_a_printer_with_no_slots_says_no_filament_configured(client, create_job, create_printer):
    m = await _match(client, create_job, create_printer, [], filament_type="PLA", filament_subtype="any")
    assert (m["eligible"], m["reasons"]) == (False, ["no filament configured"])


async def test_an_unmatched_job_stays_queued_and_the_details_still_load(client, create_job, create_printer):
    pid = await create_printer(loaded_filaments=PLA_RED)
    job_id = await create_job(printer_id=pid, filament_type="ABS", filament_subtype="any")
    assert (await client.get(f"/api/v1/jobs/{job_id}/details")).json()["status"] == "queued"


async def _mark_reported(session_factory, printer_id):
    """What the telemetry merge does (a client cannot): flag every slot as hardware-reported."""
    from app.models import Printer
    async with session_factory() as s:
        printer = await s.get(Printer, printer_id)
        printer.loaded_filaments = [{**slot, "reported": True} for slot in printer.loaded_filaments]
        await s.commit()


async def test_a_conflicting_slot_is_named_in_the_reasons_and_listed_by_the_printer(client, create_job, create_printer, session_factory):
    from app.plugins.capabilities.filament_inventory import InvMaterial, InvSpool
    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import use_provider
    silk = InvMaterial(ref="m1", name="x", material="PLA", subtype="silk", color_hex="#FF0000", vendor="Acme")
    await use_provider(FakeInventoryProvider(spools=[InvSpool(ref="9", material_ref="m1", material=silk)]), plugin_id="spoolman")
    slots = [{"slot": 0, "type": "PLA", "subtype": "standard", "color": "#FF0000",
              "inventory": {"provider": "spoolman", "spool_ref": "9"}}]
    pid = await create_printer(loaded_filaments=slots)
    assert (await client.get(f"/api/v1/printers/{pid}/slot-conflicts")).json() == {"conflicts": []}     # hand-entered: never in conflict
    await _mark_reported(session_factory, pid)
    job_id = await create_job(printer_id=pid, filament_type="PLA", filament_subtype="any")
    (cfg,) = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()["printer_configs"]
    assert cfg["filament_match"]["eligible"] is False
    assert cfg["filament_match"]["reasons"] == ["T0: bound spool conflicts with what the printer reports"]
    assert (await client.get(f"/api/v1/printers/{pid}/slot-conflicts")).json() == {"conflicts": [0]}
    assert (await client.get("/api/v1/printers/999/slot-conflicts")).status_code == 404


async def test_a_client_cannot_mark_a_slot_hardware_reported(client, create_printer, session_factory):
    from app.models import Printer
    slot = {"slot": 0, "type": "PLA", "subtype": "standard", "color": "#FF0000", "reported": True}
    pid = await create_printer(loaded_filaments=[slot])
    async with session_factory() as s:
        assert "reported" not in (await s.get(Printer, pid)).loaded_filaments[0]
    await _mark_reported(session_factory, pid)
    edited = {**slot, "color": "#00FF00", "reported": False}                        # an operator edit of a telemetry slot
    assert (await client.patch(f"/api/v1/printers/{pid}", json={"loaded_filaments": [edited]})).status_code == 200
    async with session_factory() as s:
        stored = (await s.get(Printer, pid)).loaded_filaments[0]
    assert stored["reported"] is True and stored["color"] == "#00FF00"


async def test_clearing_a_project_items_type_with_the_legacy_any_also_drops_its_subtype(client, project, upload_3mf):
    f = await upload_3mf()
    item = (await client.post(f"/api/v1/projects/{project}/items", json={"file_id": f, **TUPLE})).json()
    out = (await client.put(f"/api/v1/projects/{project}/items/{item['id']}", json={"filament_type": "any"})).json()
    assert (out["filament_type"], out["filament_subtype"]) == (None, None)       # not "silk with no type", which would narrow the ask
