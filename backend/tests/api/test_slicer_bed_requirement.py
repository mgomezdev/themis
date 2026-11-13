"""BIZ-375: stored footprints survive imports and are enforced again after fleet changes."""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.models import Job, JobPrinterConfig, Printer
from app.services import bed_requirement, model_targets
from app.services.queue_engine import QueueEngine
from app.services.slicer_service import SlicerService
from tests.services.test_queue_engine import _make_mock_printer_manager


async def test_oversize_plate_never_materializes_on_a_small_or_new_small_printer(
        client, upload_3mf, create_printer, session_factory):
    file = await upload_3mf()
    small = await create_printer(name="Small", bed_x_mm=256, bed_y_mm=256)
    big = await create_printer(name="Big", bed_x_mm=350, bed_y_mm=350)
    r = await client.post("/api/v1/jobs", json={"uploaded_file_id": file, "min_bed_mm": {"x": 300, "y": 300, "z": 50},
        "model_targets": [{"model_uuid": None, "print_profile": "Standard"}]})
    assert r.status_code == 201, r.text
    assert r.json()["min_bed_mm"] == {"x": 300, "y": 300, "z": 50}
    jid = r.json()["id"]
    new_small = await create_printer(name="New small", bed_x_mm=200, bed_y_mm=200)
    async with session_factory() as s:
        for pid in (small, big, new_small):
            await model_targets.sync_targets_for_printer(s, await s.get(Printer, pid))
        await s.commit()
        assert list((await s.execute(select(JobPrinterConfig.printer_id).where(JobPrinterConfig.job_id == jid))).scalars()) == [big]
        printer = await s.get(Printer, big)
        printer.bed_x_mm = printer.bed_y_mm = 200
        await model_targets.sync_targets_for_printer(s, printer)
        await s.commit()
        assert not (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == jid))).scalars().all()


async def test_explicit_printer_is_checked_at_claim_and_explained_when_blocked(client, upload_3mf, create_printer, session_factory):
    file = await upload_3mf()
    pid = await create_printer(name="Small", bed_x_mm=256, bed_y_mm=256)
    r = await client.post("/api/v1/jobs", json={"uploaded_file_id": file, "min_bed_mm": {"x": 300, "y": 300},
        "printer_configs": [{"printer_id": pid, "print_profile": "Standard"}]})
    jid = r.json()["id"]
    engine = QueueEngine(session_factory, _make_mock_printer_manager([pid]), MagicMock(spec=SlicerService))
    async with session_factory() as s:
        await engine._try_claim_for_printer(s, pid)
        job = await s.get(Job, jid)
        assert job.status == "queued" and job.assigned_printer_id is None
        printer = await s.get(Printer, pid)
        configs = list((await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == jid))).scalars())
        reason, _ = await engine._no_match_reason(s, job, configs, {pid: printer}, {pid: printer}, {})
        assert "bed too small" in reason and "300 x 300" in reason


async def test_rotated_fit_and_known_or_unknown_height(session_factory, create_printer):
    pid = await create_printer(bed_x_mm=180, bed_y_mm=300, bed_z_mm=80)
    async with session_factory() as s:
        printer = await s.get(Printer, pid)
        assert await bed_requirement.reason(s, {"x": 280, "y": 170, "z": 70}, printer) is None
        assert "bed too small" in await bed_requirement.reason(s, {"x": 280, "y": 170, "z": 81}, printer)
        printer.bed_z_mm = None
        assert await bed_requirement.reason(s, {"x": 280, "y": 170, "z": 10000}, printer) is None


@pytest.mark.parametrize("size", [{"x": 0, "y": 20}, {"x": -1, "y": 20}, {"x": "nan", "y": 20},
                                  {"x": 20}, {"x": 20, "y": 20, "z": -1}, {"x": 20, "y": 20, "extra": 1}])
async def test_invalid_footprints_are_rejected(client, upload_3mf, size):
    file = await upload_3mf()
    r = await client.post("/api/v1/jobs", json={"uploaded_file_id": file, "min_bed_mm": size})
    assert r.status_code == 422


async def test_project_item_footprint_round_trip_and_clear(client, upload_3mf):
    pid = (await client.post("/api/v1/projects", json={"name": "Footprint"})).json()["id"]
    fid = await upload_3mf()
    r = await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "min_bed_mm": {"x": 300, "y": 200}})
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["min_bed_mm"] == {"x": 300, "y": 200}
    assert (await client.put(f"/api/v1/projects/{pid}/items/{item['id']}", json={"min_bed_mm": None})).json()["min_bed_mm"] is None
