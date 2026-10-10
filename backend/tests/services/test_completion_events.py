"""Print-completion effects as durable event subscribers (BIZ-269): one event per completion, written atomically with the job's
state change, and effects that survive a crash at every boundary without ever applying twice."""
import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import event, select, update

from app.eventing import EventEnvelope
from app.eventing.hub import hub
from app.models import EventDelivery, EventOutbox, Job, Printer
from app.services import completion_events
from tests.services.test_queue_engine import _make_mock_printer_manager, _outbox_rows, _seed_completing_job, _seed_job, _snapshot
from tests.waiting import settle_events

SUBSCRIBERS = {"core:job_complete.maintenance", "core:job_complete.inventory", "core:job_complete.notices"}


@pytest_asyncio.fixture
async def db(session_factory):
    from app.eventing import hub as hubmod
    saved = hubmod.BACKOFF_BASE_S
    hubmod.BACKOFF_BASE_S = 0.01
    yield session_factory
    hubmod.BACKOFF_BASE_S = saved



async def _engine(db, grams=None):
    from app.services.queue_engine import QueueEngine
    from tests.services.test_queue_engine import MagicMock, SlicerService
    printer_id = 1
    job_id = await _seed_job(db, printer_id, status="printing")
    async with db() as s:
        job = await s.get(Job, job_id)
        job.assigned_printer_id, job.actual_seconds, job.actual_filament_grams = printer_id, 600, grams
        await s.commit()
    slicer = MagicMock(spec=SlicerService)
    return QueueEngine(db, _make_mock_printer_manager([printer_id]), slicer), printer_id, job_id


async def _counters(db, printer_id=1):
    async with db() as s:
        p = await s.get(Printer, printer_id)
        return p.lifetime_job_count, p.lifetime_print_seconds


async def _events(db):
    async with db() as s:
        outbox = list((await s.execute(select(EventOutbox))).scalars())
        deliveries = list((await s.execute(select(EventDelivery))).scalars())
    return outbox, deliveries


async def test_completion_commits_the_job_and_one_durable_event_together_and_effects_follow(db):
    engine, printer_id, job_id = await _engine(db)

    await engine.handle_print_complete(printer_id)

    outbox, deliveries = await _events(db)
    (o,) = outbox
    assert (o.name, o.dedup_key, o.source) == ("job.complete", f"job.complete:{job_id}", "core")
    assert o.envelope["entities"] == {"job_id": job_id, "printer_id": printer_id}
    assert o.envelope["payload"] == {"source": "queue", "actual_seconds": 600, "actual_grams": None, "inventory": None}
    assert {d.subscriber for d in deliveries} == SUBSCRIBERS and {d.status for d in deliveries} == {"pending"}
    assert (await _counters(db)) == (0, 0)                       # committed but not yet handled: still owed, not lost

    await settle_events(db)

    assert (await _counters(db)) == (1, 600)
    assert {d.status for d in (await _events(db))[1]} == {"delivered"}


async def test_duplicate_completion_callbacks_produce_one_event_and_one_accrual(db):
    engine, printer_id, job_id = await _engine(db)

    await asyncio.gather(engine.handle_print_complete(printer_id), engine.handle_print_complete(printer_id))
    await engine.handle_print_complete(printer_id)               # and a later reconciliation
    await settle_events(db)

    outbox, deliveries = await _events(db)
    assert len(outbox) == 1 and len(deliveries) == 3
    assert (await _counters(db)) == (1, 600)


async def test_a_crash_before_commit_loses_nothing_and_leaves_no_event(db, monkeypatch):
    engine, printer_id, job_id = await _engine(db)
    real = completion_events.enqueue_job_complete

    async def crash_after_enqueue(*a, **kw):
        await real(*a, **kw)
        raise RuntimeError("process died before commit")

    monkeypatch.setattr(completion_events, "enqueue_job_complete", crash_after_enqueue)
    with pytest.raises(RuntimeError):
        await engine.handle_print_complete(printer_id)
    async with db() as s:
        assert (await s.get(Job, job_id)).status == "printing"
    assert await _events(db) == ([], [])

    monkeypatch.setattr(completion_events, "enqueue_job_complete", real)
    await engine.handle_print_complete(printer_id)               # the retry completes it once
    await settle_events(db)
    assert len((await _events(db))[0]) == 1 and (await _counters(db)) == (1, 600)


