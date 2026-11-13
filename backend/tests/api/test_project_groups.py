"""BIZ-360: print groups. Every project has a Default Group; groups carry eligibility targets with a process profile each and a
fixed-bed envelope; generation runs group by group (queue order = group order)."""
from unittest.mock import patch

from sqlalchemy import select

from app.models import Job, JobModelTarget, JobPrinterConfig
from tests.fake_providers import FakeProjectConstructor


async def _project(client, name="Groups") -> int:
    r = await client.post("/api/v1/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _groups(client, pid):
    r = await client.get(f"/api/v1/projects/{pid}/groups")
    assert r.status_code == 200, r.text
    return r.json()


async def _item(client, upload_3mf, pid, name, qty=1, **body) -> dict:
    fid = await upload_3mf(filename=name, data=f"solid {name}\nendsolid\n".encode())
    r = await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "quantity": qty, **body})
    assert r.status_code == 201, r.text
    return r.json()


async def _generate(client, pid, tmp_path, **body):
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        return await client.post(f"/api/v1/projects/{pid}/generate", json=body)


async def test_group_overrides_round_trip_preserve_clear_and_reject_unknown(client):
    pid = await _project(client)
    (group,) = await _groups(client, pid)
    path = f"/api/v1/projects/{pid}/groups/{group['id']}"
    response = await client.patch(path, json={"overrides": {"wall_loops": 3, "enable_support": False}})
    assert response.status_code == 200, response.text
    assert response.json()["overrides"] == {"wall_loops": "3", "enable_support": "0"}
    renamed = await client.patch(path, json={"name": "Renamed"})
    assert renamed.json()["overrides"] == response.json()["overrides"]
    assert (await _groups(client, pid))[0]["overrides"] == response.json()["overrides"]
    for invalid in ({"unknown_key": 1}, {"wall_loops": []}):
        refused = await client.patch(path, json={"overrides": invalid})
        assert refused.status_code == 422, refused.text
    cleared = await client.patch(path, json={"overrides": None})
    assert cleared.status_code == 200 and cleared.json()["overrides"] is None


async def test_group_overrides_copy_to_only_its_generated_jobs(client, upload_3mf, session_factory, tmp_path):
    pid = await _project(client)
    (default,) = await _groups(client, pid)
    fine = (await client.post(f"/api/v1/projects/{pid}/groups", json={"name": "Fine"})).json()
    await client.patch(f"/api/v1/projects/{pid}/groups/{default['id']}", json={"targets": [{"target_key": "any"}]})
    response = await client.patch(f"/api/v1/projects/{pid}/groups/{fine['id']}", json={
        "targets": [{"target_key": "any"}], "overrides": {"layer_height": 0.12}})
    assert response.status_code == 200, response.text
    await _item(client, upload_3mf, pid, "draft.stl")
    await _item(client, upload_3mf, pid, "fine.stl", group_id=fine["id"])
    generated = await _generate(client, pid, tmp_path)
    assert generated.status_code == 200, generated.text
    jobs = generated.json()["jobs"]
    assert {j["group_id"] for j in jobs} == {default["id"], fine["id"]}
    async with session_factory() as session:
        for created in jobs:
            row = await session.get(Job, created["id"])
            assert row.overrides == ({"layer_height": "0.12"} if created["group_id"] == fine["id"] else None)
    await client.patch(f"/api/v1/projects/{pid}/groups/{fine['id']}", json={"overrides": None})
    async with session_factory() as session:
        fine_id = next(j["id"] for j in jobs if j["group_id"] == fine["id"])
        assert (await session.get(Job, fine_id)).overrides == {"layer_height": "0.12"}


async def test_presliced_project_items_cannot_generate_with_group_overrides(client, upload_3mf, tmp_path):
    pid = await _project(client)
    (group,) = await _groups(client, pid)
    await client.patch(f"/api/v1/projects/{pid}/groups/{group['id']}", json={
        "targets": [{"target_key": "any"}], "overrides": {"wall_loops": 3}})
    await _item(client, upload_3mf, pid, "plate.gcode")
    result = await _generate(client, pid, tmp_path)
    assert result.status_code == 422, result.text
    assert "pre-sliced" in result.json()["detail"]


async def test_a_new_project_has_a_default_group_and_new_items_land_in_it(client, upload_3mf):
    pid = await _project(client)
    (g,) = await _groups(client, pid)
    assert (g["name"], g["is_default"], g["targets"], g["envelope_x"], g["envelope_y"]) == ("Default Group", True, [], None, None)
    item = await _item(client, upload_3mf, pid, "a.stl")
    assert item["group_id"] == g["id"]


