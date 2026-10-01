"""Scheduled starts (jobs.not_before) and printer quiet hours, against the real QueueEngine + DB."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Printer
from app.services import scheduling
from tests.services.test_queue_ordering import cycle, engine, job_state, seed, world  # noqa: F401 (fixtures)


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


async def _set(factory, model_id, model, **values):
    async with factory() as s:
        row = await s.get(model, model_id)
        for k, v in values.items():
            setattr(row, k, v)
        await s.commit()


async def test_a_job_not_yet_due_is_skipped_without_blocking_the_jobs_behind_it(engine, world, session_factory):
    from app.models import Job
    held, behind = await seed(session_factory, {1: {}}, [
        {"position": 1.0, "printers": [1]},
        {"position": 2.0, "printers": [1]},
    ])
    await _set(session_factory, held, Job, not_before=_iso(timedelta(hours=2)))
    world.online = {1}

    await cycle(engine)

    assert (await job_state(session_factory, behind)).status == "printing"      # the later job ran
    h = await job_state(session_factory, held)
    assert (h.status, h.assigned_printer_id, h.block_reason) == ("queued", None, None)  # held, not blocked


async def test_a_scheduled_job_starts_once_its_time_has_passed(engine, world, session_factory):
    from app.models import Job
    (job,) = await seed(session_factory, {1: {}}, [{"position": 1.0, "printers": [1]}])
    await _set(session_factory, job, Job, not_before=_iso(timedelta(hours=1)))
    world.online = {1}
    await cycle(engine)
    assert (await job_state(session_factory, job)).status == "queued"

    await _set(session_factory, job, Job, not_before=_iso(timedelta(seconds=-5)))
    await cycle(engine)

    assert (await job_state(session_factory, job)).status == "printing"


async def test_offline_printers_do_not_pre_slice_a_job_that_is_not_due(engine, world, session_factory):
    from app.models import Job
    (job,) = await seed(session_factory, {1: {}}, [{"position": 1.0, "printers": [1]}])
    await _set(session_factory, job, Job, not_before=_iso(timedelta(hours=1)))
    world.online = set()          # printer known but offline → would normally slice ahead

    await engine._process_queue()

    assert (await job_state(session_factory, job)).status == "queued"
    world.slicer.slice.assert_not_called()


async def test_quiet_hours_stop_a_printer_starting_new_jobs(engine, world, session_factory):
    (job,) = await seed(session_factory, {1: {}}, [{"position": 1.0, "printers": [1]}])
    world.online = {1}
    now = datetime.now().astimezone()
    start = (now - timedelta(minutes=30)).strftime("%H:%M")
    end = (now + timedelta(minutes=30)).strftime("%H:%M")
    await _set(session_factory, 1, Printer, quiet_start=start, quiet_end=end)

    await cycle(engine)
    assert (await job_state(session_factory, job)).status == "queued"

    await _set(session_factory, 1, Printer, quiet_start=None, quiet_end=None)
    await cycle(engine)
    assert (await job_state(session_factory, job)).status == "printing"


async def test_engine_wakes_for_the_next_scheduled_start(engine, session_factory):
    from app.models import Job
    (job,) = await seed(session_factory, {1: {}}, [{"position": 1.0, "printers": [1]}])
    assert await engine._seconds_until_next_schedule() == float("inf")

    await _set(session_factory, job, Job, not_before=_iso(timedelta(seconds=90)))
    wait = await engine._seconds_until_next_schedule()
    assert 80 < wait <= 90

    await _set(session_factory, job, Job, not_before=_iso(timedelta(hours=-1)))      # already due: nothing to wait for
    assert await engine._seconds_until_next_schedule() == float("inf")


@pytest.mark.parametrize("start,end,now,expected", [
    ("22:00", "06:00", "23:30", True),     # wraps midnight, evening side
    ("22:00", "06:00", "05:59", True),     # wraps midnight, morning side
    ("22:00", "06:00", "06:00", False),    # end is exclusive
    ("22:00", "06:00", "12:00", False),
    ("09:00", "17:00", "09:00", True),     # start inclusive
    ("09:00", "17:00", "17:00", False),
    ("09:00", "09:00", "09:00", False),    # empty window
    (None, "06:00", "01:00", False),       # unset = no quiet hours
])
def test_in_quiet_hours(start, end, now, expected):
    h, m = map(int, now.split(":"))
    assert scheduling.in_quiet_hours(start, end, datetime(2026, 1, 1, h, m).astimezone()) is expected


def test_parse_helpers_reject_garbage_and_normalise_to_utc():
    with pytest.raises(ValueError):
        scheduling.parse_hhmm("25:00")
    with pytest.raises(ValueError):
        scheduling.parse_hhmm("9:00")
    assert scheduling.parse_not_before("2026-10-01T10:00:00+02:00") == "2026-10-01T08:00:00+00:00"
    assert scheduling.parse_not_before("2026-10-01T10:00:00") == "2026-10-01T10:00:00+00:00"   # naive = UTC
    assert scheduling.parse_not_before("2026-10-01T10:00:00Z") == "2026-10-01T10:00:00+00:00"
