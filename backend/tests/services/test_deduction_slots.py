"""Deduction follows the spool bound to the slot that PRINTED (BIZ-318): per used slot, none bound = no deduction plus a visible
warning, and the job's own ask never selects a spool."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import Job, JobPrinterConfig, Printer
from app.services.inventory import deduction, tasks as inventory_tasks
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import spool, use_provider
from tests.services.test_queue_engine import _outbox_rows, _seed_completing_job, _snapshot
from tests.waiting import settle_events


@pytest.fixture(autouse=True)
async def _active_provider(session_factory):
    """A slot's spool binding only counts for the ACTIVE provider (here "spoolman")."""
    await use_provider(FakeInventoryProvider(), plugin_id="spoolman")


def bound(i, ref, **kw):
    return {"slot": i, "type": "PLA", "subtype": "standard", "color": "#FF0000",
            "inventory": {"provider": "spoolman", "spool_ref": str(ref)}, **kw}


def cfg(plan=None, **kw):
    return SimpleNamespace(ask_manufacturer=None, ask_type=None, ask_subtype=None, ask_color=None, tool_index=None,
                           filament_map=kw.pop("filament_map", None), slot_plan=plan, **kw)


# --- completion_uses (pure) ---------------------------------------------------------------------------------------------------------------

def test_the_spool_comes_from_the_slot_that_printed_not_from_the_ask():
    loaded = [bound(0, 1), bound(1, 2)]
    plan = [{"model_filament": None, "slot": 1}]                                  # the claim picked T1
    assert deduction.completion_uses(cfg(plan), loaded, 12.0, None) == ([{"spool_ref": "2", "grams": 12.0}], [])


def test_two_colours_on_one_slot_deduct_the_total_from_that_spool():
    plan = [{"model_filament": 1, "slot": 0}, {"model_filament": 2, "slot": 0}]
    uses, notes = deduction.completion_uses(cfg(plan, filament_map=[{}, {}]), [bound(0, 1)], 20.0, None)
    assert (uses, notes) == ([{"spool_ref": "1", "grams": 20.0}], [])


def test_several_slots_take_their_share_from_the_per_extruder_breakdown():
    plan = [{"model_filament": 1, "slot": 0}, {"model_filament": 2, "slot": 2}]
    breakdown = [{"extruder_index": 0, "grams": 5.0}, {"extruder_index": 2, "grams": 7.5}]
    uses, notes = deduction.completion_uses(cfg(plan, filament_map=[{}, {}]), [bound(0, 1), bound(1, 9), bound(2, 3)], 12.5, breakdown)
    assert (uses, notes) == ([{"spool_ref": "1", "grams": 5.0}, {"spool_ref": "3", "grams": 7.5}], [])


def test_several_slots_without_a_breakdown_cannot_be_split_so_nothing_is_deducted_and_the_job_says_so():
    plan = [{"model_filament": 1, "slot": 0}, {"model_filament": 2, "slot": 1}]
    uses, notes = deduction.completion_uses(cfg(plan, filament_map=[{}, {}]), [bound(0, 1), bound(1, 2)], 12.5, None)
    assert uses == [] and "per slot was not reported" in notes[0]


def test_a_printing_slot_with_no_bound_spool_is_named_and_skipped():
    plan = [{"model_filament": 1, "slot": 0}, {"model_filament": 2, "slot": 1}]
    breakdown = [{"extruder_index": 0, "grams": 5.0}, {"extruder_index": 1, "grams": 7.0}]
    loaded = [bound(0, 1), {"slot": 1, "type": "PETG", "subtype": "standard", "color": "#000000"}]
    uses, notes = deduction.completion_uses(cfg(plan, filament_map=[{}, {}]), loaded, 12.0, breakdown)
    assert uses == [{"spool_ref": "1", "grams": 5.0}]
    assert notes == ["No spool is bound to T1, so nothing was deducted for it — bind a spool and correct its weight"]


def test_snapshot_refs_are_the_spools_bound_to_the_planned_slots_once_each():
    plan = [{"model_filament": 1, "slot": 0}, {"model_filament": 2, "slot": 1}, {"model_filament": 3, "slot": 2}]
    loaded = [bound(0, 5), bound(1, 5), {"slot": 2, "type": "PLA"}]
    assert deduction.snapshot_refs(cfg(plan, filament_map=[{}, {}, {}]), loaded) == ["5"]


# --- through the queue engine's completion ----------------------------------------------------------------------------------------------

async def test_completion_deducts_from_the_slot_the_claim_planned_even_if_the_slots_changed_since(session_factory):
    engine, printer_id, job_id = await _seed_completing_job(session_factory, grams=10.0)
    fake = FakeInventoryProvider(spools=[spool("1", 300.0), spool("2", 400.0)])
    await use_provider(fake)
    async with session_factory() as s:
        (await s.get(Printer, printer_id)).loaded_filaments = [bound(0, 1), bound(1, 2)]
        (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))).scalar_one().slot_plan = [
            {"model_filament": None, "slot": 1}]
        await s.commit()
    await _snapshot(session_factory, job_id, 400.0, ref="2")

    await engine.handle_print_complete(printer_id)
    await settle_events(session_factory)
    await inventory_tasks.drain()

    assert fake.writes == [("2", pytest.approx(390.0))]


async def test_a_printing_slot_without_a_bound_spool_deducts_nothing_flags_the_job_and_warns(session_factory):
    from app.services.inventory import events
    engine, printer_id, job_id = await _seed_completing_job(session_factory, grams=10.0)
    fake = FakeInventoryProvider(spools=[spool("1", 300.0)])
    await use_provider(fake)
    async with session_factory() as s:
        (await s.get(Printer, printer_id)).loaded_filaments = [{"slot": 0, "type": "PLA", "subtype": "standard", "color": "#FF0000"}]
        await s.commit()
    sent = []

    async def spy(session, event, payload, title, message, job_id=None):
        sent.append((event, payload["job_id"], message))
        return True
    from unittest.mock import patch
    with patch.object(events, "emit", spy):
        await engine.handle_print_complete(printer_id)
        await settle_events(session_factory)
        await inventory_tasks.drain()

    assert fake.writes == [] and await _outbox_rows(session_factory) == []
    async with session_factory() as s:
        job = await s.get(Job, job_id)
    assert job.deduction_skipped is True and "No spool is bound to T0" in job.deduction_note
    assert [(e, j) for e, j, _ in sent] == [("inventory.no_spool_tracked", job_id)]


async def test_a_redelivered_completion_does_not_warn_twice(session_factory):
    from app.services.inventory import events
    from unittest.mock import patch
    await use_provider(FakeInventoryProvider(spools=[]))
    engine, printer_id, job_id = await _seed_completing_job(session_factory)
    async with session_factory() as s:
        (await s.get(Printer, printer_id)).loaded_filaments = [{"slot": 0, "type": "PLA", "subtype": "standard"}]
        await s.commit()
    sent = []

    async def spy(*a, **k):
        sent.append(a[1])
        return True
    with patch.object(events, "emit", spy):
        note = ["No spool is bound to T0, so nothing was deducted for it — bind a spool and correct its weight"]
        await deduction.warn_untracked(session_factory, job_id, printer_id, note)
        await deduction.warn_untracked(session_factory, job_id, printer_id, note)
    assert sent == ["inventory.no_spool_tracked"]
