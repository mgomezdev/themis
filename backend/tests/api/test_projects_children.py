"""Project child resources (items, links), stage promotion, generate preconditions, project jobs, delete."""
from unittest.mock import patch

from tests.fake_providers import FakeSlicingProvider
import pytest
from sqlalchemy import func, select

from app.models import Job, ProjectItem, ProjectLink, ProjectPart, UploadedFile

_ITEM_KEYS = {"id", "project_id", "file_id", "file_name", "quantity", "quantity_completed", "quantity_failed",
              "filament_type", "filament_color", "filament_id", "sort_order"}  # src/api/projects.ts ProjectItem
_LINK_KEYS = {"id", "project_id", "url", "label", "sort_order", "created_at"}


@pytest.fixture
async def project(client) -> int:
    resp = await client.post("/api/v1/projects", json={"name": "Assembly"})
    assert resp.status_code == 201
    return resp.json()["id"]


async def _add_item(client, project_id: int, file_id: int, **body) -> dict:
    resp = await client.post(f"/api/v1/projects/{project_id}/items", json={"file_id": file_id, **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _item_order(client, project_id: int) -> list[int]:
    return [i["id"] for i in (await client.get(f"/api/v1/projects/{project_id}/items")).json()]


# ---------------------------------------------------------------------------
# Item reorder
# ---------------------------------------------------------------------------

async def test_reorder_items_sets_and_persists_the_order(client, project, upload_3mf):
    f = await upload_3mf()
    a, b, c = [(await _add_item(client, project, f, sort_order=i))["id"] for i in (1, 2, 3)]
    assert await _item_order(client, project) == [a, b, c]

    resp = await client.put(f"/api/v1/projects/{project}/items/reorder", json=[
        {"id": a, "sort_order": 30}, {"id": b, "sort_order": 10}, {"id": c, "sort_order": 20},
    ])

    assert resp.status_code == 200, resp.text
    assert [i["id"] for i in resp.json()] == [b, c, a]  # returns the full list in the new order
    assert {i["id"]: i["sort_order"] for i in resp.json()} == {a: 30, b: 10, c: 20}
    assert await _item_order(client, project) == [b, c, a]  # and it persisted


async def test_reorder_items_rejects_foreign_ids_and_changes_nothing(client, project, upload_3mf):
    f = await upload_3mf()
    mine = (await _add_item(client, project, f, sort_order=1))["id"]
    other_project = (await client.post("/api/v1/projects", json={"name": "Other"})).json()["id"]
    theirs = (await _add_item(client, other_project, f, sort_order=1))["id"]

    resp = await client.put(f"/api/v1/projects/{project}/items/reorder", json=[
        {"id": mine, "sort_order": 99}, {"id": theirs, "sort_order": 5},
    ])

    assert resp.status_code == 422
    assert (await client.get(f"/api/v1/projects/{project}/items")).json()[0]["sort_order"] == 1
    assert (await client.get(f"/api/v1/projects/{other_project}/items")).json()[0]["sort_order"] == 1


async def test_reorder_items_404_for_missing_project(client):
    resp = await client.put("/api/v1/projects/999999/items/reorder", json=[])
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Item lifecycle
# ---------------------------------------------------------------------------

async def test_item_lifecycle_add_list_update_delete(client, project, upload_3mf):
    f = await upload_3mf(filename="bracket.3mf")

    created = await _add_item(client, project, f, quantity=3, filament_type="PLA",
                              filament_color="#FF0000", sort_order=2)
    assert set(created) == _ITEM_KEYS
    assert created == {
        "id": created["id"], "project_id": project, "file_id": f, "file_name": "bracket.3mf",
        "quantity": 3, "quantity_completed": 0, "quantity_failed": 0,
        "filament_type": "PLA", "filament_color": "#FF0000", "filament_id": None, "sort_order": 2,
    }
    assert (await client.get(f"/api/v1/projects/{project}/items")).json() == [created]

    resp = await client.put(f"/api/v1/projects/{project}/items/{created['id']}",
                            json={"quantity": 5, "filament_color": "#00FF00"})
    assert resp.status_code == 200
    updated = resp.json()
    assert (updated["quantity"], updated["filament_color"]) == (5, "#00FF00")
    assert (updated["filament_type"], updated["sort_order"]) == ("PLA", 2)  # omitted fields untouched
    assert (await client.get(f"/api/v1/projects/{project}/items")).json() == [updated]

    resp = await client.delete(f"/api/v1/projects/{project}/items/{created['id']}")
    assert (resp.status_code, resp.json()) == (200, {"deleted": created["id"]})
    assert (await client.get(f"/api/v1/projects/{project}/items")).json() == []
    assert (await client.delete(f"/api/v1/projects/{project}/items/{created['id']}")).status_code == 404
    assert [x["id"] for x in (await client.get("/api/v1/files")).json()] == [f]  # the file itself survives


async def test_item_add_and_update_validation(client, project, upload_3mf):
    f = await upload_3mf()
    item = await _add_item(client, project, f, quantity=2)

    unknown_file = await client.post(f"/api/v1/projects/{project}/items", json={"file_id": 424242})
    zero_qty = await client.post(f"/api/v1/projects/{project}/items", json={"file_id": f, "quantity": 0})
    zero_update = await client.put(f"/api/v1/projects/{project}/items/{item['id']}", json={"quantity": 0})

    assert (unknown_file.status_code, unknown_file.json()["detail"]) == (404, "File 424242 not found")
    assert zero_qty.status_code == 422
    assert zero_update.status_code == 422
    items = (await client.get(f"/api/v1/projects/{project}/items")).json()
    assert [(i["id"], i["quantity"]) for i in items] == [(item["id"], 2)]  # nothing added, nothing changed


async def test_items_are_scoped_to_their_project(client, project, upload_3mf):
    f = await upload_3mf()
    item = await _add_item(client, project, f, quantity=2)
    other = (await client.post("/api/v1/projects", json={"name": "Other"})).json()["id"]

    wrong_update = await client.put(f"/api/v1/projects/{other}/items/{item['id']}", json={"quantity": 9})
    wrong_delete = await client.delete(f"/api/v1/projects/{other}/items/{item['id']}")
    missing_project = [await client.get("/api/v1/projects/999999/items"),
                       await client.post("/api/v1/projects/999999/items", json={"file_id": f})]

    assert (wrong_update.status_code, wrong_delete.status_code) == (404, 404)
    assert [r.status_code for r in missing_project] == [404, 404]
    assert (await client.get(f"/api/v1/projects/{project}/items")).json()[0]["quantity"] == 2
    assert (await client.get(f"/api/v1/projects/{other}/items")).json() == []


# ---------------------------------------------------------------------------
# Link lifecycle
# ---------------------------------------------------------------------------

async def _add_link(client, project_id: int, **body) -> dict:
    resp = await client.post(f"/api/v1/projects/{project_id}/links", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_link_lifecycle_add_list_order_update_delete(client, project):
    a = await _add_link(client, project, url="https://a.example", label="Datasheet", sort_order=2)
    assert set(a) == _LINK_KEYS
    assert (a["url"], a["label"], a["sort_order"], a["project_id"]) == ("https://a.example", "Datasheet", 2, project)
    assert a["created_at"]
    b = await _add_link(client, project, url="https://b.example", sort_order=1)
    c = await _add_link(client, project, url="https://c.example", sort_order=1)
    assert b["label"] is None
    listed = (await client.get(f"/api/v1/projects/{project}/links")).json()
    assert [x["id"] for x in listed] == [b["id"], c["id"], a["id"]]  # sort_order, then id

    resp = await client.put(f"/api/v1/projects/{project}/links/{a['id']}", json={"url": "https://a2.example"})
    assert resp.status_code == 200
    assert (resp.json()["url"], resp.json()["label"]) == ("https://a2.example", "Datasheet")  # label kept
    resp = await client.put(f"/api/v1/projects/{project}/links/{a['id']}", json={"label": "Renamed", "sort_order": 0})
    assert (resp.json()["label"], resp.json()["sort_order"]) == ("Renamed", 0)
    listed = (await client.get(f"/api/v1/projects/{project}/links")).json()
    assert [x["id"] for x in listed] == [a["id"], b["id"], c["id"]]  # re-sorted after the update

    resp = await client.delete(f"/api/v1/projects/{project}/links/{b['id']}")
    assert (resp.status_code, resp.json()) == (200, {"deleted": b["id"]})
    assert [x["id"] for x in (await client.get(f"/api/v1/projects/{project}/links")).json()] == [a["id"], c["id"]]
    assert (await client.delete(f"/api/v1/projects/{project}/links/{b['id']}")).status_code == 404


async def test_links_are_scoped_to_their_project(client, project):
    link = await _add_link(client, project, url="https://a.example")
    other = (await client.post("/api/v1/projects", json={"name": "Other"})).json()["id"]

    wrong_update = await client.put(f"/api/v1/projects/{other}/links/{link['id']}", json={"url": "https://evil"})
    wrong_delete = await client.delete(f"/api/v1/projects/{other}/links/{link['id']}")
    missing = [await client.get("/api/v1/projects/999999/links"),
               await client.post("/api/v1/projects/999999/links", json={"url": "https://x"})]

    assert (wrong_update.status_code, wrong_delete.status_code) == (404, 404)
    assert [r.status_code for r in missing] == [404, 404]
    assert (await client.get(f"/api/v1/projects/{project}/links")).json()[0]["url"] == "https://a.example"


# ---------------------------------------------------------------------------
# Stage promotion (draft -> planning -> queued, forward one step only)
# ---------------------------------------------------------------------------

async def _stage(client, project_id: int) -> str:
    return (await client.get(f"/api/v1/projects/{project_id}")).json()["stage"]


async def test_promote_walks_forward_one_stage_at_a_time_and_wakes_the_queue_on_queued(client):
    project_id = (await client.post("/api/v1/projects", json={"name": "Draft job", "stage": "draft"})).json()["id"]

    with patch("app.api.routes.projects.queue_engine") as engine:
        skip = await client.post(f"/api/v1/projects/{project_id}/promote", json={"stage": "queued"})
        assert (skip.status_code, await _stage(client, project_id)) == (409, "draft")  # cannot skip planning

        to_planning = await client.post(f"/api/v1/projects/{project_id}/promote", json={"stage": "planning"})
        assert (to_planning.status_code, to_planning.json()["stage"]) == (200, "planning")
        engine.wake.assert_not_called()  # planning jobs are not eligible yet

        repeat = await client.post(f"/api/v1/projects/{project_id}/promote", json={"stage": "planning"})
        backward = await client.post(f"/api/v1/projects/{project_id}/promote", json={"stage": "draft"})
        bogus = await client.post(f"/api/v1/projects/{project_id}/promote", json={"stage": "bogus"})
        assert [r.status_code for r in (repeat, backward, bogus)] == [409, 409, 409]
        assert await _stage(client, project_id) == "planning"

        to_queued = await client.post(f"/api/v1/projects/{project_id}/promote", json={"stage": "queued"})
        assert (to_queued.status_code, to_queued.json()["stage"]) == (200, "queued")
        engine.wake.assert_called_once_with()

        assert (await client.post(f"/api/v1/projects/{project_id}/promote", json={"stage": "queued"})).status_code == 409


async def test_promote_404_for_missing_project(client):
    resp = await client.post("/api/v1/projects/999999/promote", json={"stage": "planning"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Generate preconditions: every rejection leaves the project without jobs
# ---------------------------------------------------------------------------

_STL = b"solid x\nendsolid x\n"


async def _generate(client, project_id: int, tmp_path, *, sidecar: str | None = "http://laminus.test"):
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_slicing_provider", return_value=FakeSlicingProvider() if sidecar else None):
        return await client.post(f"/api/v1/projects/{project_id}/generate", json={"process_preset": "0.20mm Standard"})


async def _assert_no_jobs(client, project_id: int) -> None:
    assert (await client.get(f"/api/v1/projects/{project_id}/jobs")).json() == []


async def test_generate_is_refused_while_the_project_is_a_draft(client, tmp_path, upload_3mf):
    project_id = (await client.post("/api/v1/projects", json={"name": "D", "stage": "draft"})).json()["id"]
    await _add_item(client, project_id, await upload_3mf(filename="p.stl", data=_STL))

    resp = await _generate(client, project_id, tmp_path)

    assert (resp.status_code, resp.json()["detail"]) == (409, "Promote the project to planning before creating jobs")
    await _assert_no_jobs(client, project_id)


async def test_generate_requires_the_sidecar(client, project, tmp_path, upload_3mf):
    await _add_item(client, project, await upload_3mf(filename="p.stl", data=_STL))

    resp = await _generate(client, project, tmp_path, sidecar=None)

    assert resp.status_code == 422 and "LAMINUS_SIDECAR_URL" in resp.json()["detail"]
    await _assert_no_jobs(client, project)


async def test_generate_requires_items(client, project, tmp_path):
    resp = await _generate(client, project, tmp_path)

    assert (resp.status_code, resp.json()["detail"]) == (422, "Project has no items — add STL files before generating")
    await _assert_no_jobs(client, project)


async def test_generate_rejects_non_stl_items(client, project, tmp_path, upload_3mf):
    await _add_item(client, project, await upload_3mf(filename="model.3mf"))

    resp = await _generate(client, project, tmp_path)

    assert resp.status_code == 400 and "not an STL" in resp.json()["detail"]
    await _assert_no_jobs(client, project)


async def test_generate_reports_an_stl_missing_from_disk(client, project, tmp_path, upload_3mf):
    await _add_item(client, project, await upload_3mf(filename="gone.stl", data=_STL))
    for path in (tmp_path / "library").rglob("gone.stl"):
        path.unlink()

    resp = await _generate(client, project, tmp_path)

    assert resp.status_code == 422 and "missing from disk" in resp.json()["detail"]
    await _assert_no_jobs(client, project)


# ---------------------------------------------------------------------------
# GET /projects/{id}/jobs
# ---------------------------------------------------------------------------

_JOB_KEYS = {"id", "plate_number", "status", "queue_position", "assigned_printer_id", "block_reason", "outcome",
             "created_at", "updated_at", "completed_at", "file_name", "total_parts"}


async def _attach(session_factory, job_id: int, project_id: int, quantities: str | None = None) -> None:
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.project_id = project_id
        job.project_item_quantities = quantities
        await s.commit()


async def test_project_jobs_lists_only_that_projects_jobs_in_id_order(client, session_factory, create_job, upload_3mf):
    mine = (await client.post("/api/v1/projects", json={"name": "Mine"})).json()["id"]
    theirs = (await client.post("/api/v1/projects", json={"name": "Theirs"})).json()["id"]
    file_id = await upload_3mf(filename="plate.3mf")
    j1, j2, j3, j4 = [await create_job(file_id=file_id) for _ in range(4)]
    await _attach(session_factory, j3, mine, '{"10": 2, "11": 3}')
    await _attach(session_factory, j1, mine, "not json")  # unparseable quantities count as zero parts
    await _attach(session_factory, j2, theirs)
    # j4 belongs to no project

    rows = (await client.get(f"/api/v1/projects/{mine}/jobs")).json()

    assert [r["id"] for r in rows] == [j1, j3]
    assert all(set(r) == _JOB_KEYS for r in rows)
    assert [(r["file_name"], r["total_parts"], r["status"]) for r in rows] == [
        ("plate.3mf", 0, "queued"), ("plate.3mf", 5, "queued")]
    assert [r["id"] for r in (await client.get(f"/api/v1/projects/{theirs}/jobs")).json()] == [j2]


async def test_project_jobs_404_for_missing_project(client):
    assert (await client.get("/api/v1/projects/999999/jobs")).status_code == 404


# ---------------------------------------------------------------------------
# DELETE /projects/{id}
# ---------------------------------------------------------------------------

async def _count(session_factory, model, **where) -> int:
    async with session_factory() as s:
        q = select(func.count()).select_from(model)
        for col, val in where.items():
            q = q.where(getattr(model, col) == val)
        return (await s.execute(q)).scalar_one()


async def test_delete_project_removes_children_but_keeps_jobs_and_files(client, session_factory, create_job, upload_3mf):
    project_id = (await client.post("/api/v1/projects", json={"name": "Doomed"})).json()["id"]
    file_id = await upload_3mf(filename="keep.3mf")
    await _add_item(client, project_id, file_id)
    await _add_link(client, project_id, url="https://a.example")
    assert (await client.post(f"/api/v1/projects/{project_id}/parts", json={"name": "M3 screw", "quantity": 4})).status_code == 201
    job_id = await create_job(file_id=file_id)
    await _attach(session_factory, job_id, project_id)

    resp = await client.delete(f"/api/v1/projects/{project_id}")

    assert (resp.status_code, resp.json()) == (200, {"deleted": project_id})
    assert (await client.get(f"/api/v1/projects/{project_id}")).status_code == 404
    for model in (ProjectItem, ProjectLink, ProjectPart):
        assert await _count(session_factory, model, project_id=project_id) == 0, model.__name__
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()  # the job survives, detached, still queued
    assert (job["status"], job["project_id"]) == ("queued", None)
    assert await _count(session_factory, UploadedFile, id=file_id) == 1
    assert (await client.delete(f"/api/v1/projects/{project_id}")).status_code == 404
