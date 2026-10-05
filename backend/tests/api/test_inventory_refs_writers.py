"""One test per writer of the legacy ids (BIZ-217): every path that stores a spool binding or a "specific material" ask must
write the provider-namespaced form next to the legacy one. Each fails if its writer skips the normalizer."""
import pytest

from app.models import Job, JobModelTarget, JobPrinterConfig, Printer, ProjectItem
from app.services.printer_manager import printer_manager
from sqlalchemy import select
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import use_provider

INV7 = {"provider": "spoolman", "spool_ref": "7"}


async def _slots(client, printer_id):
    return (await client.get(f"/api/v1/printers/{printer_id}")).json()["loaded_filaments"]


# --- printers: create + whole-list PATCH ----------------------------------------------------------------------------------------------

async def test_create_printer_with_the_legacy_key_stores_the_namespaced_binding(client, create_printer):
    pid = await create_printer(loaded_filaments=[{"slot": 0, "type": "PLA", "spoolman_spool_id": "7"}])
    assert await _slots(client, pid) == [{"slot": 0, "type": "PLA", "spoolman_spool_id": "7", "inventory": INV7}]


async def test_patch_old_key_only_new_key_only_and_both(client, create_printer):
    pid = await create_printer(loaded_filaments=[{"slot": 0, "type": "PLA"}, {"slot": 1, "type": "PETG"}])

    await client.patch(f"/api/v1/printers/{pid}", json={"loaded_filaments": [
        {"slot": 0, "type": "PLA", "spoolman_spool_id": "7"},                                                # old key only
        {"slot": 1, "type": "PETG", "inventory": {"provider": "spoolman", "spool_ref": "8"}}]})              # new key only
    a, b = await _slots(client, pid)
    assert (a["inventory"], a["spoolman_spool_id"]) == (INV7, "7")
    assert (b["inventory"]["spool_ref"], b["spoolman_spool_id"]) == ("8", 8)

    await client.patch(f"/api/v1/printers/{pid}", json={"loaded_filaments": [
        {"slot": 0, "type": "PLA", "spoolman_spool_id": "7", "inventory": INV7},                             # both, consistent
        {"slot": 1, "type": "PETG", "spoolman_spool_id": "9", "inventory": {"provider": "spoolman", "spool_ref": "8"}}]})   # old client re-link
    a, b = await _slots(client, pid)
    assert a["inventory"] == INV7 and b["inventory"]["spool_ref"] == "9"


async def test_a_patch_that_omits_a_slot_drops_it_and_one_that_keeps_it_keeps_its_binding(client, create_printer):
    pid = await create_printer(loaded_filaments=[{"slot": 0, "spoolman_spool_id": "7"}, {"slot": 1, "spoolman_spool_id": "8"}])
    await client.patch(f"/api/v1/printers/{pid}", json={"loaded_filaments": [{"slot": 1, "spoolman_spool_id": "8"}]})
    (only,) = await _slots(client, pid)
    assert only["slot"] == 1 and only["inventory"]["spool_ref"] == "8"
    await client.patch(f"/api/v1/printers/{pid}", json={"name": "renamed"})                                   # not sent: untouched
    assert (await _slots(client, pid))[0]["inventory"]["spool_ref"] == "8"


async def test_a_binding_to_another_provider_survives_an_old_client_roundtrip(client, create_printer):
    pid = await create_printer(loaded_filaments=[{"slot": 0, "type": "PLA", "inventory": {"provider": "local", "spool_ref": "3"}}])
    stored = (await _slots(client, pid))[0]
    assert stored["inventory"] == {"provider": "local", "spool_ref": "3"} and stored.get("spoolman_spool_id") is None
    # an old client reads the slot and writes the object back without understanding `inventory`
    await client.patch(f"/api/v1/printers/{pid}", json={"loaded_filaments": [{"slot": 0, "type": "PETG", "spoolman_spool_id": None}]})
    assert (await _slots(client, pid))[0]["inventory"] == {"provider": "local", "spool_ref": "3"}


