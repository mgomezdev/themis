"""Create/edit a job eligible on any printer of a make/model (BIZ-187)."""
from unittest.mock import patch

from sqlalchemy import select

from app.models import Job, JobModelTarget, JobPrinterConfig

P1S = "Bambu Lab P1S 0.4 nozzle"
X1C = "Bambu Lab X1 Carbon 0.4 nozzle"


def _target(profile: str = P1S, **kw) -> dict:
    return {"machine_profile": profile, "print_profile": "0.20mm", "filament_type": "PLA",
            "filament_color": "any", **kw}


async def _post(client, body: dict):
    with patch("app.api.routes.jobs.queue_engine"):
        return await client.post("/api/v1/jobs", json=body)


async def _configs(session_factory, job_id: int) -> list[JobPrinterConfig]:
    async with session_factory() as s:
        return list((await s.execute(
            select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id).order_by(JobPrinterConfig.printer_id)
        )).scalars().all())


async def test_create_with_model_target_only_materializes_matching_printers(
        client, session_factory, upload_3mf, create_printer):
    p1 = await create_printer(name="A", current_orca_printer_profile=P1S, orca_printer_profiles=[P1S])
    await create_printer(name="B", current_orca_printer_profile=X1C, orca_printer_profiles=[X1C])
    p3 = await create_printer(name="C", current_orca_printer_profile=P1S, orca_printer_profiles=[P1S])

    resp = await _post(client, {"uploaded_file_id": await upload_3mf(), "model_targets": [_target()]})

    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]
    cfgs = await _configs(session_factory, job_id)
    assert [c.printer_id for c in cfgs] == [p1, p3]
    assert all(c.model_target_id is not None and c.filament_type == "PLA" for c in cfgs)
    details = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()
    assert details["model_targets"] == [{
        "machine_profile": P1S, "print_profile": "0.20mm", "filament_profile": None, "filament_id": None,
        "filament_type": "PLA", "filament_color": "any", "filament_map": None}]
    assert {c["printer_id"] for c in details["printer_configs"]} == {p1, p3}
    assert all(c["from_model_target"] for c in details["printer_configs"])
    listed = next(j for j in (await client.get("/api/v1/jobs")).json() if j["id"] == job_id)
    assert listed["model_targets"][0]["machine_profile"] == P1S
    assert {p["id"] for p in listed["eligible_printers"]} == {p1, p3}


async def test_create_with_target_and_no_matching_printer_still_queues(client, upload_3mf):
    resp = await _post(client, {"uploaded_file_id": await upload_3mf(), "model_targets": [_target()]})
    assert resp.status_code == 201
    assert resp.json()["status"] == "queued"


async def test_create_needs_printer_or_target(client, upload_3mf):
    resp = await _post(client, {"uploaded_file_id": await upload_3mf()})
    assert resp.status_code == 422


async def test_target_rejects_tool_index_pinning(client, upload_3mf):
    resp = await _post(client, {"uploaded_file_id": await upload_3mf(), "model_targets": [
        _target(filament_map=[{"model_filament": 1, "tool_index": 2}])]})
    assert resp.status_code == 422


async def test_duplicate_target_model_rejected(client, upload_3mf):
    resp = await _post(client, {"uploaded_file_id": await upload_3mf(), "model_targets": [_target(), _target()]})
    assert resp.status_code == 422


async def test_explicit_printer_and_target_do_not_duplicate_a_printer(
        client, session_factory, upload_3mf, create_printer):
    pid = await create_printer(current_orca_printer_profile=P1S, orca_printer_profiles=[P1S])
    resp = await _post(client, {
        "uploaded_file_id": await upload_3mf(),
        "printer_configs": [{"printer_id": pid, "print_profile": "0.28mm", "filament_type": "PETG",
                             "filament_color": "any"}],
        "model_targets": [_target()],
    })
    cfgs = await _configs(session_factory, resp.json()["id"])
    assert len(cfgs) == 1 and cfgs[0].print_profile == "0.28mm" and cfgs[0].model_target_id is None


async def test_patch_replaces_targets_and_rematerializes(client, session_factory, upload_3mf, create_printer):
    p1 = await create_printer(name="A", current_orca_printer_profile=P1S, orca_printer_profiles=[P1S])
    x1 = await create_printer(name="B", current_orca_printer_profile=X1C, orca_printer_profiles=[X1C])
    job_id = (await _post(client, {"uploaded_file_id": await upload_3mf(), "model_targets": [_target()]})).json()["id"]

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.patch(f"/api/v1/jobs/{job_id}/configs", json={"model_targets": [_target(X1C)]})

    assert resp.status_code == 200, resp.text
    assert [c.printer_id for c in await _configs(session_factory, job_id)] == [x1]
    async with session_factory() as s:
        targets = (await s.execute(select(JobModelTarget).where(JobModelTarget.job_id == job_id))).scalars().all()
        assert [t.machine_profile for t in targets] == [X1C]
    assert p1 != x1


async def test_patch_to_explicit_printer_drops_the_targets(client, session_factory, upload_3mf, create_printer):
    pid = await create_printer(current_orca_printer_profile=P1S, orca_printer_profiles=[P1S])
    job_id = (await _post(client, {"uploaded_file_id": await upload_3mf(), "model_targets": [_target()]})).json()["id"]

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.patch(f"/api/v1/jobs/{job_id}/configs", json={"printer_configs": [
            {"printer_id": pid, "print_profile": "0.20mm", "filament_type": "any", "filament_color": "any"}]})

    assert resp.status_code == 200
    async with session_factory() as s:
        assert (await s.execute(select(JobModelTarget).where(JobModelTarget.job_id == job_id))).first() is None
    assert (await client.get(f"/api/v1/jobs/{job_id}/details")).json()["model_targets"] == []


async def test_patch_needs_printer_or_target(client, create_job):
    job_id = await create_job()
    resp = await client.patch(f"/api/v1/jobs/{job_id}/configs", json={})
    assert resp.status_code == 422


async def test_deleting_a_matching_printer_does_not_block_a_targeted_job(
        client, session_factory, upload_3mf, create_printer):
    pid = await create_printer(current_orca_printer_profile=P1S, orca_printer_profiles=[P1S])
    job_id = (await _post(client, {"uploaded_file_id": await upload_3mf(), "model_targets": [_target()]})).json()["id"]

    resp = await client.delete(f"/api/v1/printers/{pid}")

    assert resp.status_code == 204
    async with session_factory() as s:
        assert (await s.get(Job, job_id)).status == "queued"


async def test_target_without_a_filament_preset_stores_none_not_the_type(
        client, session_factory, upload_3mf, create_printer):
    """Regression: "any"/a bare type must never become a preset name — the slicer would be asked for it."""
    await create_printer(current_orca_printer_profile=P1S, orca_printer_profiles=[P1S])
    job_id = (await _post(client, {"uploaded_file_id": await upload_3mf(),
                                   "model_targets": [_target(filament_type="any")]})).json()["id"]
    async with session_factory() as s:
        target = (await s.execute(select(JobModelTarget).where(JobModelTarget.job_id == job_id))).scalar_one()
        assert target.filament_profile is None
    assert all(c.filament_profile is None for c in await _configs(session_factory, job_id))


async def test_duplicate_printer_in_one_job_is_refused(client, upload_3mf, create_printer):
    pid = await create_printer()
    cfg = {"printer_id": pid, "print_profile": "0.20mm", "filament_type": "any", "filament_color": "any"}
    resp = await _post(client, {"uploaded_file_id": await upload_3mf(), "printer_configs": [cfg, cfg]})
    assert resp.status_code == 422
