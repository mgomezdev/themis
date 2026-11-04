"""BIZ-356: project items are STLs, 3MF objects, 3MF plates (bundles) or gcode plates × qty, each with a packing rule. Packable
items go to the 3mf.construct provider with their selection and rule; Locked plates and gcode become one fixed job per copy."""
import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

from app.api.routes.projects import _plate_item_quantities
from app.models import Job, Printer, ProjectItem, UploadedFile
from app.services.providers.contract import PlateManifest
from tests.conftest import slice_info_xml
from tests.fake_providers import FakeProjectConstructor


def _two_plate_3mf(tag: str = "a") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("Metadata/slice_info.config", date_time=(2026, 1, 1, 0, 0, 0)), slice_info_xml([(1, 60, 5.0), (2, 60, 5.0)]))
        for n in (1, 2):
            zf.writestr(zipfile.ZipInfo(f"Metadata/plate_{n}.png", date_time=(2026, 1, 1, 0, 0, 0)), b"\x89PNG")
        zf.writestr(zipfile.ZipInfo("Metadata/tag.txt", date_time=(2026, 1, 1, 0, 0, 0)), tag)
    return buf.getvalue()


async def _project(client) -> int:
    return (await client.post("/api/v1/projects", json={"name": "Modular"})).json()["id"]


async def _add(client, pid, fid, **body):
    return await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, **body})


async def test_kinds_are_inferred_and_validated(client, upload_3mf):
    pid = await _project(client)
    stl = await upload_3mf(filename="a.stl", data=b"solid a\nendsolid\n")
    proj = await upload_3mf(filename="kit.3mf", data=_two_plate_3mf())
    gcode = await upload_3mf(filename="part.gcode", data=b"; estimated printing time (normal mode) = 1m 0s\nG28\n")

    r = (await _add(client, pid, stl)).json()
    assert (r["kind"], r["source_ref"], r["pack_rule"]) == ("stl", None, "split")
    r = (await _add(client, pid, proj)).json()
    assert (r["kind"], r["source_ref"], r["pack_rule"]) == ("3mf_plate", "1", "split")
    r = (await _add(client, pid, proj, kind="3mf_plate", source_ref="2", pack_rule="locked")).json()
    assert (r["kind"], r["source_ref"], r["pack_rule"]) == ("3mf_plate", "2", "locked")
    r = (await _add(client, pid, gcode)).json()
    assert (r["kind"], r["source_ref"], r["pack_rule"]) == ("gcode", "1", "locked")

    assert (await _add(client, pid, proj, kind="3mf_object")).status_code == 422                       # needs the object id
    assert (await _add(client, pid, proj, kind="3mf_plate", source_ref="3")).status_code == 422        # no plate 3
    assert (await _add(client, pid, stl, pack_rule="together")).status_code == 422                     # not for one mesh
    assert (await _add(client, pid, stl, kind="gcode")).status_code == 422
    item = (await _add(client, pid, proj, kind="3mf_object", source_ref="8")).json()
    assert (await client.put(f"/api/v1/projects/{pid}/items/{item['id']}", json={"pack_rule": "solo"})).json()["pack_rule"] == "solo"
    assert (await client.put(f"/api/v1/projects/{pid}/items/{item['id']}", json={"pack_rule": "locked"})).status_code == 422


