"""BIZ-366 v2: each physical print attempt deducts what the printer plugin reported per loaded slot (else, for a completed print, the
slicer estimate). Failed, cancelled and requeued attempts settle too; a reprint deducts on top of the earlier attempt."""
from sqlalchemy import select

from app.eventing import EventEnvelope
from app.models import EventOutbox, Job, JobPrinterConfig, Printer
from app.services import job_details
from app.services.inventory import subscribers, tasks
from app.services.job_reporter import REPORT_FIELD
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import spool, use_provider
from tests.services.test_queue_engine import _seed_completing_job
from tests.waiting import settle_events


def bound(slot_id, ref, **extra):
    return {"slot": slot_id, "type": "PLA", "inventory": {"provider": "spoolman", "spool_ref": str(ref)}, **extra}


# Published slot ids are not list positions (a Bambu tray id is unit*4 + tray): 0, 4 and 9 sit at positions 0, 1 and 2.
LOADED = [bound(0, 11), bound(4, 22), bound(9, 33)]


async def start(factory, engine=None, printer_id=None, job_id=None):
    if engine is None:
        engine, printer_id, job_id = await _seed_completing_job(factory, grams=20.0)
        fake = FakeInventoryProvider(spools=[spool(str(i), 200.0) for i in (11, 22, 33, 88, 99)])
        await use_provider(fake)
        async with factory() as session:
            (await session.get(Printer, printer_id)).loaded_filaments = list(LOADED)
            config = (await session.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))).scalar_one()
            config.filament_map = [{"model_filament": 1, "tool_index": 2}, {"model_filament": 2, "tool_index": 0}]
            config.slot_plan = [{"model_filament": 1, "slot": 2}, {"model_filament": 2, "slot": 0}]       # list positions
            job = await session.get(Job, job_id)
            job.actual_filament_breakdown = [{"extruder_index": 0, "grams": 12.0}, {"extruder_index": 1, "grams": 8.0}]
            await session.commit()
    else:
        fake = None
    assert await engine.mark_printing(job_id, printer_id, origin="core")
    await settle_events(factory)
    async with factory() as session:
        context = (await job_details.get_details_in(session, job_id, ["filament_usage_context"], origin="core"))["filament_usage_context"]["value"]
    return engine, printer_id, job_id, fake, context


async def report(factory, job_id, context, slots, **over):
    """What `JobReporter.report_usage` stores: the plugin's latest report, and core's copy per attempt."""
    from app.services.job_reporter import REPORTS_FIELD
    body = {"version": 2, "attempt_id": context["attempt_id"], "revision": 1, "final": True, "source": "reported",
            "slots": [{"slot": s, "grams": g} for s, g in slots], **over}
    async with factory() as session:
        saved = (await job_details.get_details_in(session, job_id, [REPORTS_FIELD], origin="core"))[REPORTS_FIELD]["value"] or {}
        await job_details.set_details(session, job_id, "prn", {REPORT_FIELD: body})
        await job_details.set_details(session, job_id, "core", {REPORTS_FIELD: {**saved, context["attempt_id"]: body}})
        await session.commit()


async def drain(factory):
    await settle_events(factory)
    await tasks.drain()


async def accounting(factory, job_id):
    async with factory() as session:
        return (await job_details.get_details_in(session, job_id, ["filament_accounting"], origin="core"))["filament_accounting"]["value"]


async def test_no_report_on_a_completed_print_deducts_the_estimate_from_the_start_time_bindings(session_factory):
    engine, printer_id, job_id, fake, context = await start(session_factory)
    async with session_factory() as session:          # slots edited after the start: the start bindings stay authoritative
        (await session.get(Printer, printer_id)).loaded_filaments = [bound(0, 99), bound(4, 22), bound(9, 88)]
        await session.commit()
    assert await engine.complete_job(job_id, printer_id, origin="core")
    await drain(session_factory)
    assert sorted(fake.writes) == [("11", 192.0), ("33", 188.0)]
    (entry,) = (await accounting(session_factory, job_id))["attempts"].values()
    assert entry["source"] == "estimated" and entry["result"] == "complete"
    assert sorted((s["slot"], s["grams"]) for s in entry["slots"]) == [(0, 8.0), (9, 12.0)]