async def test_groups_can_be_created_renamed_configured_reordered_and_deleted(client, upload_3mf, create_printer):
    pid = await _project(client)
    (default,) = await _groups(client, pid)
    p = await create_printer(name="Fine")
    fine = (await client.post(f"/api/v1/projects/{pid}/groups", json={"name": "Fine"})).json()
    r = await client.patch(f"/api/v1/projects/{pid}/groups/{fine['id']}", json={
        "name": "Fine detail", "envelope_x": 200, "envelope_y": 180, "mix_colours": True,
        "targets": [{"target_key": str(p), "process_profile": "0.12mm Fine"}, {"target_key": "any", "process_profile": ""}]})
    assert r.status_code == 200, r.text
    assert r.json()["targets"] == [{"target_key": str(p), "process_profile": "0.12mm Fine"}, {"target_key": "any", "process_profile": ""}]
    assert (r.json()["name"], r.json()["envelope_x"], r.json()["mix_colours"]) == ("Fine detail", 200, True)

    assert (await client.put(f"/api/v1/projects/{pid}/groups/order", json={"group_ids": [fine["id"], default["id"]]})).status_code == 200
    assert [g["id"] for g in await _groups(client, pid)] == [fine["id"], default["id"]]

    item = await _item(client, upload_3mf, pid, "a.stl")
    moved = await client.put(f"/api/v1/projects/{pid}/items/{item['id']}", json={"group_id": fine["id"]})
    assert moved.status_code == 200 and moved.json()["group_id"] == fine["id"]

    assert (await client.delete(f"/api/v1/projects/{pid}/groups/{default['id']}")).status_code == 409
    assert (await client.delete(f"/api/v1/projects/{pid}/groups/{fine['id']}")).status_code == 204
    items = (await client.get(f"/api/v1/projects/{pid}/items")).json()
    assert [i["group_id"] for i in items] == [default["id"]]            # its items went back to the Default Group


async def test_bad_targets_and_foreign_groups_are_refused(client, upload_3mf):
    pid, other = await _project(client), await _project(client, "Other")
    (g,) = await _groups(client, pid)
    (og,) = await _groups(client, other)
    assert (await client.patch(f"/api/v1/projects/{pid}/groups/{g['id']}", json={"targets": [{"target_key": "nope"}]})).status_code == 422
    assert (await client.patch(f"/api/v1/projects/{pid}/groups/{g['id']}",
                               json={"targets": [{"target_key": "any"}, {"target_key": "any"}]})).status_code == 422
    item = await _item(client, upload_3mf, pid, "a.stl")
    assert (await client.put(f"/api/v1/projects/{pid}/items/{item['id']}", json={"group_id": og["id"]})).status_code == 422
    assert (await client.patch(f"/api/v1/projects/{pid}/groups/{og['id']}", json={"name": "x"})).status_code == 404


