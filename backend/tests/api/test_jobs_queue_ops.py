"""POST /jobs/{id}/reorder and /cancel — every action/edge and the per-status cancel cleanup."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models import GcodeFile, Job, UploadedFile
from app.services.printer_manager import printer_manager

_T = "2026-01-01T00:00:00"


@pytest.fixture
def seed(session_factory, create_printer):
    """`await seed([("queued", 1.0), ...])` -> job ids. Rows go straight into the DB so positions are exact."""
    async def _seed(specs, **extra) -> list[int]:
        printer_id = await create_printer()
        async with session_factory() as s:
            f = UploadedFile(original_filename="x.3mf", stored_path="/t/x.3mf", plates=[], uploaded_at=_T)
            s.add(f)
            await s.flush()
            jobs = [Job(uploaded_file_id=f.id, plate_number=1, status=status, queue_position=pos,
                        created_at=_T, updated_at=_T, **({"assigned_printer_id": printer_id} | extra))
                    for status, pos in specs]
            s.add_all(jobs)
            await s.commit()
            return [j.id for j in jobs]
    return _seed


async def _order(client, statuses=("queued", "blocked")) -> list[int]:
    """Ids of the reorderable jobs, front of the queue first."""
    rows = (await client.get("/api/v1/jobs")).json()
    return [r["id"] for r in rows if r["status"] in statuses]


async def _reorder(client, job_id: int, action: str):
    with patch("app.api.routes.jobs.queue_engine") as engine:
        resp = await client.post(f"/api/v1/jobs/{job_id}/reorder", json={"action": action})
    return resp, engine


# ---------------------------------------------------------------------------
# reorder
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action, target, expected, new_position", [
    ("promote", 2, [0, 2, 1, 3], 1.5),   # C lands halfway between A(1.0) and B(2.0)
    ("promote", 1, [1, 0, 2, 3], 0.0),   # second job goes one unit ahead of the first
    ("promote", 0, [0, 1, 2, 3], 1.0),   # already first: no-op
    ("demote", 1, [0, 2, 1, 3], 3.5),    # B lands halfway between C(3.0) and D(4.0)
    ("demote", 2, [0, 1, 3, 2], 5.0),    # second-to-last goes one unit behind the last
    ("demote", 3, [0, 1, 2, 3], 4.0),    # already last: no-op
    ("front", 3, [3, 0, 1, 2], 0.0),
    ("back", 0, [1, 2, 3, 0], 5.0),
])
async def test_reorder_moves_the_job_to_the_exact_slot_and_leaves_the_others_where_they_are(
        client, seed, action, target, expected, new_position):
    ids = await seed([("queued", 1.0), ("queued", 2.0), ("queued", 3.0), ("queued", 4.0)])

    resp, engine = await _reorder(client, ids[target], action)

    assert resp.status_code == 200
    positions = {j["id"]: j["queue_position"] for j in (await client.get("/api/v1/jobs")).json()}
    assert positions[ids[target]] == new_position == resp.json()["queue_position"]
    assert {i: positions[i] for k, i in enumerate(ids) if k != target} == {
        i: float(k + 1) for k, i in enumerate(ids) if k != target}  # nobody else was renumbered
    assert await _order(client) == [ids[i] for i in expected]  # and no ties: the order is what was asked for
    engine.wake.assert_called_once_with()


async def test_reorder_treats_blocked_jobs_like_queued_ones_and_ignores_the_rest(client, seed):
    printing, a, blocked, b = await seed([("printing", 1.0), ("queued", 2.0), ("blocked", 3.0), ("queued", 4.0)])

    resp, _ = await _reorder(client, b, "promote")
    assert resp.status_code == 200
    assert await _order(client) == [a, b, blocked]  # blocked counts as a neighbour

    resp, _ = await _reorder(client, blocked, "front")
    assert resp.status_code == 200
    assert await _order(client) == [blocked, a, b]
    # the printing job is not part of the reorderable queue and did not move
    printing_row = next(j for j in (await client.get("/api/v1/jobs")).json() if j["id"] == printing)
    assert (printing_row["status"], printing_row["queue_position"]) == ("printing", 1.0)


@pytest.mark.parametrize("action", ["promote", "demote", "front", "back"])
async def test_reorder_of_the_only_job_keeps_it_the_only_job(client, seed, action):
    (only,) = await seed([("queued", 1.0)])

    resp, _ = await _reorder(client, only, action)

    assert resp.status_code == 200
    assert await _order(client) == [only]


async def test_reorder_rejects_unknown_action_and_unknown_job(client, seed):
    a, b = await seed([("queued", 1.0), ("queued", 2.0)])

    bad_action, engine = await _reorder(client, b, "sideways")
    missing, _ = await _reorder(client, 999999, "front")

    assert (bad_action.status_code, bad_action.json()["detail"]) == (422, "Invalid action 'sideways'")
    assert missing.status_code == 404
    assert await _order(client) == [a, b]
    engine.wake.assert_not_called()


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["queued", "blocked", "slicing", "sliced", "uploading", "printing", "paused", "failed"])
async def test_cancel_clears_the_job_in_every_cancellable_status(client, seed, status):
    (job_id,) = await seed([(status, 1.0)])

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post(f"/api/v1/jobs/{job_id}/cancel")

    assert resp.status_code == 200
    body = resp.json()
    assert (body["status"], body["assigned_printer_id"], body["queue_position"]) == ("cancelled", None, None)
    assert body["completed_at"] is not None
    assert await _order(client, statuses=("queued", "blocked", "slicing", "sliced", "uploading",
                                          "printing", "paused", "failed")) == []  # off the active queue


@pytest.mark.parametrize("status, stops_printer", [
    ("printing", True), ("paused", True), ("uploading", True),
    ("slicing", False), ("sliced", False), ("queued", False), ("blocked", False), ("failed", False),
])
async def test_cancel_stops_the_printer_only_when_it_is_physically_running_the_job(client, seed, status, stops_printer):
    (job_id,) = await seed([(status, 1.0)])
    printer_id = (await client.get(f"/api/v1/jobs/{job_id}")).json()["assigned_printer_id"]
    fake = MagicMock()
    fake.connected = True
    printer_manager._clients[printer_id] = fake

    with patch("app.api.routes.jobs.queue_engine") as engine:
        resp = await client.post(f"/api/v1/jobs/{job_id}/cancel")

    assert resp.status_code == 200
    assert fake.stop_print.called is stops_printer
    assert engine.wake.called is stops_printer  # a freed printer wakes the queue


async def test_cancel_still_succeeds_when_the_printer_refuses_to_stop(client, seed):
    (job_id,) = await seed([("printing", 1.0)])
    printer_id = (await client.get(f"/api/v1/jobs/{job_id}")).json()["assigned_printer_id"]
    fake = MagicMock()
    fake.connected = True
    fake.stop_print.side_effect = RuntimeError("websocket closed")
    printer_manager._clients[printer_id] = fake

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post(f"/api/v1/jobs/{job_id}/cancel")

    assert (resp.status_code, resp.json()["status"]) == (200, "cancelled")  # best-effort stop
    fake.stop_print.assert_called_once()


async def test_cancel_sliced_job_deletes_its_parked_gcode_row_and_file(client, seed, session_factory, tmp_path):
    (job_id,) = await seed([("sliced", 1.0)])
    gcode = tmp_path / "parked.gcode"
    gcode.write_text("G28\n")
    async with session_factory() as s:
        printer_id = (await s.get(Job, job_id)).assigned_printer_id
        s.add(GcodeFile(job_id=job_id, printer_id=printer_id, path=str(gcode)))
        await s.commit()

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post(f"/api/v1/jobs/{job_id}/cancel")

    assert resp.status_code == 200
    assert not gcode.exists()
    from sqlalchemy import func, select
    async with session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(GcodeFile).where(GcodeFile.job_id == job_id))).scalar_one() == 0


async def test_cancel_sliced_job_tolerates_a_gcode_file_already_gone_from_disk(client, seed, session_factory, tmp_path):
    (job_id,) = await seed([("sliced", 1.0)])
    async with session_factory() as s:
        printer_id = (await s.get(Job, job_id)).assigned_printer_id
        s.add(GcodeFile(job_id=job_id, printer_id=printer_id, path=str(tmp_path / "never-written.gcode")))
        await s.commit()

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post(f"/api/v1/jobs/{job_id}/cancel")

    assert (resp.status_code, resp.json()["status"]) == (200, "cancelled")
    from sqlalchemy import func, select
    async with session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(GcodeFile).where(GcodeFile.job_id == job_id))).scalar_one() == 0


async def test_cancel_leaves_other_jobs_gcode_alone(client, seed, session_factory, tmp_path):
    cancelled, other = await seed([("sliced", 1.0), ("sliced", 2.0)])
    files = {}
    async with session_factory() as s:
        for job_id in (cancelled, other):
            path = tmp_path / f"job{job_id}.gcode"
            path.write_text("G28\n")
            files[job_id] = path
            s.add(GcodeFile(job_id=job_id, printer_id=(await s.get(Job, job_id)).assigned_printer_id, path=str(path)))
        await s.commit()

    with patch("app.api.routes.jobs.queue_engine"):
        await client.post(f"/api/v1/jobs/{cancelled}/cancel")

    assert not files[cancelled].exists()
    assert files[other].exists()
