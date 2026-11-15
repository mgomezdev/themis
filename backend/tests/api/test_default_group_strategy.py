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


async def test_printers_picked_at_generate_become_the_pools_targets(client, upload_3mf, create_printer, session_factory, tmp_path):
    """The Generate dialog sends the eligible printers with the request. A pool is claimed later, by its saved targets: without
    saving them, a default (pool) project would queue and then never print."""
    a = await create_printer(name="A", bed_x_mm=256.0, bed_y_mm=256.0)
    packer = PoolPacker()
    pid = (await client.post("/api/v1/projects", json={"name": "Picked"})).json()["id"]
    fid = await upload_3mf(filename="part.stl", data=b"solid part\nendsolid\n")
    await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "quantity": 2})
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=packer):
        r = await client.post(f"/api/v1/projects/{pid}/generate",
                              json={"eligible_printer_ids": [a, 99999], "process_preset": "0.2"})
    assert r.status_code == 200, r.text
    (g,) = await _groups(client, pid)
    assert [(t["target_key"], t["process_profile"]) for t in g["targets"]] == [(str(a), "0.2")]      # the unknown id is dropped
    assert r.json()["groups"] == [{"id": g["id"], "pool": True}]                                    # takeable: no warning

    await _claim(session_factory, _engine(session_factory, [a]), a, tmp_path, packer)
    async with session_factory() as s:
        assert len((await s.execute(select(Job).where(Job.project_id == pid))).scalars().all()) == 1


async def test_generating_a_pool_nothing_can_take_says_so(client, upload_3mf, tmp_path):
    pid = (await client.post("/api/v1/projects", json={"name": "Nobody"})).json()["id"]
    fid = await upload_3mf(filename="part.stl", data=b"solid part\nendsolid\n")
    await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "quantity": 2})
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=PoolPacker()):
        r = await client.post(f"/api/v1/projects/{pid}/generate", json={})
    assert r.status_code == 200 and r.json()["jobs"] == []
    (group,) = r.json()["groups"]
    assert group["pool"] is True and "no eligible printer" in group["warning"]


async def test_saved_targets_are_not_replaced_by_the_requests(client, upload_3mf, create_printer, tmp_path):
    a = await create_printer(name="A")
    b = await create_printer(name="B")
    pid = (await client.post("/api/v1/projects", json={"name": "Saved"})).json()["id"]
    (g,) = await _groups(client, pid)
    await client.patch(f"/api/v1/projects/{pid}/groups/{g['id']}", json={"targets": [{"target_key": str(a), "process_profile": "0.2"}]})
    fid = await upload_3mf(filename="part.stl", data=b"solid part\nendsolid\n")
    await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "quantity": 1})
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=PoolPacker()):
        await client.post(f"/api/v1/projects/{pid}/generate", json={"eligible_printer_ids": [b], "process_preset": "0.3"})
    assert [t["target_key"] for t in (await _groups(client, pid))[0]["targets"]] == [str(a)]