async def test_a_rollover_is_two_reported_slots_and_both_bound_spools_are_deducted(session_factory):
    engine, printer_id, job_id, fake, context = await start(session_factory)
    await report(session_factory, job_id, context, [(9, 5.0), (4, 15.0)])          # by published slot id
    assert await engine.complete_job(job_id, printer_id, origin="core")
    await drain(session_factory)
    assert sorted(fake.writes) == [("22", 185.0), ("33", 195.0)]
    (entry,) = (await accounting(session_factory, job_id))["attempts"].values()
    assert entry["source"] == "reported" and entry["grams"] == 20.0


async def test_a_failed_print_deducts_its_report_and_a_reprint_deducts_on_top(session_factory):
    engine, printer_id, job_id, fake, ctx1 = await start(session_factory)
    await report(session_factory, job_id, ctx1, [(0, 5.0)])
    assert await engine.requeue_dead_print(job_id, printer_id, "offline")         # attempt 1 ends: requeued
    await drain(session_factory)
    assert fake.writes == [("11", 195.0)]
    _, _, _, _, ctx2 = await start(session_factory, engine, printer_id, job_id)    # the reprint is a new attempt
    assert ctx2["attempt_id"] != ctx1["attempt_id"]
    await report(session_factory, job_id, ctx2, [(0, 10.0)])
    assert await engine.complete_job(job_id, printer_id, origin="core")
    await drain(session_factory)
    assert fake.writes[-1] == ("11", 185.0)                                        # 200 - 5 - 10
    acc = await accounting(session_factory, job_id)
    assert [e["result"] for e in acc["attempts"].values()] == ["requeued", "complete"] and acc["total_grams"] == 15.0
    async with session_factory() as session:
        assert (await session.get(Job, job_id)).actual_filament_grams == 20.0          # the sliced estimate stays (it is the fallback)


async def test_a_failed_print_with_no_report_deducts_nothing_and_says_so(session_factory):
    engine, printer_id, job_id, fake, _ = await start(session_factory)
    assert await engine.fail_started_job(job_id, printer_id, reason="spaghetti", origin="core")
    await drain(session_factory)
    assert fake.writes == []
    (entry,) = (await accounting(session_factory, job_id))["attempts"].values()
    assert entry["result"] == "failed" and entry["source"] == "none" and "reported no filament usage" in entry["notes"][0]
    async with session_factory() as session:
        assert "reported no filament usage" in (await session.get(Job, job_id)).deduction_note


async def test_a_failed_print_with_a_report_deducts_it(session_factory):
    engine, printer_id, job_id, fake, context = await start(session_factory)
    await report(session_factory, job_id, context, [(4, 3.5)], final=False)        # the plugin's last word, final or not
    assert await engine.fail_started_job(job_id, printer_id, reason="clog", origin="core")
    await drain(session_factory)
    assert fake.writes == [("22", 196.5)]


async def test_a_redelivered_attempt_end_or_completion_never_deducts_twice_or_differently(session_factory):
    engine, printer_id, job_id, fake, context = await start(session_factory)
    await report(session_factory, job_id, context, [(0, 5.0)])
    assert await engine.fail_started_job(job_id, printer_id, reason="clog", origin="core")
    await drain(session_factory)
    await report(session_factory, job_id, context, [(0, 50.0)], revision=2)       # a later revision after the attempt ended
    async with session_factory() as session:
        row = (await session.execute(select(EventOutbox).where(EventOutbox.name == "job.attempt_ended"))).scalar_one()
        envelope = EventEnvelope.model_validate(row.envelope)
    await subscribers.deduct_on_attempt_ended(envelope)
    await tasks.drain()
    assert fake.writes == [("11", 195.0)]
    (entry,) = (await accounting(session_factory, job_id))["attempts"].values()
    assert entry["grams"] == 5.0                                                  # the settled record is frozen too


