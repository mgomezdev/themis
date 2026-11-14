"""PUT /jobs/{id}/outcome — per-item pass/fail accounting for project jobs, and GET /jobs/history."""
import json

import pytest

from app.models import Job


@pytest.fixture
async def project_with_items(client, upload_3mf):
    """A project with two items (A wants 5, B wants 2). Returns (project_id, item_a_id, item_b_id)."""
    project_id = (await client.post("/api/v1/projects", json={"name": "Widgets"})).json()["id"]
    file_id = await upload_3mf()
    ids = []
    for qty in (5, 2):
        resp = await client.post(f"/api/v1/projects/{project_id}/items", json={"file_id": file_id, "quantity": qty})
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["id"])
    return project_id, ids[0], ids[1]


@pytest.fixture
def project_job(client, session_factory, create_job, project_with_items):
    """`await project_job({item_id: qty_on_plate, ...}, status="complete")` -> job id linked to the project."""
    project_id, *_ = project_with_items

    async def _make(plate_quantities: dict[int, int], status: str = "complete") -> int:
        job_id = await create_job()
        async with session_factory() as s:
            job = await s.get(Job, job_id)
            job.project_id = project_id
            job.status = status
            job.project_item_quantities = json.dumps({str(k): v for k, v in plate_quantities.items()})
            await s.commit()
        return job_id
    return _make


async def _item_counts(client, project_id: int) -> dict[int, tuple[int, int]]:
    items = (await client.get(f"/api/v1/projects/{project_id}")).json()["items"]
    return {i["id"]: (i["quantity_completed"], i["quantity_failed"]) for i in items}


async def _mark(client, job_id: int, failures: dict[int, int]):
    return await client.put(
        f"/api/v1/jobs/{job_id}/outcome",
        json={"failures": [{"project_item_id": k, "quantity_failed": v} for k, v in failures.items()]},
    )


async def test_outcome_credits_successes_and_failures_per_item(client, project_with_items, project_job):
    project_id, a, b = project_with_items
    job_id = await project_job({a: 3, b: 2})

    resp = await _mark(client, job_id, {a: 1})

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "reviewed"
    assert body["status"] == "complete"  # marking an outcome never changes the job's status
    assert body["failures"] == [
        {"project_item_id": a, "quantity_failed": 1},
        {"project_item_id": b, "quantity_failed": 0},
    ]
    assert await _item_counts(client, project_id) == {a: (2, 1), b: (2, 0)}


async def test_re_reviewing_replaces_the_previous_outcome_instead_of_stacking(client, project_with_items, project_job):
    project_id, a, b = project_with_items
    job_id = await project_job({a: 3, b: 2})

    await _mark(client, job_id, {a: 1})
    assert await _item_counts(client, project_id) == {a: (2, 1), b: (2, 0)}

    await _mark(client, job_id, {})  # everything actually printed fine
    assert await _item_counts(client, project_id) == {a: (3, 0), b: (2, 0)}

    await _mark(client, job_id, {a: 2, b: 1})
    assert await _item_counts(client, project_id) == {a: (1, 2), b: (1, 1)}


async def test_outcomes_of_different_jobs_accumulate_and_re_review_only_touches_its_own_job(client, project_with_items, project_job):
    project_id, a, _ = project_with_items
    job1 = await project_job({a: 2})
    job2 = await project_job({a: 1})

    await _mark(client, job1, {})
    await _mark(client, job2, {a: 1})
    assert (await _item_counts(client, project_id))[a] == (2, 1)

    await _mark(client, job2, {})  # job2 re-reviewed: its failure becomes a success
    assert (await _item_counts(client, project_id))[a] == (3, 0)


async def test_reported_failures_are_clamped_to_the_quantity_on_the_plate(client, project_with_items, project_job):
    project_id, a, _ = project_with_items
    job_id = await project_job({a: 3})

    resp = await _mark(client, job_id, {a: 10})

    assert resp.json()["failures"] == [{"project_item_id": a, "quantity_failed": 3}]
    assert (await _item_counts(client, project_id))[a] == (0, 3)


