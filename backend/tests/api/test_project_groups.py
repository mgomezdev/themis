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
    draft_item = await _item(client, upload_3mf, pid, "draft.stl")
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
    assert {draft_item["id"], fine_item["id"]}


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
