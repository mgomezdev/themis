"""BIZ-357: an "Any printer" target (a model target with model_uuid null) is eligible on every printer, including printers added
after the job and printers with no registry model."""
from unittest.mock import patch

from sqlalchemy import select

from app.models import JobModelTarget, JobPrinterConfig, Printer
from app.services import model_targets

OTHER = {"printer_type": "elegoo_centauri"}
ANY = {"model_uuid": None, "print_profile": "0.20mm", "filament_type": "PLA", "filament_subtype": "any"}


async def _post(client, body: dict):
    with patch("app.api.routes.jobs.queue_engine"):
        return await client.post("/api/v1/jobs", json=body)


async def _printer_ids(session_factory, job_id: int) -> list[int]:
    async with session_factory() as s:
        return sorted((await s.execute(
            select(JobPrinterConfig.printer_id).where(JobPrinterConfig.job_id == job_id))).scalars().all())


async def test_any_printer_materializes_on_every_printer_and_reads_back(client, session_factory, upload_3mf, create_printer):
    a = await create_printer(name="A")
    b = await create_printer(name="B", **OTHER)
    resp = await _post(client, {"uploaded_file_id": await upload_3mf(), "model_targets": [ANY]})
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]
    assert await _printer_ids(session_factory, job_id) == [a, b]
    (target,) = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()["model_targets"]
    assert (target["model_uuid"], target["model_label"]) == (None, "Any printer")


async def test_a_printer_added_later_or_without_a_model_gets_the_job(client, session_factory, upload_3mf, create_printer):
    await create_printer(name="A")
    job_id = (await _post(client, {"uploaded_file_id": await upload_3mf(), "model_targets": [ANY]})).json()["id"]
    late = await create_printer(name="Late", **OTHER)
    async with session_factory() as s:                   # a printer the registry has no model for
        p = await s.get(Printer, late)
        p.model_uuid = None
        await s.commit()
        await model_targets.sync_targets_for_printer(s, p)
        await s.commit()
    assert late in await _printer_ids(session_factory, job_id)


async def test_an_explicit_printer_beside_any_printer_is_not_duplicated(client, session_factory, upload_3mf, create_printer):
    a = await create_printer(name="A")
    b = await create_printer(name="B")
    body = {"uploaded_file_id": await upload_3mf(), "model_targets": [ANY],
            "printer_configs": [{"printer_id": a, "print_profile": "0.12mm", "filament_type": "PLA", "filament_subtype": "any"}]}
    job_id = (await _post(client, body)).json()["id"]
    async with session_factory() as s:
        cfgs = {c.printer_id: c for c in (await s.execute(
            select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))).scalars()}
    assert set(cfgs) == {a, b}
    assert (cfgs[a].print_profile, cfgs[a].model_target_id) == ("0.12mm", None)       # the explicit pick wins
    assert cfgs[b].model_target_id is not None


async def test_two_any_printer_targets_are_refused(client, upload_3mf):
    resp = await _post(client, {"uploaded_file_id": await upload_3mf(), "model_targets": [ANY, ANY]})
    assert resp.status_code == 422


async def test_a_blank_model_uuid_is_still_refused(client, upload_3mf):
    resp = await _post(client, {"uploaded_file_id": await upload_3mf(), "model_targets": [{**ANY, "model_uuid": "  "}]})
    assert resp.status_code == 422


async def test_project_generate_with_any_printer_packs_for_the_smallest_bed_and_targets_every_printer(
        client, session_factory, upload_3mf, create_printer, tmp_path):
    from tests.fake_providers import FakeProjectConstructor
    big = await create_printer(name="Big", bed_x_mm=350.0, bed_y_mm=320.0)
    await create_printer(name="Small", bed_x_mm=180.0, bed_y_mm=190.0, **OTHER)
    project_id = (await client.post("/api/v1/projects", json={"name": "Any"})).json()["id"]
    fid = await upload_3mf(filename="a.stl", data=b"solid a\nendsolid\n")
    await client.post(f"/api/v1/projects/{project_id}/items", json={"file_id": fid, "quantity": 1})
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        resp = await client.post(f"/api/v1/projects/{project_id}/generate", json={"eligible_any_printer": True})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["pack_bed_x"], resp.json()["pack_bed_y"]) == (180.0, 190.0)
    job_id = resp.json()["jobs"][0]["id"]
    async with session_factory() as s:
        targets = (await s.execute(select(JobModelTarget).where(JobModelTarget.job_id == job_id))).scalars().all()
    assert [t.model_uuid for t in targets] == [None]
    assert big in await _printer_ids(session_factory, job_id) and len(await _printer_ids(session_factory, job_id)) == 2


async def test_a_printer_with_a_model_added_later_gets_the_job_and_keeps_it_on_resync(
        client, session_factory, upload_3mf, create_printer):
    job_id = (await _post(client, {"uploaded_file_id": await upload_3mf(), "model_targets": [ANY]})).json()["id"]
    late = await create_printer(name="Late")                      # has a registry model
    async def _sync():
        async with session_factory() as s:
            await model_targets.sync_targets_for_printer(s, await s.get(Printer, late))
            await s.commit()

    async def _config():
        async with session_factory() as s:
            return (await s.execute(select(JobPrinterConfig).where(
                JobPrinterConfig.job_id == job_id, JobPrinterConfig.printer_id == late))).scalar_one()

    await _sync()
    async with session_factory() as s:     # what a claim leaves on the row; deleting + re-adding the config would lose it
        cfg = await s.get(JobPrinterConfig, (await _config()).id)
        cfg.slot_plan = [{"model_filament": None, "slot": 0}]
        await s.commit()
    await _sync()
    assert (await _config()).slot_plan == [{"model_filament": None, "slot": 0}]


async def test_a_printer_that_loses_its_model_drops_model_configs_but_keeps_any_printer(
        client, session_factory, upload_3mf, create_printer, model_uuid_of):
    p = await create_printer(name="A")
    model = await model_uuid_of(p)
    by_model = (await _post(client, {"uploaded_file_id": await upload_3mf(),
                                     "model_targets": [{**ANY, "model_uuid": model}]})).json()["id"]
    by_any = (await _post(client, {"uploaded_file_id": await upload_3mf(), "model_targets": [ANY]})).json()["id"]
    async with session_factory() as s:
        printer = await s.get(Printer, p)
        printer.model_uuid = None
        await s.commit()
        await model_targets.sync_targets_for_printer(s, printer)
        await s.commit()
    assert p not in await _printer_ids(session_factory, by_model)
    assert p in await _printer_ids(session_factory, by_any)
