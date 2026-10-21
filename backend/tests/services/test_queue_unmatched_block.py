"""A job nothing in the fleet could take is `blocked` (spec job.blocked): requeue-and-wait cannot fix it. Offline printers under the user's
cutoff ("undead") still count; dead, disabled and queue-off ones do not. Real QueueEngine + PrinterManager (fake clock) + the test DB."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.models import Job, JobPrinterConfig, Printer, QueueConfig, UploadedFile
from app.services.printer_manager import PrinterManager
from app.services.queue_engine import NO_MATCH_PREFIX, QueueEngine

NOW = "2026-10-20T12:00:00Z"
CUTOFF = 30
PA_CF = [{"slot": 0, "type": "PA-CF", "subtype": "standard", "color": "#000000"}]
PLA = [{"slot": 0, "type": "PLA", "subtype": "standard", "color": "#FFFFFF"}]


class Clock:
    t = 1000.0

    def __call__(self) -> float:
        return self.t


def client(connected=True, idle=True) -> MagicMock:
    c = MagicMock()
    c.connected, c.is_idle = connected, idle
    c.file_upload_supported = False
    return c


@pytest.fixture
async def env(session_factory, listen):
    mgr, clock = PrinterManager(), Clock()
    mgr._clock = clock
    engine = QueueEngine(session_factory, mgr, MagicMock(), broadcast_cb=None)
    engine.wake = MagicMock()
    engine._fire_webhooks, engine._fire_notifications = AsyncMock(), AsyncMock()
    engine._run_slice_and_print = AsyncMock()
    async with session_factory() as s:
        s.add(QueueConfig(id=1, check_interval_minutes=5, printer_dead_after_minutes=CUTOFF, snapshot_interval_seconds=2, estimates_enabled=False))
        f = UploadedFile(original_filename="m.3mf", stored_path="/x", plates=[], uploaded_at=NOW)
        s.add(f)
        await s.commit()
        file_id = f.id
    yield MagicMock(engine=engine, mgr=mgr, clock=clock, file_id=file_id, blocked=listen("job.blocked"))
    engine._executor.shutdown(wait=False)


async def add_printer(env, factory, pid, loaded, *, connected=True, queue_on=True, enabled=True) -> MagicMock:
    async with factory() as s:
        s.add(Printer(id=pid, name=f"P{pid}", printer_type="bambu", connection_config={}, orca_printer_profiles=[], loaded_filaments=loaded, machine_profile_override="Test Machine Preset",
                      queue_on=queue_on, enabled=enabled))
        await s.commit()
    c = client(connected=connected)
    env.mgr.register_client(pid, c)
    return c


async def add_job(env, factory, printers, *, ask_type="PA-CF", ask_color="#000000", position=1.0, status="queued", **fields) -> int:
    async with factory() as s:
        job = Job(uploaded_file_id=env.file_id, plate_number=1, queue_position=position, status=status, created_at=NOW, updated_at=NOW, **fields)
        s.add(job)
        await s.flush()
        for pid in printers:
            s.add(JobPrinterConfig(job_id=job.id, printer_id=pid, print_profile="p", ask_type=ask_type, ask_subtype="any", ask_color=ask_color))
        await s.commit()
        return job.id


async def job(factory, job_id) -> Job:
    async with factory() as s:
        return await s.get(Job, job_id)


async def test_a_job_no_online_printer_has_the_filament_for_is_blocked_once_with_a_reason_and_a_notice(env, session_factory):
    await add_printer(env, session_factory, 1, PLA)
    job_id = await add_job(env, session_factory, [1])

    await env.engine._sweep_unmatched_jobs()
    await env.engine._sweep_unmatched_jobs()                                   # the next cycle: same reason, nothing new
    from app.eventing.hub import hub
    await hub.drain()

    j = await job(session_factory, job_id)
    assert j.status == "blocked" and j.block_reason.startswith(NO_MATCH_PREFIX) and "PA-CF" in j.block_reason
    assert [(e.origin, e.entities["job_id"], e.payload["phase"]) for e in env.blocked] == [("core", job_id, "queue")]
    assert env.engine._fire_webhooks.await_count == 1 and env.engine._fire_notifications.await_count == 1


async def test_a_job_that_needs_a_model_no_printer_has_is_blocked(env, session_factory):
    await add_printer(env, session_factory, 1, PLA)
    job_id = await add_job(env, session_factory, [])                           # no printer is compatible: nothing to match

    await env.engine._sweep_unmatched_jobs()

    assert "no printer in the fleet is compatible" in (await job(session_factory, job_id)).block_reason


async def test_a_busy_or_plate_uncleared_printer_with_the_filament_still_counts(env, session_factory):
    c = await add_printer(env, session_factory, 1, PA_CF)
    c.is_idle = False                                                          # printing something else right now
    env.mgr.set_awaiting_plate_clear(1, True)
    job_id = await add_job(env, session_factory, [1])

    await env.engine._sweep_unmatched_jobs()

    assert (await job(session_factory, job_id)).status == "queued" and env.blocked == []


async def test_an_undead_offline_printer_counts_but_a_dead_one_does_not(env, session_factory):
    c = await add_printer(env, session_factory, 1, PA_CF, connected=False)
    job_id = await add_job(env, session_factory, [1])
    env.clock.t += 10 * 60                                                     # offline 10 of 30 minutes: undead

    await env.engine._sweep_unmatched_jobs()
    assert (await job(session_factory, job_id)).status == "queued" and env.blocked == []

    env.clock.t += 25 * 60                                                     # past the cutoff: dead, so it can no longer take the job
    await env.engine._liveness_pass()
    await env.engine._sweep_unmatched_jobs()
    assert (await job(session_factory, job_id)).status == "blocked"

    c.connected = True                                                         # it comes back: the liveness pass wakes the queue ...
    await env.engine._liveness_pass()
    assert env.engine.wake.call_count >= 1
    await env.engine._sweep_unmatched_jobs()                                   # ... and the next sweep reopens the job
    assert (await job(session_factory, job_id)).status == "queued"


@pytest.mark.parametrize("kw", [{"queue_on": False}, {"enabled": False}], ids=["queue-off", "disabled"])
async def test_a_queue_off_or_disabled_printer_still_counts_because_the_operator_can_switch_it_back(env, session_factory, kw):
    await add_printer(env, session_factory, 1, PA_CF, **kw)
    job_id = await add_job(env, session_factory, [1])

    await env.engine._sweep_unmatched_jobs()

    assert (await job(session_factory, job_id)).status == "queued" and env.blocked == []        # toggling a queue must not block and announce jobs


async def test_a_printer_with_no_client_yet_counts_so_a_restart_blocks_nothing(env, session_factory):
    async with session_factory() as s:                                                          # a row, but its client is not registered (just booted)
        s.add(Printer(id=1, name="P1", printer_type="bambu", connection_config={}, orca_printer_profiles=[], loaded_filaments=PA_CF))
        await s.commit()
    job_id = await add_job(env, session_factory, [1])

    await env.engine._sweep_unmatched_jobs()

    assert (await job(session_factory, job_id)).status == "queued" and env.blocked == []


async def test_a_dead_printer_counts_again_when_the_limit_is_switched_off(env, session_factory):
    await add_printer(env, session_factory, 1, PA_CF, connected=False)
    job_id = await add_job(env, session_factory, [1])
    env.clock.t += 60 * 60
    await env.engine._liveness_pass()
    await env.engine._sweep_unmatched_jobs()
    assert (await job(session_factory, job_id)).status == "blocked"

    async with session_factory() as s:
        (await s.get(QueueConfig, 1)).printer_dead_after_minutes = 0
        await s.commit()
    await env.engine._liveness_pass()
    await env.engine._sweep_unmatched_jobs()

    assert (await job(session_factory, job_id)).status == "queued"                             # "never dead": the offline printer counts again


async def test_a_reworded_reason_updates_the_block_quietly_instead_of_announcing_it_again(env, session_factory):
    await add_printer(env, session_factory, 1, PLA)
    job_id = await add_job(env, session_factory, [1])
    await env.engine._sweep_unmatched_jobs()
    first_reason = (await job(session_factory, job_id)).block_reason
    async with session_factory() as s:                                                          # the slot's colour changes: the reason text differs, the block does not
        (await s.get(Printer, 1)).loaded_filaments = [{"slot": 0, "type": "PETG", "subtype": "standard", "color": "#FF0000"}]
        await s.commit()

    await env.engine._sweep_unmatched_jobs()
    from app.eventing.hub import hub
    await hub.drain()

    j = await job(session_factory, job_id)
    assert j.status == "blocked" and j.block_reason.startswith(NO_MATCH_PREFIX) and j.block_reason != first_reason
    assert len(env.blocked) == 1 and env.engine._fire_webhooks.await_count == 1


async def test_a_printer_skipped_by_the_claim_loop_still_has_its_model_targets_materialized_before_the_sweep(env, session_factory):
    from app.models import JobModelTarget, PrinterModelRecord
    async with session_factory() as s:
        s.add(PrinterModelRecord(id="m1", plugin_id="bambu", manufacturer_id="bambu", model_id="m1", display_name="M1"))
        s.add(Printer(id=1, name="P1", printer_type="bambu", connection_config={}, orca_printer_profiles=[], loaded_filaments=PA_CF,
                      model_uuid="m1", quiet_start="00:00", quiet_end="23:59"))             # quiet hours: the claim loop skips it
        await s.commit()
    env.mgr.register_client(1, client())
    async with session_factory() as s:
        j = Job(uploaded_file_id=env.file_id, plate_number=1, queue_position=1.0, status="queued", created_at=NOW, updated_at=NOW)
        s.add(j)
        await s.flush()
        s.add(JobModelTarget(job_id=j.id, model_uuid="m1", print_profile="p", ask_type="PA-CF", ask_subtype="any"))
        await s.commit()
        job_id = j.id

    await env.engine._sweep_unmatched_jobs()

    assert (await job(session_factory, job_id)).status == "queued" and env.blocked == []      # a match exists: P1 has a config now


async def test_loading_the_filament_or_adding_a_matching_printer_releases_the_job(env, session_factory):
    await add_printer(env, session_factory, 1, PLA)
    job_id = await add_job(env, session_factory, [1])
    await env.engine._sweep_unmatched_jobs()
    assert (await job(session_factory, job_id)).status == "blocked"

    async with session_factory() as s:
        (await s.get(Printer, 1)).loaded_filaments = PA_CF
        await s.commit()
    await env.engine._sweep_unmatched_jobs()

    j = await job(session_factory, job_id)
    assert (j.status, j.block_reason) == ("queued", None)
    from app.eventing.hub import hub
    await hub.drain()
    assert len(env.blocked) == 1                                               # releasing is not a new `job.blocked`


async def test_a_block_with_another_cause_is_left_alone_and_a_slice_failed_candidate_is_not_a_no_match(env, session_factory):
    await add_printer(env, session_factory, 1, PLA)
    other = await add_job(env, session_factory, [1], status="blocked", block_reason="slicing failed: x")
    failed = await add_job(env, session_factory, [1])
    async with session_factory() as s:
        cfg = (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == failed))).scalar_one()
        cfg.slice_failed = True
        await s.commit()

    await env.engine._sweep_unmatched_jobs()

    assert (await job(session_factory, other)).block_reason == "slicing failed: x"            # not ours: untouched
    assert (await job(session_factory, failed)).status == "queued" and env.blocked == []      # the slice-failure path owns that one


async def test_one_match_among_several_printers_is_enough(env, session_factory):
    await add_printer(env, session_factory, 1, PLA)
    await add_printer(env, session_factory, 2, PA_CF)
    job_id = await add_job(env, session_factory, [1, 2])

    await env.engine._sweep_unmatched_jobs()

    assert (await job(session_factory, job_id)).status == "queued"


async def test_a_blocked_job_does_not_hold_up_the_job_behind_it(env, session_factory):
    await add_printer(env, session_factory, 1, PLA)
    stuck = await add_job(env, session_factory, [1], position=1.0)                           # needs PA-CF: nobody has it
    behind = await add_job(env, session_factory, [1], ask_type="PLA", ask_color="#FFFFFF", position=2.0)

    await env.engine._process_queue()

    assert (await job(session_factory, stuck)).status == "blocked"
    b = await job(session_factory, behind)
    assert (b.status, b.assigned_printer_id, b.block_reason) == ("slicing", 1, None)                                # claimed on the ready printer


async def test_running_not_due_and_unpromoted_jobs_are_not_evaluated(env, session_factory):
    await add_printer(env, session_factory, 1, PLA)
    printing = await add_job(env, session_factory, [1], status="printing", assigned_printer_id=1)
    later = await add_job(env, session_factory, [1], not_before="2999-01-01T00:00:00Z")

    await env.engine._sweep_unmatched_jobs()

    assert (await job(session_factory, printing)).status == "printing"
    assert (await job(session_factory, later)).status == "queued" and env.blocked == []


async def test_every_other_block_publishes_job_blocked_too(env, session_factory):
    """`_block_job` is the single place a queue block is written: it publishes one `job.blocked` (no webhook for a per-printer cause)."""
    await add_printer(env, session_factory, 1, PLA)
    job_id = await add_job(env, session_factory, [1], ask_type="PLA", ask_color="#FFFFFF")
    async with session_factory() as s:
        j = await s.get(Job, job_id)
        await env.engine._block_job(s, j, "slicing provider is down")
        await env.engine._block_job(s, j, "slicing provider is down")                   # same reason again: one event
    from app.eventing.hub import hub
    await hub.drain()
    assert [e.payload for e in env.blocked] == [{"reason": "slicing provider is down", "phase": "queue"}]
    assert env.engine._fire_webhooks.await_count == 0