async def test_a_crash_during_the_handler_rolls_the_flag_and_counters_back_together_then_retries(db, monkeypatch):
    from app.eventing import hub as hubmod
    monkeypatch.setattr(hubmod, "BACKOFF_BASE_S", 60.0)          # the retry must not start by itself while we inspect the failure
    engine, printer_id, job_id = await _engine(db)
    await engine.handle_print_complete(printer_id)
    boom = {"left": 1}

    def fail_once(mapper, connection, target):
        if boom["left"]:
            boom["left"] -= 1
            raise RuntimeError("died while writing the counters")

    event.listen(Printer, "before_update", fail_once)
    try:
        await hub.deliver_pending(db)
        async with db() as s:
            assert (await s.get(Job, job_id)).maintenance_accrued in (False, 0)       # the claim rolled back with the counters
        assert (await _counters(db)) == (0, 0)
        (failed,) = [d for d in (await _events(db))[1] if d.subscriber == "core:job_complete.maintenance"]
        assert failed.status == "pending" and failed.attempts == 1 and "died while writing" in failed.last_error

        async with db() as s:                                     # time passes: the backoff elapses
            await s.execute(update(EventDelivery).where(EventDelivery.id == failed.id).values(next_attempt_at="2000-01-01T00:00:00.000000Z"))
            await s.commit()
        await hub.deliver_pending(db)
        assert (await _counters(db)) == (1, 600)
    finally:
        event.remove(Printer, "before_update", fail_once)
    async with db() as s:
        assert (await s.get(Job, job_id)).maintenance_accrued in (True, 1)


async def test_redelivery_after_a_lost_acknowledgement_never_double_applies_maintenance(db):
    engine, printer_id, job_id = await _engine(db)
    await engine.handle_print_complete(printer_id)
    await settle_events(db)
    async with db() as s:                                         # the side effect happened, but the "delivered" mark was lost
        await s.execute(update(EventDelivery).values(status="pending", next_attempt_at="2000-01-01T00:00:00.000000Z"))
        await s.commit()

    await settle_events(db)

    assert (await _counters(db)) == (1, 600)


async def test_redelivery_never_double_deducts_inventory(db):
    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import spool, use_provider

    engine, printer_id, job_id = await _seed_completing_job(db)
    fake = FakeInventoryProvider(spools=[spool("42", 300.0)])
    await use_provider(fake)
    await _snapshot(db, job_id, 300.0)
    await engine.handle_print_complete(printer_id)
    await settle_events(db)
    assert fake.writes == [("42", pytest.approx(282.5))] and len(await _outbox_rows(db)) == 1

    async with db() as s:
        await s.execute(update(EventDelivery).values(status="pending", next_attempt_at="2000-01-01T00:00:00.000000Z"))
        await s.commit()
    await settle_events(db)

    assert len(await _outbox_rows(db)) == 1 and fake.writes == [("42", pytest.approx(282.5))]


async def test_the_spool_is_resolved_when_the_job_completes_not_when_the_event_is_handled(db):
    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import spool, use_provider

    engine, printer_id, job_id = await _seed_completing_job(db)
    fake = FakeInventoryProvider(spools=[spool("42", 300.0), spool("99", 500.0)])
    await use_provider(fake)
    await _snapshot(db, job_id, 300.0)
    await engine.handle_print_complete(printer_id)
    async with db() as s:                                         # the operator swaps the spool while the event is still queued
        printer = await s.get(Printer, printer_id)
        printer.loaded_filaments = [{"type": "PLA", "color": "", "filament_profile": "PLA", "spoolman_spool_id": 99}]
        await s.commit()

    await settle_events(db)

    assert [w[0] for w in fake.writes] == ["42"]


async def test_a_failing_notice_never_fails_the_completion_or_the_critical_effects(db, monkeypatch):
    engine, printer_id, job_id = await _engine(db)
    monkeypatch.setattr(engine, "_broadcast_job", AsyncMock(side_effect=RuntimeError("websocket down")))
    monkeypatch.setattr(engine, "_fire_webhooks", AsyncMock(side_effect=RuntimeError("webhook receiver down")))
    fire_notifications = AsyncMock()
    monkeypatch.setattr(engine, "_fire_notifications", fire_notifications)

    await engine.handle_print_complete(printer_id)
    await settle_events(db)

    async with db() as s:
        assert (await s.get(Job, job_id)).status == "complete"
    assert (await _counters(db)) == (1, 600)
    fire_notifications.assert_awaited_once()                      # one failing channel did not stop the next one
    assert {d.status for d in (await _events(db))[1]} == {"delivered"}