# --- AMS reports (this test fails against the old two-named-keys merge) ----------------------------------------------------------------

async def test_an_ams_report_preserves_every_themis_owned_slot_key(client, create_printer, session_factory):
    pid = await create_printer(loaded_filaments=[
        {"slot": 0, "type": "PLA", "filament_profile": "PLA @P1S", "spoolman_spool_id": "7"}])
    printer_manager.set_session_factory(session_factory)
    printer_manager._on_state_broadcast = None

    await printer_manager.on_ams_change(pid, [{"slot": 0, "type": "PETG", "color": "#00FF00", "filament_id": "GFA01"},
                                              {"slot": 1, "type": "PLA", "color": "#FFFFFF", "filament_id": "GFA00"}])

    s0, s1 = await _slots(client, pid)
    assert (s0["type"], s0["filament_id"]) == ("PETG", "GFA01")                          # hardware facts come from the report
    assert (s0["filament_profile"], s0["spoolman_spool_id"], s0["inventory"]) == ("PLA @P1S", "7", INV7)   # links survive
    assert "inventory" not in s1 and not s1.get("spoolman_spool_id")                      # a new slot starts unbound


async def test_laminus_confirm_remap_keeps_the_inventory_binding_it_spreads(client, create_printer):
    from tests.api.test_laminus_api import _confirm, _park, _pending
    pid = await create_printer(loaded_filaments=[{"slot": 0, "type": "PLA", "filament_profile": "Old PLA", "spoolman_spool_id": "7"}])
    sync = _park(_pending(printers=[{"field": "filament_profile", "stale_value": "Old PLA", "required": False, "options_kind": "filament",
                                     "affected_printer_ids": [pid], "affected_printer_names": ["P1S"], "affected_slots": [0]}]))

    resp = await _confirm(client, sync, printers=[{"field": "filament_profile", "stale_value": "Old PLA", "new_value": "New PLA"}])

    assert resp.status_code == 200, resp.text
    slot = (await _slots(client, pid))[0]
    assert slot["filament_profile"] == "New PLA" and slot["inventory"] == INV7 and slot["spoolman_spool_id"] == "7"


# --- jobs, model targets, project items, order parts ------------------------------------------------------------------------------------

async def _configs(session_factory, job_id):
    async with session_factory() as s:
        return (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))).scalars().all()


@pytest.mark.parametrize("overrides, want", [
    ({"filament_id": 12}, (12, "spoolman", "12")),                                     # old client
    ({"material_ref": "12"}, (12, "spoolman", "12")),                                  # new client, no provider -> ... see next test
    ({"material_provider": "spoolman", "material_ref": "12"}, (12, "spoolman", "12")),
    ({"filament_id": 3, "material_provider": "spoolman", "material_ref": "12"}, (12, "spoolman", "12")),     # ref wins
    ({}, (None, None, None)),
])
async def test_job_configs_store_both_forms(client, create_job, session_factory, overrides, want):
    if "material_ref" in overrides and "material_provider" not in overrides:
        await use_provider(FakeInventoryProvider(), plugin_id="spoolman")                # ref-only needs an active provider
    job_id = await create_job(**overrides)
    (cfg,) = await _configs(session_factory, job_id)
    assert (cfg.filament_id, cfg.material_provider, cfg.material_ref) == want


async def test_a_job_config_for_another_provider_has_no_integer_mirror(client, create_job, session_factory):
    job_id = await create_job(material_provider="local", material_ref="m-5")
    (cfg,) = await _configs(session_factory, job_id)
    assert (cfg.filament_id, cfg.material_provider, cfg.material_ref) == (None, "local", "m-5")


async def test_a_bad_material_ref_is_a_422_and_creates_nothing(client, upload_3mf, create_printer, session_factory):
    resp = await client.post("/api/v1/jobs", json={"uploaded_file_id": await upload_3mf(), "plate_number": 1, "printer_configs": [
        {"printer_id": await create_printer(), "print_profile": "0.20mm", "filament_type": "any", "filament_color": "any",
         "material_provider": "spoolman", "material_ref": "abc"}]})
    assert resp.status_code == 422
    async with session_factory() as s:
        assert (await s.execute(select(Job))).scalars().all() == []


