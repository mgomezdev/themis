# backend/tests/services/test_queue_engine.py
import asyncio
import os
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.database import Base
from app.models import Job, JobPrinterConfig, Printer, UploadedFile, GcodeFile
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
        # Ensure the target printer exists (queue engine checks queue_on before claiming)
        if await session.get(Printer, printer_id) is None:
            session.add(Printer(
                id=printer_id,
                name=f"Printer {printer_id}",
                printer_type="elegoo_centauri",
                connection_config={},
                current_orca_printer_profile="Test Machine Preset",
            ))
            await session.flush()
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
    await asyncio.sleep(0.1)  # allow background task to run through to printing

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == "printing"


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
async def test_queue_off_printer_does_not_claim(db):
    # A ready printer with queue_on=False must behave like no eligible printer:
    # the top job stays queued.
    mgr = _make_mock_printer_manager([1])
    qe = QueueEngine(db, mgr, MagicMock())
    job_id = await _seed_job(db, printer_id=1)

    async with db() as session:
        printer = await session.get(Printer, 1)
        printer.queue_on = False
        await session.commit()

    await qe._process_queue()
    await asyncio.sleep(0.1)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == "queued"


@pytest.mark.asyncio
async def test_slice_failure_blocks_job(db):
    mgr = _make_mock_printer_manager([1])
    mock_slicer = MagicMock()
    mock_slicer.slice.side_effect = SliceError("Profile not found")

    qe = QueueEngine(db, mgr, mock_slicer)
    job_id = await _seed_job(db, printer_id=1)

    await qe._process_queue()
    await asyncio.sleep(0.1)

    async with db() as session:
        job = await session.get(Job, job_id)
        # A slicing issue blocks the job (per queue policy), with a reason.
        assert job.status == "blocked"
        assert "slicing failed" in (job.block_reason or "")


@pytest.mark.asyncio
async def test_slice_failure_requeues_when_other_printers_available(db):
    # Printer 1 fails, but printer 2 is also eligible
    mgr = _make_mock_printer_manager([1])
    mock_slicer = MagicMock()
    mock_slicer.slice.side_effect = SliceError("fail")

    qe = QueueEngine(db, mgr, mock_slicer)

    async with db() as session:
        for pid in (1, 2):
            session.add(Printer(id=pid, name=f"P{pid}", printer_type="elegoo_centauri",
                                connection_config={}, current_orca_printer_profile="Test Machine Preset"))
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
        # Printer 1's slice failed → blocked; printer 2 (config not failed) can still
        # rescue it on a later check.
        assert job.status == "blocked"
        cfgs = (await session.execute(
            select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))).scalars().all()
        by_printer = {c.printer_id: c.slice_failed for c in cfgs}
        assert by_printer[1] is True and by_printer[2] is False


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


async def _set_filament(db, job_id, printer_id, req_type, req_color, loaded):
    async with db() as session:
        cfg = (await session.execute(select(JobPrinterConfig).where(
            JobPrinterConfig.job_id == job_id,
            JobPrinterConfig.printer_id == printer_id))).scalar_one()
        cfg.filament_type = req_type
        cfg.filament_color = req_color
        (await session.get(Printer, printer_id)).loaded_filaments = loaded
        await session.commit()


@pytest.mark.asyncio
async def test_filament_mismatch_blocks_job(db):
    mgr = _make_mock_printer_manager([1])
    qe = QueueEngine(db, mgr, MagicMock())
    job_id = await _seed_job(db, printer_id=1)
    await _set_filament(db, job_id, 1, "PETG", "#FF0000", [{"slot": 0, "type": "PLA", "color": "#FFFFFF"}])

    await qe._process_queue()
    await asyncio.sleep(0.05)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == "blocked"
        assert "filament" in (job.block_reason or "").lower()


@pytest.mark.asyncio
async def test_filament_match_allows_claim(db, tmp_path):
    mgr = _make_mock_printer_manager([1])
    mock_slicer = MagicMock()
    gp = str(tmp_path / "o.gcode"); Path(gp).write_text("G28")
    mock_slicer.slice.return_value = gp
    qe = QueueEngine(db, mgr, mock_slicer)
    job_id = await _seed_job(db, printer_id=1)
    # case- and #-insensitive match
    await _set_filament(db, job_id, 1, "PLA", "#FFFFFF", [{"type": "pla", "color": "FFFFFF"}])

    await qe._process_queue()
    await asyncio.sleep(0.1)

    async with db() as session:
        assert (await session.get(Job, job_id)).status == "printing"


