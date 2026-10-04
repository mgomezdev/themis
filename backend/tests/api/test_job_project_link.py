"""Linking jobs to projects by hand (orders -> projects migration left no way to): at creation, and afterwards."""
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_engine_wake():
    with patch("app.api.routes.jobs.queue_engine"):
        yield


async def _project(client, **body) -> dict:
    resp = await client.post("/api/v1/projects", json={"name": "Assembly", **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create(client, upload_3mf, create_printer, **extra):
    body = {"uploaded_file_id": await upload_3mf(), "plate_number": 1,
            "printer_configs": [{"printer_id": await create_printer(), "print_profile": "0.20mm",
                                 "filament_type": "any", "filament_color": "any"}], **extra}
    with patch("app.api.routes.jobs.queue_engine"):
        return await client.post("/api/v1/jobs", json=body)


async def test_create_job_linked_to_a_project_shows_up_in_the_project(client, upload_3mf, create_printer):
    proj = await _project(client, stage="planning")

    resp = await _create(client, upload_3mf, create_printer, project_id=proj["id"])

    assert resp.status_code == 201, resp.text
    job = resp.json()
    assert job["project_id"] == proj["id"]
    listed = (await client.get(f"/api/v1/projects/{proj['id']}/jobs")).json()
    assert [j["id"] for j in listed] == [job["id"]]


async def test_a_linked_job_takes_the_projects_order(client, session_factory, upload_3mf, create_printer):
    from app.models import Order, Project
    proj = await _project(client, stage="planning")
    async with session_factory() as s:
        order = Order(title="Internal grouping", customer="x", order_type="internal",
                      created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00")
        s.add(order)
        await s.flush()
        (await s.get(Project, proj["id"])).order_id = order.id
        await s.commit()
        order_id = order.id

    job = (await _create(client, upload_3mf, create_printer, project_id=proj["id"])).json()

    assert job["order_id"] == order_id


async def test_create_job_rejects_an_unknown_or_draft_project(client, upload_3mf, create_printer):
    assert (await _create(client, upload_3mf, create_printer, project_id=9999)).status_code == 404
    draft = await _project(client, stage="draft")
    resp = await _create(client, upload_3mf, create_printer, project_id=draft["id"])
    assert resp.status_code == 409
    assert "planning" in resp.json()["detail"]


async def test_link_an_existing_job_then_unlink_it(client, create_job):
    proj = await _project(client, stage="planning")
    job_id = await create_job()

    resp = await client.patch(f"/api/v1/jobs/{job_id}/project", json={"project_id": proj["id"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["project_id"] == proj["id"]
    assert [j["id"] for j in (await client.get(f"/api/v1/projects/{proj['id']}/jobs")).json()] == [job_id]

    resp = await client.patch(f"/api/v1/jobs/{job_id}/project", json={"project_id": None})
    assert resp.status_code == 200
    assert resp.json()["project_id"] is None and resp.json()["order_id"] is None
    assert (await client.get(f"/api/v1/projects/{proj['id']}/jobs")).json() == []


async def test_link_refuses_a_job_that_already_belongs_to_another_project(client, create_job):
    a = await _project(client, name="A", stage="planning")
    b = await _project(client, name="B", stage="planning")
    job_id = await create_job()
    await client.patch(f"/api/v1/jobs/{job_id}/project", json={"project_id": a["id"]})

    resp = await client.patch(f"/api/v1/jobs/{job_id}/project", json={"project_id": b["id"]})

    assert resp.status_code == 409
    assert (await client.get(f"/api/v1/jobs/{job_id}")).json()["project_id"] == a["id"]


async def test_link_errors(client, create_job):
    job_id = await create_job()
    draft = await _project(client, stage="draft")
    assert (await client.patch(f"/api/v1/jobs/{job_id}/project", json={"project_id": 9999})).status_code == 404
    assert (await client.patch(f"/api/v1/jobs/{job_id}/project", json={"project_id": draft["id"]})).status_code == 409
    assert (await client.patch("/api/v1/jobs/9999/project", json={"project_id": None})).status_code == 404
