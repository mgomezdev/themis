from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import Job, JobPrinterConfig, Project, ProjectGroup, ProjectItem, SlicerSend


async def job_body(upload_3mf,create_printer):
    return {"send_id":str(uuid4()),"kind":"job","job":{"uploaded_file_id":await upload_3mf(),
        "model_targets":[{"model_uuid":None,"print_profile":"Standard"}]}}


async def test_replay_returns_one_job_and_different_payload_conflicts(client,upload_3mf,create_printer,session_factory):
    await create_printer()
    body = await job_body(upload_3mf,create_printer)
    first = await client.post("/api/v1/slicer-sends",json=body)
    assert first.status_code == 201,first.text
    second = await client.post("/api/v1/slicer-sends",json=body)
    assert second.status_code == 200 and second.json() == first.json()
    body["job"]["min_bed_mm"] = {"x":300,"y":300}
    assert (await client.post("/api/v1/slicer-sends",json=body)).status_code == 409
    async with session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(Job))).scalar() == 1
        job = await s.get(Job,first.json()["created"]["job_id"])
        assert job.send_id == body["send_id"] and job.source_app == "orcaslicer"


async def test_update_replaces_file_targets_and_footprint_but_never_started_work(client,upload_3mf,create_printer,session_factory):
    await create_printer()
    b = await create_printer(name="B")
    body = await job_body(upload_3mf,create_printer)
    first = (await client.post("/api/v1/slicer-sends",json=body)).json()
    jid = first["created"]["job_id"]
    body["update"] = True
    newfile = await upload_3mf(filename="new.3mf",data=b"new model")
    body["job"] = {"uploaded_file_id":newfile,"printer_configs":[{"printer_id":b,"print_profile":"Fine",
        "overrides":{"wall_loops":3}}],"min_bed_mm":{"x":200,"y":200},
        "printer_preference":{"entries":[b],"strict":True}}
    changed = await client.post("/api/v1/slicer-sends",json=body)
    assert changed.status_code == 200,changed.text
    assert changed.json()["created"]["job_id"] == jid
    async with session_factory() as s:
        job = await s.get(Job,jid)
        assert job.uploaded_file_id == newfile and job.status == "queued" and job.min_bed_mm["x"] == 200
        assert [(c.printer_id,c.overrides) for c in (await s.execute(select(JobPrinterConfig))).scalars()] == [(b,{"wall_loops":"3"})]
        job.status = "slicing"
        await s.commit()
    body["job"]["plate_number"] = 2
    assert (await client.post("/api/v1/slicer-sends",json=body)).status_code == 409
    looked = (await client.get("/api/v1/slicer-sends/"+body["send_id"])).json()
    assert not looked["can_update"] and looked["status"] == "slicing"


async def test_project_import_and_retries_are_one_atomic_group_and_item_set(client,upload_3mf,session_factory):
    file = await upload_3mf(filename="a.stl",data=b"solid a\nendsolid\n")
    body = {"send_id":str(uuid4()),"kind":"project","project_name":"New", "group":{"targets":[{"target_key":"any"}]},
            "items":[{"file_id":file,"quantity":2,"kind":"stl"}]}
    first = await client.post("/api/v1/slicer-sends",json=body)
    assert first.status_code == 201,first.text
    assert (await client.post("/api/v1/slicer-sends",json=body)).json() == first.json()
    body["update"] = True
    body["items"][0]["quantity"] = 3
    changed = await client.post("/api/v1/slicer-sends",json=body)
    assert changed.status_code == 200,changed.text
    async with session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(Project))).scalar() == 1
        assert (await s.execute(select(func.count()).select_from(ProjectGroup))).scalar() == 2 # default + imported
        items = list((await s.execute(select(ProjectItem))).scalars())
        assert len(items) == 1 and items[0].quantity == 3 and items[0].send_id


async def test_invalid_project_item_rolls_back_project_group_items_and_send_record(client,upload_3mf,session_factory):
    file = await upload_3mf(filename="a.stl",data=b"solid a\nendsolid\n")
    body = {"send_id":str(uuid4()),"kind":"project","project_name":"New", "group":{"targets":[{"target_key":"any"}]},
            "items":[{"file_id":file},{"file_id":999999}]}
    assert (await client.post("/api/v1/slicer-sends",json=body)).status_code == 404
    async with session_factory() as s:
        for model in (Project,ProjectGroup,ProjectItem,SlicerSend):
            assert (await s.execute(select(func.count()).select_from(model))).scalar() == 0


