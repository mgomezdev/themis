"""Project child resources (items, links), stage promotion, generate preconditions, project jobs, delete."""
import pytest

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
