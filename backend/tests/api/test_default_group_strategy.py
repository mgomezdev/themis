"""New print groups pack when a printer picks them up ("pool") unless Settings -> Print defaults says otherwise."""
from unittest.mock import patch

from sqlalchemy import select

from app.models import Job
from tests.api.test_pools import PoolPacker, _claim, _engine


async def _groups(client, pid):
    return (await client.get(f"/api/v1/projects/{pid}/groups")).json()


async def test_the_default_is_pool_and_it_is_a_setting(client):
    assert (await client.get("/api/v1/settings/queue")).json()["default_group_strategy"] == "pool"
    assert (await client.put("/api/v1/settings/queue", json={"default_group_strategy": "fixed"})).status_code == 200
    assert (await client.get("/api/v1/settings/queue")).json()["default_group_strategy"] == "fixed"
    assert (await client.put("/api/v1/settings/queue", json={"default_group_strategy": "sometimes"})).status_code == 422
    assert (await client.get("/api/v1/settings/queue")).json()["default_group_strategy"] == "fixed"


async def test_a_new_projects_groups_are_pools(client):
    pid = (await client.post("/api/v1/projects", json={"name": "P"})).json()["id"]
    (default,) = await _groups(client, pid)
    extra = (await client.post(f"/api/v1/projects/{pid}/groups", json={"name": "Extra"})).json()
    assert (default["strategy"], extra["strategy"]) == ("pool", "pool")


async def test_the_setting_decides_new_groups_and_leaves_existing_ones_alone(client):
    old = (await client.post("/api/v1/projects", json={"name": "Old"})).json()["id"]
    (old_default,) = await _groups(client, old)
    await client.put("/api/v1/settings/queue", json={"default_group_strategy": "fixed"})

    new = (await client.post("/api/v1/projects", json={"name": "New"})).json()["id"]
    (new_default,) = await _groups(client, new)
    extra_old = (await client.post(f"/api/v1/projects/{old}/groups", json={"name": "Extra"})).json()

    assert (new_default["strategy"], extra_old["strategy"]) == ("fixed", "fixed")
    assert (await _groups(client, old))[0]["strategy"] == old_default["strategy"] == "pool"


async def test_a_groups_own_strategy_can_still_be_changed(client):
    pid = (await client.post("/api/v1/projects", json={"name": "P"})).json()["id"]
    (g,) = await _groups(client, pid)
    r = await client.patch(f"/api/v1/projects/{pid}/groups/{g['id']}", json={"strategy": "fixed"})
    assert r.status_code == 200 and (await _groups(client, pid))[0]["strategy"] == "fixed"


async def test_out_of_the_box_a_project_is_packed_by_the_printer_that_picks_it_up(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    """No strategy chosen anywhere: generate queues the pool (no jobs), and an idle printer packs a plate for its own bed."""
    a = await create_printer(name="A", bed_x_mm=256.0, bed_y_mm=256.0)
    packer = PoolPacker()
    pid = (await client.post("/api/v1/projects", json={"name": "Defaults"})).json()["id"]
    (g,) = await _groups(client, pid)
    await client.patch(f"/api/v1/projects/{pid}/groups/{g['id']}", json={"targets": [{"target_key": str(a), "process_profile": "0.2"}]})
    fid = await upload_3mf(filename="part.stl", data=b"solid part\nendsolid\n")
    item = (await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "quantity": 5})).json()["id"]
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=packer):
        r = await client.post(f"/api/v1/projects/{pid}/generate", json={})
    assert r.status_code == 200 and r.json()["jobs"] == []

    await _claim(session_factory, _engine(session_factory, [a]), a, tmp_path, packer)

    async with session_factory() as s:
        (job,) = (await s.execute(select(Job).where(Job.project_id == pid))).scalars().all()
    assert job.pool_group_id == g["id"] and job.project_item_quantities == f'{{"{item}": 2}}'   # 256 mm bed: two parts
    assert (await _groups(client, pid))[0]["pool"] == {"done": 0, "printing": 2, "left": 3}