async def test_a_slot_loaded_mid_print_is_charged_to_its_end_time_binding(session_factory):
    engine, printer_id, job_id, fake, context = await start(session_factory)
    async with session_factory() as session:          # the operator loaded a spool into an empty tray (id 13) during the print
        (await session.get(Printer, printer_id)).loaded_filaments = [*LOADED, bound(13, 88)]
        await session.commit()
    await report(session_factory, job_id, context, [(13, 7.0)])
    assert await engine.complete_job(job_id, printer_id, origin="core")
    await drain(session_factory)
    assert fake.writes == [("88", 193.0)]


async def test_a_report_for_an_unknown_slot_deducts_the_rest_and_notes_the_slot(session_factory):
    engine, printer_id, job_id, fake, context = await start(session_factory)
    await report(session_factory, job_id, context, [(0, 4.0), (21, 2.0)])
    assert await engine.complete_job(job_id, printer_id, origin="core")
    await drain(session_factory)
    assert fake.writes == [("11", 196.0)]
    (entry,) = (await accounting(session_factory, job_id))["attempts"].values()
    assert "slot 21" in entry["notes"][0]


async def test_an_invalid_report_deducts_nothing_and_never_falls_back_to_the_estimate(session_factory):
    engine, printer_id, job_id, fake, context = await start(session_factory)
    await report(session_factory, job_id, context, [(0, 4.0), (0, 1.0)])           # a slot twice
    assert await engine.complete_job(job_id, printer_id, origin="core")
    await drain(session_factory)
    assert fake.writes == []
    (entry,) = (await accounting(session_factory, job_id))["attempts"].values()
    assert "invalid" in entry["notes"][0]


async def test_an_earlier_attempts_report_is_not_this_attempts(session_factory):
    engine, printer_id, job_id, fake, ctx1 = await start(session_factory)
    assert await engine.requeue_dead_print(job_id, printer_id, "offline")
    await drain(session_factory)
    _, _, _, _, ctx2 = await start(session_factory, engine, printer_id, job_id)
    await report(session_factory, job_id, ctx1, [(0, 99.0)])                      # a stale report naming attempt 1
    assert await engine.complete_job(job_id, printer_id, origin="core")
    await drain(session_factory)
    assert sorted(fake.writes) == [("11", 192.0), ("33", 188.0)]                  # attempt 2: the estimate, not 99 g


async def test_the_deduction_starts_from_the_attempts_start_weight_not_the_weight_at_its_end(session_factory):
    """The start snapshot is per attempt: a spool weight that changed during the print (an edit elsewhere) is not the base."""
    from app.models import InventoryPendingWrite
    engine, printer_id, job_id, fake, context = await start(session_factory)
    fake.spools["11"] = spool("11", 150.0)                                        # changed during the print
    await report(session_factory, job_id, context, [(0, 5.0)])
    assert await engine.complete_job(job_id, printer_id, origin="core")
    await drain(session_factory)
    async with session_factory() as session:
        (row,) = (await session.execute(select(InventoryPendingWrite).where(InventoryPendingWrite.spool_ref == "11"))).scalars()
    assert (row.pre_weight_g, row.target_g, row.attempt_id) == (200.0, 195.0, context["attempt_id"])


async def _outbox(factory, name):
    async with factory() as session:
        rows = (await session.execute(select(EventOutbox).where(EventOutbox.name == name).order_by(EventOutbox.id))).scalars().all()
        return [EventEnvelope.model_validate(r.envelope) for r in rows]


async def test_a_reprints_start_snapshot_waits_until_the_previous_attempt_is_settled(session_factory):
    """Review: `job.started` (attempt 2) and `job.attempt_ended` (attempt 1) are different subscribers with no ordering. If the
    snapshot came first it would start from the weight before attempt 1's write, and that write would be superseded (lost)."""
    import pytest
    from app.services.inventory.subscribers import PreviousAttemptPending
    engine, printer_id, job_id, fake, ctx1 = await start(session_factory)
    await report(session_factory, job_id, ctx1, [(0, 5.0)])
    assert await engine.requeue_dead_print(job_id, printer_id, "offline")          # attempt_ended written, not delivered
    assert await engine.mark_printing(job_id, printer_id, origin="core")           # attempt 2 starts at once
    started = (await _outbox(session_factory, "job.started"))[-1]
    with pytest.raises(PreviousAttemptPending):
        await subscribers.snapshot_on_start(started)
    (ended,) = await _outbox(session_factory, "job.attempt_ended")
    await subscribers.deduct_on_attempt_ended(ended)
    await tasks.drain()
    await subscribers.snapshot_on_start(started)                                   # the retry: now chained from attempt 1
    async with session_factory() as session:
        ctx2 = (await job_details.get_details_in(session, job_id, ["filament_usage_context"], origin="core"))["filament_usage_context"]["value"]
    assert ctx2["previous_attempt_id"] == ctx1["attempt_id"]
    await report(session_factory, job_id, ctx2, [(0, 10.0)])
    assert await engine.complete_job(job_id, printer_id, origin="core")
    await drain(session_factory)
    assert fake.writes[-1] == ("11", 185.0)


