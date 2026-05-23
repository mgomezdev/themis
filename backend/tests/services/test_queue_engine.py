# backend/tests/services/test_queue_engine.py
import asyncio
import os
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.database import Base
from app.models import Job, JobPrinterConfig, UploadedFile, GcodeFile
from app.services.queue_engine import QueueEngine
from app.services.printer_manager import PrinterManager
from app.services.slicer_service import SliceError


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _make_mock_printer_manager(printer_ids_ready: list[int]) -> PrinterManager:
    mgr = MagicMock(spec=PrinterManager)
    mgr.get_all_printer_ids.return_value = printer_ids_ready
    mgr.is_printer_ready.side_effect = lambda pid: pid in printer_ids_ready
    mock_client = MagicMock()
    mock_client.file_upload_supported = False
    mock_client.start_print.return_value = True
    mgr.get_client.return_value = mock_client
    return mgr


async def _seed_job(factory, printer_id: int, status: str = "queued") -> int:
    async with factory() as session:
        f = UploadedFile(
            original_filename="test.3mf",
            stored_path="/data/uploads/x/model.3mf",
            plates=[],
            uploaded_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(f)
        await session.flush()
        j = Job(
            uploaded_file_id=f.id,
            plate_number=1,
            queue_position=1.0,
            status=status,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(j)
        await session.flush()
        c = JobPrinterConfig(
            job_id=j.id,
            printer_id=printer_id,
            print_profile="0.20mm",
            filament_profile="PLA",
        )
        session.add(c)
        await session.commit()
        return j.id


@pytest.mark.asyncio
async def test_claim_transitions_job_to_slicing(db, tmp_path):
    mgr = _make_mock_printer_manager([1])
    mock_slicer = MagicMock()
    gcode_path = str(tmp_path / "output.gcode")
    Path(gcode_path).write_text("G28\n")
    mock_slicer.slice.return_value = gcode_path

    qe = QueueEngine(db, mgr, mock_slicer)
    job_id = await _seed_job(db, printer_id=1)

    await qe._process_queue()
    await asyncio.sleep(0.1)  # allow background task to run

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status in ("slicing", "uploading", "printing")


@pytest.mark.asyncio
async def test_no_ready_printers_leaves_job_queued(db):
    mgr = _make_mock_printer_manager([])  # no ready printers
    qe = QueueEngine(db, mgr, MagicMock())
    job_id = await _seed_job(db, printer_id=1)

    await qe._process_queue()

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == "queued"


@pytest.mark.asyncio
async def test_slice_failure_marks_config_and_requeues(db):
    mgr = _make_mock_printer_manager([1])
    mock_slicer = MagicMock()
    mock_slicer.slice.side_effect = SliceError("Profile not found")

    qe = QueueEngine(db, mgr, mock_slicer)
    job_id = await _seed_job(db, printer_id=1)

    await qe._process_queue()
    await asyncio.sleep(0.1)

    async with db() as session:
        job = await session.get(Job, job_id)
        # All configs failed → job should be 'failed'
        assert job.status == "failed"


@pytest.mark.asyncio
async def test_slice_failure_requeues_when_other_printers_available(db):
    # Printer 1 fails, but printer 2 is also eligible
    mgr = _make_mock_printer_manager([1])
    mock_slicer = MagicMock()
    mock_slicer.slice.side_effect = SliceError("fail")

    qe = QueueEngine(db, mgr, mock_slicer)

    async with db() as session:
        f = UploadedFile(
            original_filename="test.3mf",
            stored_path="/data/uploads/x/model.3mf",
            plates=[],
            uploaded_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(f)
        await session.flush()
        j = Job(
            uploaded_file_id=f.id,
            plate_number=1,
            queue_position=1.0,
            status="queued",
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(j)
        await session.flush()
        # Two printer configs — printer 1 fails but printer 2 is available
        session.add(JobPrinterConfig(job_id=j.id, printer_id=1, print_profile="0.20mm", filament_profile="PLA"))
        session.add(JobPrinterConfig(job_id=j.id, printer_id=2, print_profile="0.20mm", filament_profile="PLA"))
        await session.commit()
        job_id = j.id

    await qe._process_queue()
    await asyncio.sleep(0.1)

    async with db() as session:
        job = await session.get(Job, job_id)
        # Printer 1 slice failed, but printer 2's config is still valid → job requeued
        assert job.status == "queued"


@pytest.mark.asyncio
async def test_handle_print_complete_transitions_job(db):
    mgr = _make_mock_printer_manager([])
    qe = QueueEngine(db, mgr, MagicMock())
    job_id = await _seed_job(db, printer_id=1, status="printing")

    # Set assigned_printer_id
    async with db() as session:
        job = await session.get(Job, job_id)
        job.assigned_printer_id = 1
        await session.commit()

    await qe.handle_print_complete(1)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == "complete"