async def test_failures_for_items_not_on_the_plate_are_ignored(client, project_with_items, project_job):
    project_id, a, b = project_with_items
    job_id = await project_job({a: 2})

    resp = await _mark(client, job_id, {b: 1, 9999: 1})

    assert resp.status_code == 200
    assert resp.json()["failures"] == [{"project_item_id": a, "quantity_failed": 0}]
    assert await _item_counts(client, project_id) == {a: (2, 0), b: (0, 0)}


async def test_job_without_project_items_cannot_be_marked(client, create_job):
    job_id = await create_job()

    resp = await client.put(f"/api/v1/jobs/{job_id}/outcome", json={"failures": []})

    assert resp.status_code == 400
    assert (await client.get(f"/api/v1/jobs/{job_id}")).json()["outcome"] is None


async def test_marking_an_unknown_job_is_404(client):
    assert (await client.put("/api/v1/jobs/9999/outcome", json={"failures": []})).status_code == 404


# ---------------------------------------------------------------------------
# GET /jobs/history
# ---------------------------------------------------------------------------

@pytest.fixture
def jobs_by_status(session_factory, create_job, create_printer):
    """`await jobs_by_status({"complete": "2026-01-03", ...})` -> {status: job_id}; updated_at drives ordering."""
    async def _make(spec: dict[str, str], printer_id: int | None = None) -> dict[str, int]:
        printer_id = printer_id if printer_id is not None else await create_printer()
        ids = {}
        for status, updated_at in spec.items():
            job_id = await create_job(printer_id=printer_id)
            async with session_factory() as s:
                job = await s.get(Job, job_id)
                job.status = status
                job.updated_at = updated_at
                job.assigned_printer_id = printer_id if status == "complete" else None
                await s.commit()
            ids[status] = job_id
        return ids
    return _make


async def test_history_defaults_to_finished_jobs_newest_first(client, jobs_by_status):
    ids = await jobs_by_status({
        "queued": "2026-01-09", "complete": "2026-01-02", "failed": "2026-01-03",
        "cancelled": "2026-01-01", "printing": "2026-01-10",
    })

    rows = (await client.get("/api/v1/jobs/history")).json()

    assert [r["id"] for r in rows] == [ids["failed"], ids["complete"], ids["cancelled"]]


async def test_history_filters_by_status_and_respects_limit(client, jobs_by_status):
    ids = await jobs_by_status({"complete": "2026-01-01", "failed": "2026-01-02", "cancelled": "2026-01-03"})

    only_failed = (await client.get("/api/v1/jobs/history", params={"status": "failed"})).json()
    limited = (await client.get("/api/v1/jobs/history", params={"limit": 2})).json()

    assert [r["id"] for r in only_failed] == [ids["failed"]]
    assert [r["id"] for r in limited] == [ids["cancelled"], ids["failed"]]
    assert (await client.get("/api/v1/jobs/history", params={"status": "nonsense"})).json() == []


async def test_history_enriches_rows_with_file_printer_and_project_names(client, session_factory, jobs_by_status, project_with_items):
    project_id, *_ = project_with_items
    ids = await jobs_by_status({"complete": "2026-01-01", "failed": "2026-01-02"})
    async with session_factory() as s:
        (await s.get(Job, ids["complete"])).project_id = project_id
        await s.commit()

    rows = {r["id"]: r for r in (await client.get("/api/v1/jobs/history")).json()}

    assert rows[ids["complete"]]["file_name"] == "m.3mf"
    assert rows[ids["complete"]]["printer_name"] == "P1S"
    assert rows[ids["complete"]]["project_name"] == "Widgets"
    assert rows[ids["failed"]]["printer_name"] is None
    assert rows[ids["failed"]]["project_name"] is None


async def test_history_can_be_scoped_to_one_project(client, session_factory, jobs_by_status, project_with_items):
    project_id, *_ = project_with_items
    ids = await jobs_by_status({"complete": "2026-01-01", "failed": "2026-01-02"})
    async with session_factory() as s:
        (await s.get(Job, ids["complete"])).project_id = project_id
        await s.commit()

    rows = (await client.get("/api/v1/jobs/history", params={"project_id": project_id})).json()

    assert [r["id"] for r in rows] == [ids["complete"]]