async def test_a_project_with_all_four_kinds_generates_packs_and_fixed_jobs(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    p = await create_printer(name="P")
    pid = await _project(client)
    stl = await upload_3mf(filename="a.stl", data=b"solid a\nendsolid\n")
    proj = await upload_3mf(filename="kit.3mf", data=_two_plate_3mf())
    gcode = await upload_3mf(filename="part.gcode", data=b"; estimated printing time (normal mode) = 1m 0s\nG28\n")
    await _add(client, pid, stl, quantity=2)
    await _add(client, pid, proj, kind="3mf_object", source_ref="8", quantity=2)
    await _add(client, pid, proj, kind="3mf_plate", source_ref="1", pack_rule="together")
    locked = (await _add(client, pid, proj, kind="3mf_plate", source_ref="2", pack_rule="locked", quantity=2)).json()
    gc = (await _add(client, pid, gcode)).json()

    packer = FakeProjectConstructor()
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=packer):
        r = await client.post(f"/api/v1/projects/{pid}/generate", json={"eligible_printer_ids": [p], "process_preset": "0.2"})
    assert r.status_code == 200, r.text

    (req,) = packer.calls
    assert [(i.quantity, i.object_id, i.plate, i.rule) for i in req.inputs] == [
        (2, None, None, "split"), (2, "8", None, "split"), (1, None, 1, "together")]
    async with session_factory() as s:
        jobs = (await s.execute(select(Job).where(Job.project_id == pid).order_by(Job.queue_position))).scalars().all()
        files = {f.id: f.original_filename for f in (await s.execute(select(UploadedFile))).scalars()}
    fixed = [(files[j.uploaded_file_id], j.plate_number, json.loads(j.project_item_quantities)) for j in jobs
             if j.uploaded_file_id in (proj, gcode)]
    assert fixed == [("kit.3mf", 2, {str(locked["id"]): 1}), ("kit.3mf", 2, {str(locked["id"]): 1}),
                     ("part.gcode", 1, {str(gc["id"]): 1})]
    assert any(files[j.uploaded_file_id].startswith("project-") for j in jobs)            # the mixed pack


async def test_a_3mf_item_whose_file_changed_must_be_re_picked(client, upload_3mf, create_printer, session_factory, tmp_path):
    pid = await _project(client)
    proj = await upload_3mf(filename="kit.3mf", data=_two_plate_3mf())
    item = (await _add(client, pid, proj, kind="3mf_object", source_ref="8")).json()
    async with session_factory() as s:
        (await s.get(ProjectItem, item["id"])).source_hash = "an-older-version"
        await s.commit()
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        r = await client.post(f"/api/v1/projects/{pid}/generate", json={})
    assert r.status_code == 422 and "pick it again" in r.json()["detail"]


def test_a_bundle_is_counted_in_copies_not_objects():
    items = [SimpleNamespace(id=1, file_id=9, quantity=2)]
    wanted = [("kit.3mf", 2, "item-1", {"plate": 1, "rule": "together"})]
    m = PlateManifest(plates={1: {0: 3}, 2: {0: 3}}, requested={0: 6})              # 3 objects per copy
    assert _plate_item_quantities(m, wanted, items, [1, 2]) == [{"1": 1}, {"1": 1}]
    assert _plate_item_quantities(PlateManifest(plates={1: {0: 5}, 2: {0: 1}}, requested={0: 6}), wanted, items, [1, 2]) is not None


async def test_a_failed_locked_plate_requeues_as_a_new_fixed_job(client, upload_3mf, create_printer, session_factory, tmp_path):
    p = await create_printer(name="P")
    pid = await _project(client)
    proj = await upload_3mf(filename="kit.3mf", data=_two_plate_3mf())
    locked = (await _add(client, pid, proj, kind="3mf_plate", source_ref="2", pack_rule="locked")).json()
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        job_id = (await client.post(f"/api/v1/projects/{pid}/generate", json={"eligible_printer_ids": [p]})).json()["jobs"][0]["id"]
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.status, job.assigned_printer_id = "complete", p
        (await s.get(Printer, p)).awaiting_plate_clear = True
        await s.commit()
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        r = await client.post(f"/api/v1/printers/{p}/plate-cleared", json={
            "job_id": job_id, "results": [{"project_item_id": locked["id"], "quantity_failed": 1}]})
    (new_id,) = r.json()["requeued_job_ids"]
    async with session_factory() as s:
        new = await s.get(Job, new_id)
    assert (new.uploaded_file_id, new.plate_number, json.loads(new.project_item_quantities)) == (proj, 2, {str(locked["id"]): 1})