@pytest.mark.parametrize("scope",["jobs:write","projects:write"])
async def test_send_enforces_destination_scope(client,session_factory,scope):
    from app.models import ApiKey
    async with session_factory() as s:
        for key in (await s.execute(select(ApiKey))).scalars():
            key.scopes = ["files:write"]
        await s.commit()
    body = {"send_id":str(uuid4()),"kind":"job","job":{"uploaded_file_id":1}}
    if scope == "projects:write":
        body = {"send_id":str(uuid4()),"kind":"project","project_name":"P","group":{},"items":[{"file_id":1}]}
    assert (await client.post("/api/v1/slicer-sends",json=body)).status_code == 403


async def test_concurrent_identical_sends_commit_one_job(client,upload_3mf,create_printer,session_factory):
    import asyncio
    body = await job_body(upload_3mf,create_printer)
    responses = await asyncio.gather(*(client.post("/api/v1/slicer-sends",json=body) for _ in range(2)))
    assert sorted(r.status_code for r in responses) == [200,201]
    assert responses[0].json()["created"] == responses[1].json()["created"]
    async with session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(Job))).scalar() == 1


async def test_update_regenerates_only_unstarted_import_work_and_protects_started_work(client,upload_3mf,session_factory,tmp_path):
    from unittest.mock import patch
    from tests.fake_providers import FakeProjectConstructor
    fid = await upload_3mf(filename="a.stl",data=b"solid a\nendsolid\n")
    body = {"send_id":str(uuid4()),"kind":"project","project_name":"New","group":{"targets":[{"target_key":"any"}]},
            "items":[{"file_id":fid,"quantity":1}]}
    first = (await client.post("/api/v1/slicer-sends",json=body)).json()
    pid = first["created"]["project_id"]
    with patch("app.config.get_library_dir",return_value=tmp_path/"library"), \
         patch("app.api.routes.projects.get_project_constructor",return_value=FakeProjectConstructor()):
        generated = await client.post(f"/api/v1/projects/{pid}/generate",json={})
        assert generated.status_code == 200,generated.text
        original = generated.json()["jobs"][0]["id"]
        assert (await client.get("/api/v1/slicer-sends/"+body["send_id"])).json()["can_update"]
        body["update"] = True
        body["items"][0]["quantity"] = 2
        result = await client.post("/api/v1/slicer-sends",json=body)
        assert result.status_code == 200,result.text
        new_ids = result.json()["created"]["job_ids"]
        assert new_ids and original not in new_ids
        async with session_factory() as s:
            assert (await s.get(Job,original)).status == "cancelled"
            job = await s.get(Job,new_ids[0])
            job.status = "slicing"
            await s.commit()
        body["items"][0]["quantity"] = 3
        assert (await client.post("/api/v1/slicer-sends",json=body)).status_code == 409


async def test_failed_repack_restores_old_jobs_items_ledger_and_removes_new_artifacts(client,upload_3mf,session_factory,tmp_path):
    from unittest.mock import patch
    from tests.fake_providers import FakeProjectConstructor
    from app.services.providers.contract import ProviderError,ErrorCode
    fid = await upload_3mf(filename="a.stl",data=b"solid a\nendsolid\n")
    body = {"send_id":str(uuid4()),"kind":"project","project_name":"New","group":{"targets":[{"target_key":"any"}]},
            "items":[{"file_id":fid,"quantity":1}]}
    first = (await client.post("/api/v1/slicer-sends",json=body)).json()
    pid = first["created"]["project_id"]
    constructor = FakeProjectConstructor()
    library = tmp_path/"library"
    with patch("app.config.get_library_dir",return_value=library), \
         patch("app.api.routes.projects.get_project_constructor",return_value=constructor):
        generated = await client.post(f"/api/v1/projects/{pid}/generate",json={})
        assert generated.status_code == 200,generated.text
        jid = generated.json()["jobs"][0]["id"]
        before = {p.relative_to(library) for p in library.rglob("*") if p.is_file()}
        constructor.outputs = [b"3mf",ProviderError(ErrorCode.UNSUPPORTED_INPUT,"cannot pack second material")]
        body.update(update=True,items=[{"file_id":fid,"filament_type":kind,"filament_subtype":"any"} for kind in ("PLA","PETG")])
        result = await client.post("/api/v1/slicer-sends",json=body)
        assert result.status_code == 422,result.text
        assert {p.relative_to(library) for p in library.rglob("*") if p.is_file()} == before
    async with session_factory() as s:
        assert (await s.get(Job,jid)).status == "queued"
        assert (await s.execute(select(func.count()).select_from(Job))).scalar() == 1
        assert (await s.execute(select(func.count()).select_from(ProjectItem))).scalar() == 1
        assert (await s.get(SlicerSend,body["send_id"])).result == first["created"]