async def test_model_targets_store_both_forms_and_materialize_them_into_configs(client, upload_3mf, create_printer, session_factory):
    from unittest.mock import MagicMock, patch
    p = await create_printer()
    with patch("app.api.routes.jobs.queue_engine", MagicMock()):
        resp = await client.post("/api/v1/jobs", json={"uploaded_file_id": await upload_3mf(), "plate_number": 1, "model_targets": [
            {"machine_profile": "Bambu Lab P1S 0.4", "print_profile": "0.20mm", "filament_type": "PLA", "filament_color": "any",
             "filament_id": 5}]})
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]
    async with session_factory() as s:
        t = (await s.execute(select(JobModelTarget).where(JobModelTarget.job_id == job_id))).scalars().one()
        assert (t.filament_id, t.material_provider, t.material_ref) == (5, "spoolman", "5")
    (cfg,) = await _configs(session_factory, job_id)                                    # materialised for the matching printer
    assert cfg.printer_id == p and (cfg.filament_id, cfg.material_provider, cfg.material_ref) == (5, "spoolman", "5")
    details = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()
    assert (details["model_targets"][0]["material_provider"], details["model_targets"][0]["material_ref"]) == ("spoolman", "5")
    assert details["printer_configs"][0]["material_ref"] == "5"


async def test_project_items_store_both_forms_on_create_and_update(client, upload_3mf, session_factory):
    project = (await client.post("/api/v1/projects", json={"name": "P"})).json()["id"]
    f = await upload_3mf()
    created = (await client.post(f"/api/v1/projects/{project}/items", json={"file_id": f, "filament_id": 4})).json()
    assert (created["filament_id"], created["material_provider"], created["material_ref"]) == (4, "spoolman", "4")
    upd = (await client.put(f"/api/v1/projects/{project}/items/{created['id']}",
                            json={"material_provider": "local", "material_ref": "m-9"})).json()
    assert (upd["filament_id"], upd["material_provider"], upd["material_ref"]) == (None, "local", "m-9")
    unchanged = (await client.put(f"/api/v1/projects/{project}/items/{created['id']}", json={"quantity": 2})).json()
    assert unchanged["material_ref"] == "m-9" and unchanged["quantity"] == 2
    async with session_factory() as s:
        assert (await s.get(ProjectItem, created["id"])).material_ref == "m-9"
    bad = await client.post(f"/api/v1/projects/{project}/items", json={"file_id": f, "material_provider": "spoolman", "material_ref": "x"})
    assert bad.status_code == 422


async def test_order_parts_store_both_forms(client):
    from tests.api.test_orders_api import _create_order
    order = (await _create_order(client, parts=[
        {"name": "Old client", "filament_id": 3}, {"name": "New client", "material_provider": "local", "material_ref": "m-1"},
        {"name": "Neither"}])).json()
    old, new, none = order["parts"]
    assert (old["filament_id"], old["material_provider"], old["material_ref"]) == (3, "spoolman", "3")
    assert (new["filament_id"], new["material_provider"], new["material_ref"]) == (None, "local", "m-1")
    assert (none["filament_id"], none["material_provider"], none["material_ref"]) == (None, None, None)
    patched = (await client.patch(f"/api/v1/orders/{order['id']}", json={"parts": [{"name": "x", "filament_id": 9}]})).json()
    assert patched["parts"][0]["material_ref"] == "9"


# --- project generation groups by the namespaced ask ------------------------------------------------------------------------------------

