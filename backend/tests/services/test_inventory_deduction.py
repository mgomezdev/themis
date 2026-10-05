"""Deduction model (BIZ-218): print-start snapshot -> absolute `pre - spent` write through the outbox; suspension when no
starting weight exists. Provider-facing behaviour is driven through a FakeInventoryProvider."""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import (
    InventoryPendingWrite, InventorySpoolStatus, Job, JobSpoolSnapshot, Printer, UploadedFile, WebhookConfig,
)
from app.plugins.kinds.filament_inventory import InvSpool, InventoryProviderError
from app.services.inventory import deduction, outbox, snapshots, tasks
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import spool, use_provider

NOW = datetime.now(timezone.utc).isoformat()


async def _job(factory) -> int:
    async with factory() as session:
        if await session.get(Printer, 1) is None:
            session.add(Printer(id=1, name="P1", printer_type="elegoo_centauri", connection_config={}))
            await session.flush()
        f = UploadedFile(original_filename="a.3mf", stored_path="/x/a.3mf", plates=[], uploaded_at=NOW)
        session.add(f)
        await session.flush()
        j = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status="printing", created_at=NOW, updated_at=NOW)
        session.add(j)
        await session.commit()
        return j.id


async def _rows(factory, model=InventoryPendingWrite):
    async with factory() as session:
        return list((await session.execute(select(model).order_by(model.id if hasattr(model, "id") else None))).scalars())


async def _complete(factory, job_id, ref, grams, fake_source="queue"):
    """What the completion transaction does: plan inside the transaction, then act after commit."""
    async with factory() as session:
        job = await session.get(Job, job_id)
        plan = await deduction.plan_completion(session, job=job, printer_id=1, spool_ref=ref, grams=grams, source=fake_source)
        await session.commit()
    deduction.after_commit(plan, factory)
    await tasks.drain()
    return plan


# ---- snapshots -------------------------------------------------------------------------------------------------------

async def test_snapshot_prefers_the_newest_pending_target_over_the_live_reading(session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 500.0)])
    await use_provider(fake)
    job_id = await _job(session_factory)
    async with session_factory() as s:
        outbox.enqueue(s, "spoolman", "1", 400.0, job_id=None, printer_id=None, source="queue")
        outbox.enqueue(s, "spoolman", "1", 350.0, job_id=None, printer_id=None, source="queue")
        await s.commit()

    await snapshots.take(session_factory, job_id, 1, "1")

    (snap,) = await _rows(session_factory, JobSpoolSnapshot)
    assert (snap.pre_weight_g, snap.source) == (350.0, "pending")
    assert "get_spool" not in fake.calls                      # the provider was never asked


async def test_snapshot_reads_the_live_weight_and_is_idempotent(session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 500.0)])
    await use_provider(fake)
    job_id = await _job(session_factory)

    await snapshots.take(session_factory, job_id, 1, "1")
    fake.spools["1"].remaining_g = 10.0                       # a second take must not overwrite the start weight
    await snapshots.take(session_factory, job_id, 1, "1")

    (snap,) = await _rows(session_factory, JobSpoolSnapshot)
    assert (snap.pre_weight_g, snap.source, snap.provider) == (500.0, "live", "spoolman")


@pytest.mark.parametrize("case", ["unreachable", "unweighed", "unknown"])
async def test_snapshot_without_an_obtainable_weight_is_recorded_as_missing(session_factory, case):
    fake = FakeInventoryProvider(spools=[InvSpool(ref="1", remaining_g=None, label="x")])
    if case == "unreachable":
        fake.fail_with = InventoryProviderError("down", code="503", status=503)
    await use_provider(fake)
    job_id = await _job(session_factory)

    await snapshots.take(session_factory, job_id, 1, "999" if case == "unknown" else "1")

    (snap,) = await _rows(session_factory, JobSpoolSnapshot)
    assert (snap.pre_weight_g, snap.source) == (None, "missing")


# ---- completion: the absolute write ----------------------------------------------------------------------------------

