from sqlalchemy import select

from app.eventing import EventEnvelope
from app.models import EventOutbox, Job, JobPrinterConfig, Printer
from app.services import job_details
from app.services.inventory import subscribers, tasks
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import spool, use_provider
from tests.services.test_queue_engine import _seed_completing_job
from tests.waiting import settle_events


def bound(index, ref):
    return {"slot": index, "type": "PLA", "inventory": {"provider": "spoolman", "spool_ref": str(ref)}}


async def start(factory):
    engine, printer_id, job_id = await _seed_completing_job(factory, grams=20.0)
    fake = FakeInventoryProvider(spools=[spool(str(i), 200.0) for i in (11, 22, 33, 88, 99)])
    await use_provider(fake)
    engine._mgr.get_client.return_value.slice_tool_mapping = False
    async with factory() as session:
        printer = await session.get(Printer, printer_id)
        printer.loaded_filaments = [bound(0, 11), bound(1, 22), bound(2, 33)]
        config = (await session.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))).scalar_one()
        config.filament_map = [{"model_filament": 1, "tool_index": 2}, {"model_filament": 2, "tool_index": 0}]
        config.slot_plan = [{"model_filament": 1, "slot": 2}, {"model_filament": 2, "slot": 0}]
        job = await session.get(Job, job_id)
        job.actual_filament_breakdown = [{"extruder_index": 0, "grams": 12.0}, {"extruder_index": 1, "grams": 8.0}]
        await session.commit()
    assert await engine.mark_printing(job_id, printer_id, origin="core")
    await settle_events(factory)
    async with factory() as session:
        context = (await job_details.get_details_in(session, job_id, ["filament_usage_context"], origin="core"))["filament_usage_context"]["value"]
    return engine, printer_id, job_id, fake, context


def usage(context, **overrides):
    return {"version": 1, "attempt_id": context["attempt_id"], "report_id": "printer-summary", "revision": 1,
            "basis": "physical_slot", "source": "reported", "final": True, "complete": True, "rollover": True,
            "grams": 20.0, "breakdown": [{"index": 2, "grams": 5.0}, {"index": 1, "grams": 15.0}], **overrides}


async def finish(factory, engine, printer_id, job_id, report=None):
    assert await engine.complete_job(job_id, printer_id, origin="core", details={"filament_usage": report} if report is not None else None)
    await settle_events(factory)
    await tasks.drain()


async def test_ams_estimates_deduct_from_the_start_time_bindings_after_slots_are_edited(session_factory):
    engine, printer_id, job_id, fake, context = await start(session_factory)
    assert context["basis"] == "slicer_filament"
    async with session_factory() as session:
        (await session.get(Printer, printer_id)).loaded_filaments = [bound(0, 99), bound(1, 22), bound(2, 88)]
        await session.commit()
    await finish(session_factory, engine, printer_id, job_id)
    assert sorted(fake.writes) == [("11", 192.0), ("33", 188.0)]
    async with session_factory() as session:
        accounting = (await job_details.get_details_in(session, job_id, ["filament_accounting"], origin="core"))["filament_accounting"]["value"]
    assert accounting["source"] == "estimated" and accounting["by_slot"] == {"2": 12.0, "0": 8.0}


async def test_quantified_rollover_uses_actual_physical_slot_totals_and_spare_spools_starting_weights(session_factory):
    engine, printer_id, job_id, fake, context = await start(session_factory)
    await finish(session_factory, engine, printer_id, job_id, usage(context))
    assert sorted(fake.writes) == [("22", 185.0), ("33", 195.0)]


async def test_unquantified_rollover_never_charges_the_original_spool(session_factory):
    engine, printer_id, job_id, fake, context = await start(session_factory)
    await finish(session_factory, engine, printer_id, job_id, usage(context, complete=False))
    assert fake.writes == []
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        assert job.deduction_skipped and "partial" in job.deduction_note


async def test_later_report_revisions_and_duplicate_completion_cannot_reallocate_or_double_deduct(session_factory):
    engine, printer_id, job_id, fake, context = await start(session_factory)
    await finish(session_factory, engine, printer_id, job_id, usage(context))
    async with session_factory() as session:
        await job_details.set_details(session, job_id, "core", {"filament_usage": usage(context, revision=2,
            breakdown=[{"index": 0, "grams": 20.0}])})
        envelope = EventEnvelope.model_validate((await session.execute(select(EventOutbox).where(EventOutbox.name == "job.complete"))).scalar_one().envelope)
        await session.commit()
    await subscribers.deduct_on_complete(envelope)
    await tasks.drain()
    assert sorted(fake.writes) == [("22", 185.0), ("33", 195.0)]


async def test_a_rollover_report_without_any_quantity_still_records_a_reconciliation_warning(session_factory):
    engine, printer_id, job_id, fake, context = await start(session_factory)
    async with session_factory() as session:
        (await session.get(Job, job_id)).actual_filament_grams = None
        await session.commit()
    await finish(session_factory, engine, printer_id, job_id, usage(context, grams=None, complete=False))
    assert fake.writes == []
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        assert job.deduction_skipped and "Invalid printer filament usage report" in job.deduction_note
