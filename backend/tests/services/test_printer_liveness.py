"""Printer liveness (spec §2a): a printer offline for the user's cutoff is dead. Under the cutoff it is "undead": part of queue processing,
and a blip that heals triggers nothing unless work is waiting for it. Fake clock; real QueueEngine + PrinterManager + the test DB."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.eventing.hub import hub
from app.models import EventOutbox, GcodeFile, Job, JobDetail, Printer, QueueConfig, UploadedFile
from app.services import job_details as jd
from app.services.printer_manager import PrinterManager
from app.services.queue_engine import QueueEngine

NOW = "2026-10-20T12:00:00Z"
CUTOFF = 30


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, minutes: float) -> None:
        self.t += minutes * 60


def client(connected=True, idle=True) -> MagicMock:
    c = MagicMock()
    c.connected, c.is_idle = connected, idle
    return c


@pytest.fixture
async def env(session_factory, tmp_path):
    mgr, clock = PrinterManager(), Clock()
    mgr._clock = clock
    engine = QueueEngine(session_factory, mgr, MagicMock(), broadcast_cb=None)
    engine.wake = MagicMock()
    for name in ("_fire_webhooks", "_fire_notifications"):
        setattr(engine, name, AsyncMock())
    async with session_factory() as s:
        s.add(QueueConfig(id=1, check_interval_minutes=5, printer_dead_after_minutes=CUTOFF, snapshot_interval_seconds=2, estimates_enabled=False))
        await s.commit()
    failed = []

    async def on_failed(e):
        failed.append(e)

    hub.subscribe("job.failed", on_failed, name="t.liveness.failed")
    yield MagicMock(engine=engine, mgr=mgr, clock=clock, failed=failed, tmp=tmp_path)
    hub._core.pop("core:t.liveness.failed", None)
    engine._executor.shutdown(wait=False)


async def seed(session_factory, tmp_path, *, status="printing", position=5.0, extra_queued=True) -> tuple[int, int]:
    gcode = tmp_path / "j.gcode"
    gcode.write_text("G28\n")
    async with session_factory() as s:
        printer = Printer(name="P", printer_type="bambu", connection_config={}, orca_printer_profiles=[], awaiting_plate_clear=True)
        f = UploadedFile(original_filename="m.3mf", stored_path="/x", plates=[], uploaded_at=NOW)
        s.add_all([printer, f])
        await s.flush()
        job = Job(uploaded_file_id=f.id, plate_number=1, queue_position=position, status=status, created_at=NOW, updated_at=NOW,
                  assigned_printer_id=printer.id if status == "printing" else None, printed_on_printer_id=printer.id)
        s.add(job)
        await s.flush()
        s.add(GcodeFile(job_id=job.id, printer_id=printer.id, path=str(gcode)))
        if extra_queued:
            s.add(Job(uploaded_file_id=f.id, plate_number=1, queue_position=2.0, status="queued", created_at=NOW, updated_at=NOW))
        await s.commit()
        return job.id, printer.id


async def job_row(factory, job_id) -> Job:
    async with factory() as s:
        return await s.get(Job, job_id)


async def outbox(factory):
    async with factory() as s:
        return list((await s.execute(select(EventOutbox))).scalars())


async def test_an_offline_printer_under_the_cutoff_is_undead_and_nothing_happens(env, session_factory):
    job_id, pid = await seed(session_factory, env.tmp)
    c = client(connected=False)
    env.mgr.register_client(pid, c)
    env.clock.advance(5)

    await env.engine._liveness_pass()

    assert env.mgr.liveness(pid, CUTOFF) == "undead"
    job = await job_row(session_factory, job_id)
    assert (job.status, job.assigned_printer_id) == ("printing", pid)
    assert env.failed == [] and env.engine.wake.call_count == 0 and await outbox(session_factory) == []


async def test_crossing_the_cutoff_fails_the_running_job_and_requeues_it_at_the_top_once(env, session_factory):
    job_id, pid = await seed(session_factory, env.tmp, position=5.0)
    env.mgr.register_client(pid, client(connected=False))
    env.clock.advance(CUTOFF + 1)

    await env.engine._liveness_pass()
    await hub.drain()

    job = await job_row(session_factory, job_id)
    assert (job.status, job.assigned_printer_id, job.completed_at, job.block_reason) == ("queued", None, None, None)
    async with session_factory() as s:
        other = (await s.execute(select(Job).where(Job.id != job_id))).scalars().one()
    assert job.queue_position < other.queue_position                       # the top of the queue
    async with session_factory() as s:
        assert (await s.execute(select(GcodeFile))).first() is None        # the parked gcode is gone
        assert (await s.get(Printer, pid)).awaiting_plate_clear is True    # a half-printed part may be on the bed
    assert [(e.origin, e.entities["job_id"], e.payload["phase"]) for e in env.failed] == [("core", job_id, "print")]
    assert "offline for more than 30 minutes" in env.failed[0].payload["reason"]
    assert (await jd.get_details(job_id, ["last_failure"]))["last_failure"]["value"]["printer_id"] == pid
    assert env.mgr.liveness(pid, CUTOFF) == "dead" and env.engine.wake.call_count == 1

    await env.engine._liveness_pass()                                      # the same outage is declared once
    await hub.drain()
    assert len(env.failed) == 1 and env.engine._fire_webhooks.await_count == 1


async def test_a_heal_before_the_cutoff_triggers_nothing(env, session_factory):
    job_id, pid = await seed(session_factory, env.tmp)
    c = client(connected=False)
    env.mgr.register_client(pid, c)
    env.clock.advance(10)
    await env.engine._liveness_pass()
    c.connected = True

    await env.engine._liveness_pass()

    assert env.engine.wake.call_count == 0 and env.failed == []
    assert env.mgr.liveness(pid, CUTOFF) == "online"
    assert (await job_row(session_factory, job_id)).status == "printing"


async def test_a_dead_printer_coming_back_wakes_the_queue_once_and_does_not_replay_the_failure(env, session_factory):
    job_id, pid = await seed(session_factory, env.tmp)
    c = client(connected=False)
    env.mgr.register_client(pid, c)
    env.clock.advance(CUTOFF + 1)
    await env.engine._liveness_pass()
    env.engine.wake.reset_mock()
    c.connected = True

    await env.engine._liveness_pass()
    await env.engine._liveness_pass()
    await hub.drain()

    assert env.engine.wake.call_count == 1 and len(env.failed) == 1
    assert (await job_row(session_factory, job_id)).status == "queued"


async def test_a_cutoff_of_zero_never_declares_a_printer_dead(env, session_factory):
    job_id, pid = await seed(session_factory, env.tmp)
    async with session_factory() as s:
        (await s.get(QueueConfig, 1)).printer_dead_after_minutes = 0
        await s.commit()
    env.mgr.register_client(pid, client(connected=False))
    env.clock.advance(60 * 24 * 7)

    await env.engine._liveness_pass()

    assert (await job_row(session_factory, job_id)).status == "printing" and env.failed == []
    assert env.mgr.liveness(pid, 0) == "undead" and env.mgr.seconds_until_next_dead(0) is None


async def test_a_late_completion_and_the_dead_sweep_have_exactly_one_winner(env, session_factory):
    job_id, pid = await seed(session_factory, env.tmp)
    assert await env.engine.complete_job(job_id, pid, origin="core") is True

    assert await env.engine.requeue_dead_print(job_id, pid, "offline") is False

    assert (await job_row(session_factory, job_id)).status == "complete" and len(await outbox(session_factory)) == 1
    await hub.drain()
    assert env.failed == []


async def test_the_wait_shortens_to_the_next_dead_deadline(env, session_factory):
    _, pid = await seed(session_factory, env.tmp)
    assert env.mgr.seconds_until_next_dead(CUTOFF) is None                 # nobody is offline
    env.mgr.register_client(pid, client(connected=False))
    env.clock.advance(10)
    assert env.mgr.seconds_until_next_dead(CUTOFF) == pytest.approx(20 * 60)
    env.clock.advance(25)
    assert env.mgr.seconds_until_next_dead(CUTOFF) == 1.0                  # overdue: floored, never a hot loop
    await env.engine._liveness_pass()
    assert env.mgr.seconds_until_next_dead(CUTOFF) is None                 # declared: not waited for again


async def test_an_undead_printer_with_a_sliced_job_is_woken_when_it_returns_and_the_ready_path_starts_it(env, session_factory):
    job_id, pid = await seed(session_factory, env.tmp, status="sliced", extra_queued=False)
    async with session_factory() as s:
        (await s.get(Printer, pid)).awaiting_plate_clear = False
        await s.commit()
    c = client(connected=False)
    env.mgr.register_client(pid, c)
    env.clock.advance(10)
    await env.engine._liveness_pass()
    assert env.engine.wake.call_count == 0
    c.connected = True

    await env.engine._liveness_pass()

    assert env.engine.wake.call_count == 1                                 # work is waiting: wake, even though the blip healed in time
    env.engine._try_resume_sliced_job = AsyncMock(return_value=True)
    env.engine._reconcile_printing_jobs = AsyncMock()
    await env.engine._process_queue()
    assert [call.args[1] for call in env.engine._try_resume_sliced_job.await_args_list] == [pid]     # idle + plate clear: it is picked up


async def test_the_returning_printer_is_not_started_while_it_is_busy_or_awaiting_the_plate(env, session_factory):
    job_id, pid = await seed(session_factory, env.tmp, status="sliced", extra_queued=False)   # plate gate still set
    c = client(connected=False)
    env.mgr.register_client(pid, c)
    env.mgr.set_awaiting_plate_clear(pid, True)
    c.connected = True
    await env.engine._liveness_pass()
    assert env.engine.wake.call_count == 1
    env.engine._try_resume_sliced_job = AsyncMock(return_value=True)
    env.engine._reconcile_printing_jobs = AsyncMock()

    await env.engine._process_queue()

    assert env.engine._try_resume_sliced_job.await_count == 0              # not ready: the job waits as `sliced`
    assert (await job_row(session_factory, job_id)).status == "sliced"


async def test_a_printer_with_no_pending_work_that_heals_does_not_wake_the_queue(env, session_factory):
    _, pid = await seed(session_factory, env.tmp, status="queued", extra_queued=False)
    c = client(connected=False)
    env.mgr.register_client(pid, c)
    c.connected = True
    await env.engine._liveness_pass()
    assert env.engine.wake.call_count == 0


async def test_a_dead_printer_gives_back_a_job_sliced_for_it_without_a_failure(env, session_factory):
    job_id, pid = await seed(session_factory, env.tmp, status="sliced", position=3.0, extra_queued=False)
    env.mgr.register_client(pid, client(connected=False))
    env.clock.advance(CUTOFF + 1)

    await env.engine._liveness_pass()
    await hub.drain()

    job = await job_row(session_factory, job_id)
    assert (job.status, job.queue_position) == ("queued", 3.0)             # back where it was, claimable by another printer
    async with session_factory() as s:
        assert (await s.execute(select(GcodeFile))).first() is None
    assert env.failed == []                                                # it never started: not a failure