async def test_completion_writes_snapshot_minus_spent_clamped_at_zero(session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0), spool("2", 5.0)])
    await use_provider(fake)
    a, b = await _job(session_factory), await _job(session_factory)
    await snapshots.take(session_factory, a, 1, "1")
    await snapshots.take(session_factory, b, 1, "2")

    await _complete(session_factory, a, "1", 17.5)
    await _complete(session_factory, b, "2", 20.0)

    assert fake.writes == [("1", pytest.approx(82.5)), ("2", 0.0)]


async def test_flushing_twice_leaves_one_value_and_one_write(session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake)
    job_id = await _job(session_factory)
    await snapshots.take(session_factory, job_id, 1, "1")
    await _complete(session_factory, job_id, "1", 10.0)

    assert await outbox.flush(session_factory) == 0           # already applied: nothing to resend
    assert fake.writes == [("1", 90.0)]
    (row,) = await _rows(session_factory)
    assert (row.status, row.target_g, row.job_id) == ("applied", 90.0, job_id)


async def test_two_jobs_on_one_spool_while_offline_end_at_the_newest_target_and_supersede_the_older(session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 500.0)])
    await use_provider(fake)
    a, b = await _job(session_factory), await _job(session_factory)
    await snapshots.take(session_factory, a, 1, "1")           # 500 (live)
    fake.fail_with = InventoryProviderError("down", code="503", status=503)

    await _complete(session_factory, a, "1", 20.0)             # target 480 queued; the write fails
    await snapshots.take(session_factory, b, 1, "1")           # starts from the pending 480, not the stale live 500
    await _complete(session_factory, b, "1", 20.0)             # target 460 queued
    assert fake.writes == []
    pending = await _rows(session_factory)
    assert [(r.target_g, r.status) for r in pending] == [(480.0, "pending"), (460.0, "pending")]
    assert pending[1].attempts >= 1 and pending[1].last_error

    fake.fail_with = None                                      # the provider recovers
    await outbox.flush(session_factory)

    assert fake.writes == [("1", 460.0)]                       # one write: the newest target
    assert [(r.target_g, r.status) for r in await _rows(session_factory)] == [(480.0, "superseded"), (460.0, "applied")]


async def test_a_failed_write_stays_pending_and_is_retried_by_the_next_flush(session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake)
    job_id = await _job(session_factory)
    await snapshots.take(session_factory, job_id, 1, "1")
    fake.fail_with = InventoryProviderError("down", code="503", status=503)
    await _complete(session_factory, job_id, "1", 10.0)
    assert (await _rows(session_factory))[0].status == "pending"

    fake.fail_with = None
    assert await outbox.flush(session_factory) == 1
    assert (await _rows(session_factory))[0].status == "applied" and fake.writes == [("1", 90.0)]


async def test_rows_of_an_inactive_provider_are_left_alone(session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake, plugin_id="other_inventory")
    async with session_factory() as s:
        outbox.enqueue(s, "spoolman", "1", 50.0, job_id=None, printer_id=None, source="queue")
        await s.commit()

    assert await outbox.flush(session_factory) == 0
    assert fake.writes == [] and (await _rows(session_factory))[0].status == "pending"


async def test_deferred_completion_takes_the_weight_at_completion(session_factory):
    """No start snapshot (job already printing at upgrade / manual completion): live weight - spent, via the host."""
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake)
    job_id = await _job(session_factory)

    plan = await _complete(session_factory, job_id, "1", 8.0, "manual_complete")

    assert plan.kind == "deferred"
    assert fake.writes == [("1", 92.0)]
    (row,) = await _rows(session_factory)
    assert (row.source, row.status) == ("manual_complete", "applied")


# ---- suspension ------------------------------------------------------------------------------------------------------

async def _webhook(factory):
    async with factory() as s:
        s.add(WebhookConfig(id=1, url="http://hook.test", secret=None, events=[]))
        await s.commit()