async def test_generate_runs_group_by_group_with_each_targets_process_and_envelope(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    pid = await _project(client)
    (default,) = await _groups(client, pid)
    big = await create_printer(name="Big", bed_x_mm=350.0, bed_y_mm=350.0)
    small = await create_printer(name="Small", bed_x_mm=180.0, bed_y_mm=180.0)
    fine = (await client.post(f"/api/v1/projects/{pid}/groups", json={"name": "Fine"})).json()
    await client.patch(f"/api/v1/projects/{pid}/groups/{fine['id']}", json={
        "envelope_x": 300, "envelope_y": 300,                                    # only Big contains it
        "targets": [{"target_key": str(big), "process_profile": "0.12mm Fine"},
                    {"target_key": str(small), "process_profile": "0.12mm Fine"}]})
    await client.patch(f"/api/v1/projects/{pid}/groups/{default['id']}", json={
        "targets": [{"target_key": str(small), "process_profile": ""}, {"target_key": "any", "process_profile": "0.28mm Draft"}]})
    await client.put(f"/api/v1/projects/{pid}/groups/order", json={"group_ids": [fine["id"], default["id"]]})
    await _item(client, upload_3mf, pid, "draft.stl")
    fine_item = await _item(client, upload_3mf, pid, "fine.stl")
    await client.put(f"/api/v1/projects/{pid}/items/{fine_item['id']}", json={"group_id": fine["id"]})

    r = await _generate(client, pid, tmp_path)
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    assert [j["group_id"] for j in jobs] == [fine["id"], default["id"]]

    async with session_factory() as s:
        rows = {j.id: j for j in (await s.execute(select(Job).where(Job.project_id == pid))).scalars()}
        cfgs = (await s.execute(select(JobPrinterConfig))).scalars().all()
        targets = (await s.execute(select(JobModelTarget))).scalars().all()
    fine_job, draft_job = jobs[0]["id"], jobs[1]["id"]
    assert rows[fine_job].queue_position < rows[draft_job].queue_position
    explicit = {(c.job_id, c.printer_id): c.print_profile for c in cfgs if c.model_target_id is None}
    assert explicit == {(fine_job, big): "0.12mm Fine", (draft_job, small): ""}      # Small doesn't contain 300 × 300
    assert [(t.job_id, t.model_uuid, t.print_profile) for t in targets] == [(draft_job, None, "0.28mm Draft")]
    assert r.json()["groups"] == [
        {"id": fine["id"], "pack_bed_x": 300.0, "pack_bed_y": 300.0},
        {"id": default["id"], "pack_bed_x": 180.0, "pack_bed_y": 180.0},          # auto = smallest covered bed
    ]


async def test_an_empty_group_adds_nothing(client, upload_3mf, tmp_path):
    pid = await _project(client)
    await client.post(f"/api/v1/projects/{pid}/groups", json={"name": "Empty"})
    await _item(client, upload_3mf, pid, "a.stl")
    r = await _generate(client, pid, tmp_path)
    assert r.status_code == 200 and len(r.json()["jobs"]) == 1


async def test_legacy_request_eligibility_applies_to_a_default_group_without_targets(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    pid = await _project(client)
    p = await create_printer(name="P")
    await _item(client, upload_3mf, pid, "a.stl")
    r = await _generate(client, pid, tmp_path, eligible_printer_ids=[p], process_preset="0.20mm Standard")
    assert r.status_code == 200, r.text
    async with session_factory() as s:
        (cfg,) = (await s.execute(select(JobPrinterConfig))).scalars().all()
    assert (cfg.printer_id, cfg.print_profile) == (p, "0.20mm Standard")


async def test_the_default_group_exists_as_soon_as_the_project_does(client, session_factory):
    from app.models import ProjectGroup
    pid = await _project(client)
    async with session_factory() as s:
        rows = (await s.execute(select(ProjectGroup).where(ProjectGroup.project_id == pid))).scalars().all()
    assert [(g.name, g.is_default) for g in rows] == [("Default Group", True)]


async def test_a_group_no_printer_can_take_is_reported(client, upload_3mf, create_printer, tmp_path):
    pid = await _project(client)
    (default,) = await _groups(client, pid)
    small = await create_printer(name="Small", bed_x_mm=180.0, bed_y_mm=180.0)
    await client.patch(f"/api/v1/projects/{pid}/groups/{default['id']}", json={
        "envelope_x": 300, "envelope_y": 300, "targets": [{"target_key": str(small), "process_profile": ""}]})
    await _item(client, upload_3mf, pid, "a.stl")
    r = await _generate(client, pid, tmp_path)
    assert r.status_code == 200
    assert "no printer can take these jobs" in r.json()["groups"][0]["warning"]


async def test_legacy_any_printer_and_model_fields_apply_to_the_default_group(
        client, upload_3mf, create_printer, model_uuid_of, session_factory, tmp_path):
    p = await create_printer(name="P")
    model = await model_uuid_of(p)
    for body, expected in (({"eligible_any_printer": True}, [None]), ({"eligible_model_uuids": [model]}, [model])):
        pid = await _project(client, f"L{expected}")
        await _item(client, upload_3mf, pid, f"{expected}.stl")
        r = await _generate(client, pid, tmp_path, process_preset="0.20mm", **body)
        job_id = r.json()["jobs"][0]["id"]
        async with session_factory() as s:
            rows = (await s.execute(select(JobModelTarget).where(JobModelTarget.job_id == job_id))).scalars().all()
        assert [(t.model_uuid, t.print_profile) for t in rows] == [(expected[0], "0.20mm")]


async def test_invalid_order_and_half_envelope_are_refused(client):
    pid = await _project(client)
    (g,) = await _groups(client, pid)
    assert (await client.put(f"/api/v1/projects/{pid}/groups/order", json={"group_ids": []})).status_code == 422
    assert (await client.patch(f"/api/v1/projects/{pid}/groups/{g['id']}", json={"envelope_x": 200})).status_code == 422
    assert (await client.patch(f"/api/v1/projects/{pid}/groups/{g['id']}",
                               json={"targets": [{"target_key": "²"}]})).status_code == 422


async def test_an_item_with_a_dangling_group_is_adopted_by_the_default_group(client, upload_3mf, session_factory, tmp_path):
    from app.models import ProjectItem
    pid = await _project(client)
    (g,) = await _groups(client, pid)
    item = await _item(client, upload_3mf, pid, "a.stl")
    async with session_factory() as s:
        (await s.get(ProjectItem, item["id"])).group_id = 9999
        await s.commit()
    r = await _generate(client, pid, tmp_path)
    assert r.status_code == 200 and len(r.json()["jobs"]) == 1
    assert [i["group_id"] for i in (await client.get(f"/api/v1/projects/{pid}/items")).json()] == [g["id"]]


async def test_deleting_a_project_removes_its_groups_and_targets(client, session_factory):
    from app.models import ProjectGroup, ProjectGroupTarget
    pid = await _project(client)
    g = (await client.post(f"/api/v1/projects/{pid}/groups", json={"name": "X"})).json()
    await client.patch(f"/api/v1/projects/{pid}/groups/{g['id']}", json={"targets": [{"target_key": "any"}]})
    assert (await client.delete(f"/api/v1/projects/{pid}")).status_code in (200, 204)
    async with session_factory() as s:
        assert (await s.execute(select(ProjectGroup))).scalars().all() == []
        assert (await s.execute(select(ProjectGroupTarget))).scalars().all() == []