@pytest.mark.asyncio
async def test_slice_uses_filament_profile_from_loaded_slot(db, tmp_path):
    """When the job config has no filament_profile, the matched loaded slot's
    filament_profile is used as the fallback."""
    mgr = _make_mock_printer_manager([1])
    mock_slicer = MagicMock()
    gp = str(tmp_path / "o.gcode"); Path(gp).write_text("G28")
    mock_slicer.slice.return_value = gp
    qe = QueueEngine(db, mgr, mock_slicer)
    job_id = await _seed_job(db, printer_id=1)
    # Clear the job-level filament_profile so the slot's preset is the only option.
    async with db() as session:
        cfg = (await session.execute(select(JobPrinterConfig).where(
            JobPrinterConfig.job_id == job_id,
            JobPrinterConfig.printer_id == 1))).scalar_one()
        cfg.filament_profile = None
        await session.commit()
    # Job asks for PLA white; the printer's loaded slot provides it with a real preset.
    await _set_filament(db, job_id, 1, "PLA", "#FFFFFF",
                        [{"type": "PLA", "color": "#FFFFFF",
                          "filament_profile": "Generic PLA @Test"}])

    await qe._process_queue()
    await asyncio.sleep(0.1)

    assert mock_slicer.slice.call_count == 1
    req = mock_slicer.slice.call_args[0][0]
    assert req.filament_presets == ["Generic PLA @Test"]


@pytest.mark.asyncio
async def test_print_start_marks_printer_awaiting_plate_clear(db, tmp_path):
    """When a job starts printing, the printer is flagged not-ready so it won't
    auto-claim the next job until the user marks the plate cleared."""
    mgr = _make_mock_printer_manager([1])
    mock_slicer = MagicMock()
    gp = str(tmp_path / "o.gcode"); Path(gp).write_text("G28")
    mock_slicer.slice.return_value = gp
    qe = QueueEngine(db, mgr, mock_slicer)
    job_id = await _seed_job(db, printer_id=1)
    await _set_filament(db, job_id, 1, "PLA", "#FFFFFF", [{"type": "PLA", "color": "#FFFFFF"}])

    await qe._process_queue()
    await asyncio.sleep(0.1)

    mgr.set_awaiting_plate_clear.assert_any_call(1, True)
    async with db() as session:
        printer = await session.get(Printer, 1)
        assert printer.awaiting_plate_clear is True


@pytest.mark.asyncio
async def test_blocked_job_unblocks_when_correct_filament_loaded(db, tmp_path):
    mgr = _make_mock_printer_manager([1])
    mock_slicer = MagicMock()
    gp = str(tmp_path / "o.gcode"); Path(gp).write_text("G28")
    mock_slicer.slice.return_value = gp
    qe = QueueEngine(db, mgr, mock_slicer)
    job_id = await _seed_job(db, printer_id=1)
    await _set_filament(db, job_id, 1, "PLA", "#FFFFFF", [{"type": "PETG", "color": "#000000"}])

    await qe._process_queue()
    await asyncio.sleep(0.05)
    async with db() as session:
        assert (await session.get(Job, job_id)).status == "blocked"

    # load the matching filament, then re-check
    async with db() as session:
        (await session.get(Printer, 1)).loaded_filaments = [{"type": "PLA", "color": "#FFFFFF"}]
        await session.commit()
    await qe._process_queue()
    await asyncio.sleep(0.1)
    async with db() as session:
        assert (await session.get(Job, job_id)).status == "printing"


@pytest.mark.asyncio
async def test_check_interval_reads_config(db):
    from app.models import QueueConfig
    qe = QueueEngine(db, _make_mock_printer_manager([]), MagicMock())
    assert await qe._check_interval_seconds() == 5 * 60  # default
    async with db() as session:
        session.add(QueueConfig(id=1, check_interval_minutes=15))
        await session.commit()
    assert await qe._check_interval_seconds() == 15 * 60