async def test_a_missing_snapshot_suspends_tracking_once_and_later_prints_are_skipped_and_flagged(session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    await use_provider(fake)
    await _webhook(session_factory)
    a, b = await _job(session_factory), await _job(session_factory)
    async with session_factory() as s:                         # the start snapshot found no weight
        s.add(JobSpoolSnapshot(job_id=a, printer_id=1, provider="spoolman", spool_ref="1", pre_weight_g=None,
                               source="missing", taken_at=NOW))
        await s.commit()

    with patch("app.services.webhook_service.schedule") as hook:
        await _complete(session_factory, a, "1", 10.0)
        await _complete(session_factory, b, "1", 10.0)         # later print on the suspended spool

    assert fake.writes == [] and await _rows(session_factory) == []
    (status,) = await _status(session_factory)
    assert (status.provider, status.spool_ref, status.tracking, status.job_id) == ("spoolman", "1", "suspended", a)
    events = [c.args[2] for c in hook.call_args_list]
    assert events == ["inventory.tracking_unavailable"]       # exactly one event, however many prints follow
    async with session_factory() as s:
        for jid in (a, b):
            job = await s.get(Job, jid)
            assert job.deduction_skipped is True and job.deduction_note


async def _status(factory):
    async with factory() as s:
        return list((await s.execute(select(InventorySpoolStatus))).scalars())


async def test_a_deferred_completion_that_cannot_read_a_weight_suspends(session_factory):
    fake = FakeInventoryProvider(spools=[InvSpool(ref="1", remaining_g=None, label="x")])
    await use_provider(fake)
    job_id = await _job(session_factory)

    await _complete(session_factory, job_id, "1", 10.0)

    assert fake.writes == [] and len(await _status(session_factory)) == 1
    async with session_factory() as s:
        assert (await s.get(Job, job_id)).deduction_skipped is True


async def test_restore_clears_the_suspension_and_emits_restored_once(session_factory):
    await use_provider(FakeInventoryProvider(spools=[spool("1", 100.0)]))
    await _webhook(session_factory)
    job_id = await _job(session_factory)
    async with session_factory() as s:
        await deduction.suspend(s, "spoolman", "1", "no weight", await s.get(Job, job_id))
        await s.commit()

    with patch("app.services.webhook_service.schedule") as hook:
        async with session_factory() as s:
            assert await deduction.restore(s, "spoolman", "1") is True
            assert await deduction.restore(s, "spoolman", "1") is False       # nothing left to restore
            await s.commit()

    assert [c.args[2] for c in hook.call_args_list] == ["inventory.tracking_restored"]
    assert await _status(session_factory) == []


@pytest.mark.parametrize("caps,expected", [(frozenset({"TRACKS_WEIGHT", "WRITE_WEIGHT"}), True),
                                            (frozenset({"TRACKS_WEIGHT"}), False), (frozenset({"WRITE_WEIGHT"}), False)])
async def test_can_deduct_needs_both_weight_capabilities(session_factory, caps, expected):
    assert deduction.can_deduct() is False                                    # no provider
    await use_provider(FakeInventoryProvider(capabilities=caps))
    assert deduction.can_deduct() is expected


async def test_suspending_an_already_suspended_spool_emits_no_second_event_but_still_flags_the_job(session_factory):
    await use_provider(FakeInventoryProvider(spools=[spool("1", 100.0)]))
    await _webhook(session_factory)
    a, b = await _job(session_factory), await _job(session_factory)

    with patch("app.services.webhook_service.schedule") as hook:
        async with session_factory() as s:
            assert await deduction.suspend(s, "spoolman", "1", "first", await s.get(Job, a)) is True
            assert await deduction.suspend(s, "spoolman", "1", "again", await s.get(Job, b)) is False
            await s.commit()

    assert len(hook.call_args_list) == 1
    async with session_factory() as s:
        assert (await s.get(Job, b)).deduction_note == "again"
        assert (await _status(session_factory))[0].job_id == a             # the original suspension is kept