async def test_two_attempts_settling_at_once_both_stay_on_record(session_factory, monkeypatch):
    import asyncio
    from app.services.inventory import subscribers as subs
    real = subs.job_details.get_details_in

    async def slow(session, job_id, fields, **kw):                # a yield between reading the accounting and writing it back
        out = await real(session, job_id, fields, **kw)
        if REPORT_FIELD in fields:
            await asyncio.sleep(0.05)
        return out
    monkeypatch.setattr(subs.job_details, "get_details_in", slow)
    engine, printer_id, job_id, fake, ctx1 = await start(session_factory)
    await report(session_factory, job_id, ctx1, [(0, 5.0)])
    assert await engine.requeue_dead_print(job_id, printer_id, "offline")
    (ended,) = await _outbox(session_factory, "job.attempt_ended")
    assert await engine.mark_printing(job_id, printer_id, origin="core")           # attempt 2, before attempt 1 is settled
    async with session_factory() as session:
        ctx2 = (await job_details.get_details_in(session, job_id, ["filament_usage_context"], origin="core"))["filament_usage_context"]["value"]
    await report(session_factory, job_id, ctx2, [(4, 7.0)])
    assert await engine.complete_job(job_id, printer_id, origin="core")
    (complete,) = await _outbox(session_factory, "job.complete")
    await asyncio.gather(subscribers.deduct_on_attempt_ended(ended), subscribers.deduct_on_complete(complete),
                         subscribers.deduct_on_complete(complete))
    await tasks.drain()
    acc = await accounting(session_factory, job_id)
    assert set(acc["attempts"]) == {ctx1["attempt_id"], ctx2["attempt_id"]} and acc["total_grams"] == 12.0
    assert sorted(fake.writes) == [("11", 195.0), ("22", 193.0)]


async def test_a_redelivered_completion_never_deducts_twice(session_factory):
    engine, printer_id, job_id, fake, context = await start(session_factory)
    await report(session_factory, job_id, context, [(0, 5.0)])
    assert await engine.complete_job(job_id, printer_id, origin="core")
    await drain(session_factory)
    (complete,) = await _outbox(session_factory, "job.complete")
    await subscribers.deduct_on_complete(complete)
    await tasks.drain()
    assert fake.writes == [("11", 195.0)]


async def test_a_record_settled_before_v2_is_not_deducted_again_on_redelivery(session_factory):
    """Review: a PR #173 `filament_accounting` (one attempt, no `attempts`) whose writes have no attempt id."""
    from app.models import InventoryPendingWrite
    engine, printer_id, job_id, fake, context = await start(session_factory)
    async with session_factory() as session:
        await job_details.set_details(session, job_id, "core", {"filament_accounting": {
            "status": "planned", "source": "reported", "attempt_id": context["attempt_id"], "provider": "spoolman",
            "by_slot": {"0": 5.0}, "uses": [{"spool_ref": "11", "grams": 5.0}], "notes": []}})
        session.add(InventoryPendingWrite(provider="spoolman", spool_ref="11", target_g=195.0, job_id=job_id, printer_id=printer_id,
                                          source="queue", created_at="2026-11-04T00:00:00", status="applied", attempt_id=None))
        await session.commit()
    assert await engine.complete_job(job_id, printer_id, origin="core")
    await drain(session_factory)
    assert fake.writes == []
    async with session_factory() as session:
        rows = (await session.execute(select(InventoryPendingWrite).where(InventoryPendingWrite.job_id == job_id))).scalars().all()
    assert len(rows) == 1