async def test_a_manual_completion_accrues_and_deducts_but_notifies_nobody(db, monkeypatch):
    engine, printer_id, job_id = await _engine(db)
    for name in ("_broadcast_job", "_fire_webhooks", "_fire_notifications"):
        monkeypatch.setattr(engine, name, AsyncMock())
    async with db() as s:
        await s.execute(update(Job).where(Job.id == job_id).values(status="complete"))
        await completion_events.enqueue_job_complete(s, await s.get(Job, job_id), printer_id, source="manual", inventory=None)
        await s.commit()

    await settle_events(db)

    assert (await _counters(db)) == (1, 600)
    for name in ("_broadcast_job", "_fire_webhooks", "_fire_notifications"):
        getattr(engine, name).assert_not_awaited()


async def test_the_event_payload_is_validated(db):
    with pytest.raises(Exception, match="invalid payload"):
        await hub.enqueue_durable(None, EventEnvelope(name="job.complete", payload={"source": "carrier-pigeon"}))


async def test_migration_v046_backfills_completed_jobs_and_is_idempotent():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.migrations import v046_job_maintenance_accrued as mig

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT)"))
        await conn.execute(text("INSERT INTO jobs (id, status) VALUES (1, 'complete'), (2, 'printing'), (3, 'queued')"))
        await mig.up(conn)
        await mig.up(conn)
        rows = (await conn.execute(text("SELECT id, maintenance_accrued FROM jobs ORDER BY id"))).fetchall()
        assert [tuple(r) for r in rows] == [(1, 1), (2, 0), (3, 0)]
        await mig.down(conn)
        assert "maintenance_accrued" not in {r[1] for r in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    await engine.dispose()


async def test_a_deferred_deduction_is_durable_before_the_event_is_acknowledged(db, monkeypatch):
    """No start snapshot (e.g. a manual completion): the weight is read and the outbox row written inside the handler, so a crash
    before that redelivers the event, and a redelivery after it deducts nothing more."""
    from app.eventing import hub as hubmod
    from app.services.inventory import deduction as inventory_deduction
    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import spool, use_provider

    monkeypatch.setattr(hubmod, "BACKOFF_BASE_S", 60.0)
    engine, printer_id, job_id = await _seed_completing_job(db)
    fake = FakeInventoryProvider(spools=[spool("42", 300.0)])
    await use_provider(fake)
    real, calls = inventory_deduction.complete_deferred, {"n": 0}

    async def dies_once(factory, plan):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("process died before the outbox row was written")
        await real(factory, plan)

    monkeypatch.setattr(inventory_deduction, "complete_deferred", dies_once)
    await engine.handle_print_complete(printer_id)
    await hub.deliver_pending(db)

    (inv,) = [d for d in (await _events(db))[1] if d.subscriber == "core:job_complete.inventory"]
    assert inv.status == "pending" and inv.attempts == 1 and await _outbox_rows(db) == []      # not acknowledged, nothing deducted

    async def make_due():
        async with db() as s:
            await s.execute(update(EventDelivery).where(EventDelivery.status == "pending")
                            .values(next_attempt_at="2000-01-01T00:00:00.000000Z"))
            await s.commit()

    await make_due()
    await hub.deliver_pending(db)
    from app.services.inventory import tasks as inventory_tasks
    await inventory_tasks.drain()
    assert fake.writes == [("42", pytest.approx(282.5))] and len(await _outbox_rows(db)) == 1

    async with db() as s:                                         # and a redelivery after the acknowledgement was lost
        await s.execute(update(EventDelivery).values(status="pending", next_attempt_at="2000-01-01T00:00:00.000000Z"))
        await s.commit()
    await settle_events(db)
    assert fake.writes == [("42", pytest.approx(282.5))] and len(await _outbox_rows(db)) == 1


async def test_notices_are_dropped_with_a_log_not_silently_when_no_engine_is_bound(db, caplog, monkeypatch):
    monkeypatch.setattr(completion_events, "_engine", None)
    envelope = EventEnvelope(name="job.complete", entities={"job_id": 7}, payload={"source": "queue"})
    with caplog.at_level("WARNING", logger="app"):
        await completion_events.notify_consumers(envelope)
    assert "dropped: no queue engine is bound" in caplog.text