def test_project_items_group_by_provider_and_ref_and_label_files_accordingly():
    from app.api.routes.projects import _filament_label, _item_material, _material_cols
    mk = lambda **kw: ProjectItem(project_id=1, file_id=1, filament_type="PLA", filament_color="any", **kw)    # noqa: E731
    assert _item_material(mk(filament_id=4)) == ("spoolman", "4")                    # pre-migration row
    assert _item_material(mk(material_provider="local", material_ref="m-1")) == ("local", "m-1")
    assert _item_material(mk()) is None
    assert _filament_label("PLA", "any", ("spoolman", "4")) == "f4" and _filament_label("PLA", "any", ("local", "m-1")) == "local-m-1"
    assert _material_cols(("spoolman", "4")) == {"filament_id": 4, "material_provider": "spoolman", "material_ref": "4"}
    assert _material_cols(("local", "m-1")) == {"filament_id": None, "material_provider": "local", "material_ref": "m-1"}
    assert _material_cols(None) == {"filament_id": None, "material_provider": None, "material_ref": None}


# --- old-client echo: GET now returns material_ref, an old client edits only filament_id and echoes the whole object -------------------

async def test_an_old_client_editing_filament_id_on_a_job_config_is_not_undone_by_the_echoed_ref(client, create_job, session_factory):
    job_id = await create_job(filament_id=5)
    (cfg,) = await _configs(session_factory, job_id)
    from unittest.mock import MagicMock, patch
    body = {"printer_configs": [{"printer_id": cfg.printer_id, "print_profile": "0.20mm", "filament_type": "any", "filament_color": "any",
                                 "filament_id": 9, "material_provider": cfg.material_provider, "material_ref": cfg.material_ref}]}
    with patch("app.api.routes.jobs.queue_engine", MagicMock()):
        resp = await client.patch(f"/api/v1/jobs/{job_id}/configs", json=body)
    assert resp.status_code == 200, resp.text
    (new,) = await _configs(session_factory, job_id)
    assert (new.filament_id, new.material_provider, new.material_ref) == (9, "spoolman", "9")        # the edit won; the stale ref did not


async def test_an_old_client_editing_an_order_part_or_project_item_is_not_undone_by_the_echoed_ref(client, upload_3mf):
    from tests.api.test_orders_api import _create_order
    order = (await _create_order(client, parts=[{"name": "A", "filament_id": 3}])).json()
    echoed = {**order["parts"][0], "filament_id": 8}                       # the stored part incl. material_ref "3", edited filament_id
    patched = (await client.patch(f"/api/v1/orders/{order['id']}", json={"parts": [echoed]})).json()
    assert (patched["parts"][0]["filament_id"], patched["parts"][0]["material_ref"]) == (8, "8")
    same = (await client.patch(f"/api/v1/orders/{order['id']}", json={"parts": [patched["parts"][0]]})).json()
    assert same["parts"][0]["material_ref"] == "8"                         # echoing unchanged keeps it

    project = (await client.post("/api/v1/projects", json={"name": "P"})).json()["id"]
    item = (await client.post(f"/api/v1/projects/{project}/items", json={"file_id": await upload_3mf(), "filament_id": 4})).json()
    upd = (await client.put(f"/api/v1/projects/{project}/items/{item['id']}",
                            json={"filament_id": 6, "material_provider": "spoolman", "material_ref": "4"})).json()
    assert (upd["filament_id"], upd["material_ref"]) == (6, "6")
    keep = (await client.put(f"/api/v1/projects/{project}/items/{item['id']}",
                             json={"filament_id": 6, "material_provider": "spoolman", "material_ref": "6", "quantity": 3})).json()
    assert (keep["material_ref"], keep["quantity"]) == ("6", 3)


async def test_a_new_client_rebind_keeps_the_legacy_spool_id_an_int(client, create_printer):
    pid = await create_printer(loaded_filaments=[{"slot": 0, "spoolman_spool_id": 7}])
    await client.patch(f"/api/v1/printers/{pid}", json={"loaded_filaments": [
        {"slot": 0, "inventory": {"provider": "spoolman", "spool_ref": "12"}}]})
    (slot,) = await _slots(client, pid)
    assert slot["spoolman_spool_id"] == 12 and isinstance(slot["spoolman_spool_id"], int)
