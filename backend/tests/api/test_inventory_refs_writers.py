"""One test per writer of the legacy ids (BIZ-217): every path that stores a spool binding must write the provider-namespaced
form next to the legacy one. Each fails if its writer skips the normalizer."""
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
    assert await _slots(client, pid) == [
        {"slot": 0, "type": "PLA", "subtype": "standard", "manufacturer": None, "spoolman_spool_id": "7", "inventory": INV7}]


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


async def test_a_new_client_rebind_keeps_the_legacy_spool_id_an_int(client, create_printer):
    pid = await create_printer(loaded_filaments=[{"slot": 0, "spoolman_spool_id": 7}])
    await client.patch(f"/api/v1/printers/{pid}", json={"loaded_filaments": [
        {"slot": 0, "inventory": {"provider": "spoolman", "spool_ref": "12"}}]})
    (slot,) = await _slots(client, pid)
    assert slot["spoolman_spool_id"] == 12 and isinstance(slot["spoolman_spool_id"], int)
