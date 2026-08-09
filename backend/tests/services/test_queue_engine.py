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
from app.services.slicer_service import SliceError, SlicerService


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


def _install_fake_put(engine: QueueEngine) -> None:
    """Bypass the slice worker by running queued coroutines inline.

    Unit tests that call _process_queue() directly never call start(), so the
    _slice_worker_task is not running. Replacing _slice_queue.put with this
    inline executor avoids PytestUnraisableExceptionWarning from orphaned tasks.
    """
    async def fake_put(item):
        _, _seq, coro = item
        await coro

    engine._slice_queue.put = fake_put  # type: ignore[method-assign]


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
    _install_fake_put(qe)
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
    _install_fake_put(qe)
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
    _install_fake_put(qe)

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
    _install_fake_put(qe)
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
    _install_fake_put(qe)
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
    _install_fake_put(qe)
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
    _install_fake_put(qe)
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


def _make_mock_printer_manager_with_offline(ready_ids: list[int], offline_ids: list[int]) -> PrinterManager:
    """Mock where some printers are tracked but not ready (offline)."""
    all_ids = ready_ids + offline_ids
    mgr = MagicMock(spec=PrinterManager)
    mgr.get_all_printer_ids.return_value = all_ids
    mgr.is_printer_ready.side_effect = lambda pid: pid in ready_ids
    mock_client = MagicMock()
    mock_client.file_upload_supported = False
    mock_client.start_print.return_value = True
    mock_client.orca_export_args.return_value = []
    mgr.get_client.return_value = mock_client
    return mgr


@pytest.mark.asyncio
async def test_offline_printer_slices_job_to_sliced_status(db, tmp_path):
    """An offline (tracked but not ready) printer should still slice jobs; the job
    parks at 'sliced' instead of going to 'printing'."""
    mgr = _make_mock_printer_manager_with_offline(ready_ids=[], offline_ids=[1])
    mock_slicer = MagicMock()
    gcode_path = str(tmp_path / "output.gcode")
    Path(gcode_path).write_text("G28\n")
    mock_slicer.slice.return_value = gcode_path

    qe = QueueEngine(db, mgr, mock_slicer)
    _install_fake_put(qe)
    job_id = await _seed_job(db, printer_id=1)

    await qe._process_queue()
    await asyncio.sleep(0.15)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == "sliced"


@pytest.mark.asyncio
async def test_sliced_job_resumes_when_printer_comes_online(db, tmp_path):
    """A 'sliced' job (gcode on disk) is picked up and sent to upload+print when
    the printer becomes ready on the next queue cycle."""
    mgr = _make_mock_printer_manager_with_offline(ready_ids=[], offline_ids=[1])
    mock_slicer = MagicMock()
    gcode_path = str(tmp_path / "output.gcode")
    Path(gcode_path).write_text("G28\n")
    mock_slicer.slice.return_value = gcode_path

    qe = QueueEngine(db, mgr, mock_slicer)
    _install_fake_put(qe)
    job_id = await _seed_job(db, printer_id=1)

    # First cycle: offline → job reaches "sliced"
    await qe._process_queue()
    await asyncio.sleep(0.15)
    async with db() as session:
        assert (await session.get(Job, job_id)).status == "sliced"

    # Printer comes online
    mgr.get_all_printer_ids.return_value = [1]
    mgr.is_printer_ready.side_effect = lambda pid: pid == 1

    # Second cycle: ready → resume upload+print
    await qe._process_queue()
    await asyncio.sleep(0.15)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == "printing"


@pytest.mark.asyncio
async def test_offline_does_not_reslice_when_sliced_job_pending(db, tmp_path):
    """The offline slice loop skips if a 'sliced' gcode artifact already exists
    for the same printer, preventing duplicate slice work."""
    mgr = _make_mock_printer_manager_with_offline(ready_ids=[], offline_ids=[1])
    mock_slicer = MagicMock()
    gcode_path = str(tmp_path / "output.gcode")
    Path(gcode_path).write_text("G28\n")
    mock_slicer.slice.return_value = gcode_path

    qe = QueueEngine(db, mgr, mock_slicer)
    _install_fake_put(qe)
    job_id = await _seed_job(db, printer_id=1)

    # First cycle slices the job
    await qe._process_queue()
    await asyncio.sleep(0.15)
    assert mock_slicer.slice.call_count == 1

    # Second cycle with printer still offline: must NOT slice again
    await qe._process_queue()
    await asyncio.sleep(0.05)
    assert mock_slicer.slice.call_count == 1  # no additional calls


def test_parse_gcode_estimates_single_extruder(tmp_path):
    from app.services.queue_engine import _parse_gcode_estimates
    gcode = tmp_path / "test.gcode"
    gcode.write_text(
        "; filament used [g] = 12.50\n"
        "; estimated printing time (normal mode) = 1h 30m 45s\n"
    )
    grams, secs, extruder_grams = _parse_gcode_estimates(str(gcode))
    assert grams == pytest.approx(12.50)
    assert secs == 1 * 3600 + 30 * 60 + 45
    assert extruder_grams == [pytest.approx(12.50)]


def test_parse_gcode_estimates_multi_extruder(tmp_path):
    from app.services.queue_engine import _parse_gcode_estimates
    gcode = tmp_path / "test.gcode"
    gcode.write_text(
        "; filament used [g] = 15.23, 8.45\n"
        "; estimated printing time (normal mode) = 2h 0m 0s\n"
    )
    grams, secs, extruder_grams = _parse_gcode_estimates(str(gcode))
    assert grams == pytest.approx(23.68)
    assert secs == 7200
    assert extruder_grams == [pytest.approx(15.23), pytest.approx(8.45)]


def test_parse_gcode_estimates_missing_returns_none(tmp_path):
    from app.services.queue_engine import _parse_gcode_estimates
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; no filament info here\n")
    grams, secs, extruder_grams = _parse_gcode_estimates(str(gcode))
    assert grams is None
    assert secs is None
    assert extruder_grams is None


@pytest.mark.asyncio
async def test_priority_queue_orders_production_before_estimate(db):
    """Production slices (priority 0) are dequeued before estimate slices (priority 1)."""
    import itertools
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    mgr = _make_mock_printer_manager([])
    slicer = MagicMock(spec=SlicerService)

    engine = QueueEngine(db, mgr, slicer)
    seq = itertools.count()

    results = []

    async def coro(label):
        results.append(label)

    # Put estimate first, then production
    await engine._slice_queue.put((1, next(seq), coro("estimate")))
    await engine._slice_queue.put((0, next(seq), coro("production")))

    # Drain both
    for _ in range(2):
        _, _s, c = await engine._slice_queue.get()
        await c
        engine._slice_queue.task_done()

    assert results == ["production", "estimate"]


@pytest.mark.asyncio
async def test_equal_priority_no_type_error(db):
    """Two equal-priority items with seq tiebreaker don't raise TypeError."""
    import itertools
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    mgr = _make_mock_printer_manager([])
    slicer = MagicMock(spec=SlicerService)
    engine = QueueEngine(db, mgr, slicer)
    seq = itertools.count()

    async def noop():
        pass

    await engine._slice_queue.put((1, next(seq), noop()))
    await engine._slice_queue.put((1, next(seq), noop()))
    # Should not raise — drain without error
    for _ in range(2):
        _, _s, c = await engine._slice_queue.get()
        await c
        engine._slice_queue.task_done()


@pytest.mark.asyncio
async def test_run_verify_slice_goes_through_slice_queue_not_bare_executor(db, tmp_path):
    """verify-slice must be enqueued on the shared _slice_queue (consumed by
    asyncio.to_thread), not dispatched straight to queue_engine._executor — that
    4-thread pool is also what _do_upload_and_print depends on for
    upload_file/start_print, and a debug-only test-slice can block for minutes."""
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService, SliceRequest

    mgr = _make_mock_printer_manager([])
    gp = str(tmp_path / "out.gcode")
    Path(gp).write_text("G28")
    slicer = MagicMock(spec=SlicerService)
    slicer.slice.return_value = gp
    engine = QueueEngine(db, mgr, slicer)
    _install_fake_put(engine)

    req = SliceRequest(job_id=1, source_3mf="x.3mf", plate_number=1,
                        machine_preset="m", process_preset="p", filament_presets=["f"])
    output_dir = tmp_path / "verify"

    result = await engine.run_verify_slice(req, output_dir)

    assert result == gp
    slicer.slice.assert_called_once_with(req, output_dir)


@pytest.mark.asyncio
async def test_verify_slice_priority_lower_than_production_and_estimate(db):
    """verify-slice (priority 2) is dequeued after both production (0) and
    estimate (1) slices already sitting in the queue."""
    import itertools
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    mgr = _make_mock_printer_manager([])
    slicer = MagicMock(spec=SlicerService)
    engine = QueueEngine(db, mgr, slicer)
    seq = itertools.count()

    results = []

    async def coro(label):
        results.append(label)

    await engine._slice_queue.put((2, next(seq), coro("verify")))
    await engine._slice_queue.put((1, next(seq), coro("estimate")))
    await engine._slice_queue.put((0, next(seq), coro("production")))

    for _ in range(3):
        _, _s, c = await engine._slice_queue.get()
        await c
        engine._slice_queue.task_done()

    assert results == ["production", "estimate", "verify"]


@pytest.mark.asyncio
async def test_actual_values_captured_at_slice_time(db):
    """After a production slice, actual_filament_grams/actual_seconds/actual_filament_breakdown
    are persisted on the Job row in the same session block as GcodeFile creation."""
    import tempfile, os
    from pathlib import Path
    from unittest.mock import patch, MagicMock, AsyncMock
    from app.models import Job, QueueConfig
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    # Set up a printer with filament profile
    async with db() as session:
        printer = await session.get(Printer, printer_id)
        printer.current_orca_printer_profile = "Test Machine"
        printer.loaded_filaments = [{"filament_profile": "PLA Generic", "type": "PLA", "color": ""}]
        await session.commit()

    # Mock the slice to write a fake gcode file
    with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False, mode="w") as f:
        f.write("; filament used [g] = 15.50\n; estimated printing time (normal mode) = 1h 0m 0s\n")
        fake_gcode = f.name

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path(tempfile.mkdtemp())

    engine = QueueEngine(db, mgr, slicer)

    # Patch the priority queue to run synchronously
    async def fake_put(item):
        _, _seq, coro = item
        await coro

    engine._slice_queue.put = fake_put

    with patch.object(slicer, "slice", return_value=fake_gcode), \
         patch.object(engine, "_do_upload_and_print", new_callable=AsyncMock):
        await engine._run_slice_and_print(job_id, printer_id, 1)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.actual_filament_grams == pytest.approx(15.50)
        assert job.actual_seconds == 3600
        assert job.actual_filament_breakdown is not None
        assert len(job.actual_filament_breakdown) == 1
        assert job.actual_filament_breakdown[0]["grams"] == pytest.approx(15.50)

    os.unlink(fake_gcode)


@pytest.mark.asyncio
async def test_run_slice_and_print_removes_gcode_when_cancelled_during_slice(db):
    """A cancel that lands while the slice is in flight is caught by the
    post-slice status check, which returns before creating a GcodeFile row.
    The already-downloaded artifact must not be silently orphaned on disk —
    nothing else ever cleans it up since no GcodeFile row references it."""
    import tempfile, os
    from pathlib import Path
    from unittest.mock import patch, MagicMock, AsyncMock
    from app.models import Job
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        printer.current_orca_printer_profile = "Test Machine"
        printer.loaded_filaments = [{"filament_profile": "PLA Generic", "type": "PLA", "color": ""}]
        await session.commit()

    with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False, mode="w") as f:
        f.write("; filament used [g] = 15.50\n")
        fake_gcode = f.name

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path(tempfile.mkdtemp())
    engine = QueueEngine(db, mgr, slicer)
    _install_fake_put(engine)

    loop = asyncio.get_running_loop()

    def fake_slice(req, *a, **kw):
        # Runs off-thread via asyncio.to_thread — simulates a user cancel that
        # commits while the (already in-flight) slice is producing its artifact.
        async def _cancel():
            async with db() as s:
                j = await s.get(Job, job_id)
                j.status = "cancelled"
                await s.commit()
        fut = asyncio.run_coroutine_threadsafe(_cancel(), loop)
        fut.result(timeout=5)
        return fake_gcode

    with patch.object(slicer, "slice", side_effect=fake_slice), \
         patch.object(engine, "_do_upload_and_print", new_callable=AsyncMock):
        await engine._run_slice_and_print(job_id, printer_id, 1)

    assert not os.path.exists(fake_gcode)


@pytest.mark.asyncio
async def test_run_estimate_sets_done_with_fields(db):
    """run_estimate writes estimate fields when slice succeeds."""
    import tempfile, os
    from unittest.mock import patch, MagicMock
    from app.models import Job, QueueConfig
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        job.estimate_status = "pending"
        job.estimate_token = 1
        await session.commit()

        printer = await session.get(Printer, printer_id)
        printer.current_orca_printer_profile = "Test Machine"
        printer.loaded_filaments = [{"filament_profile": "PLA Generic", "type": "PLA", "color": ""}]
        await session.commit()

    with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False, mode="w") as f:
        f.write("; filament used [g] = 10.00\n; estimated printing time (normal mode) = 30m 0s\n")
        fake_gcode = f.name

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path(tempfile.mkdtemp())

    engine = QueueEngine(db, mgr, slicer)

    async def fake_put(item):
        _, _seq, coro = item
        await coro

    engine._slice_queue.put = fake_put

    with patch.object(slicer, "slice", return_value=fake_gcode):
        await engine.run_estimate(job_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.estimate_status == "done"
        assert job.estimate_filament_grams == pytest.approx(10.0)
        assert job.estimate_seconds == 1800
        assert job.estimate_filament_breakdown is not None
        assert job.estimate_preset_label is not None

    os.unlink(fake_gcode)


@pytest.mark.asyncio
async def test_run_estimate_fails_when_no_printer_config(db):
    """A job with no JobPrinterConfig row must fail the estimate rather than
    return silently — leaving estimate_status stuck on 'pending' means the UI
    spinner never resolves."""
    from app.models import Job, JobPrinterConfig
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService
    from unittest.mock import MagicMock

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        job.estimate_status = "pending"
        job.estimate_token = 1
        cfgs = (await session.execute(
            select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id)
        )).scalars().all()
        for cfg in cfgs:
            await session.delete(cfg)
        await session.commit()

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    engine = QueueEngine(db, mgr, slicer)

    await engine.run_estimate(job_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.estimate_status == "failed"


@pytest.mark.asyncio
async def test_run_estimate_fails_when_printer_missing(db):
    """A JobPrinterConfig pointing at a printer that no longer exists must fail
    the estimate rather than return silently, leaving it stuck on 'pending'."""
    from app.models import Job
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService
    from unittest.mock import MagicMock

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        job.estimate_status = "pending"
        job.estimate_token = 1
        printer = await session.get(Printer, printer_id)
        await session.delete(printer)
        await session.commit()

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    engine = QueueEngine(db, mgr, slicer)

    await engine.run_estimate(job_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.estimate_status == "failed"


@pytest.mark.asyncio
async def test_run_estimate_conditional_update_guards_against_cancel(db):
    """If estimate_status is cleared (cancellation) before write, results are discarded."""
    import tempfile, os
    from unittest.mock import patch, MagicMock
    from app.models import Job
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        job.estimate_status = "pending"
        job.estimate_token = 1
        await session.commit()
        printer = await session.get(Printer, printer_id)
        printer.current_orca_printer_profile = "M"
        printer.loaded_filaments = [{"filament_profile": "PLA", "type": "PLA", "color": ""}]
        await session.commit()

    with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False, mode="w") as f:
        f.write("; filament used [g] = 10.00\n; estimated printing time (normal mode) = 30m\n")
        fake_gcode = f.name

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path(tempfile.mkdtemp())
    engine = QueueEngine(db, mgr, slicer)

    # Cancel the estimate mid-flight (simulates cancel_job clearing estimate_status)
    async def fake_put(item):
        _, _seq, coro = item
        # Clear the status BEFORE running the coro (simulating cancellation race)
        async with db() as session:
            j = await session.get(Job, job_id)
            j.estimate_status = None
            await session.commit()
        await coro

    engine._slice_queue.put = fake_put

    with patch.object(slicer, "slice", return_value=fake_gcode):
        await engine.run_estimate(job_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.estimate_status is None  # cleared, not overwritten
        assert job.estimate_filament_grams is None

    os.unlink(fake_gcode)


@pytest.mark.asyncio
async def test_run_estimate_failure_sets_failed(db):
    """SliceError from slicer: estimate_status becomes 'failed'; job.status unchanged."""
    from unittest.mock import patch, MagicMock
    from app.models import Job
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService, SliceError

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        job.estimate_status = "pending"
        job.estimate_token = 1
        await session.commit()
        printer = await session.get(Printer, printer_id)
        printer.current_orca_printer_profile = "M"
        printer.loaded_filaments = [{"filament_profile": "PLA", "type": "PLA", "color": ""}]
        await session.commit()

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path("/tmp/test_estimates")

    engine = QueueEngine(db, mgr, slicer)

    async def fake_put(item):
        _, _seq, coro = item
        await coro

    engine._slice_queue.put = fake_put

    with patch.object(slicer, "slice", side_effect=SliceError("profile not found")):
        await engine.run_estimate(job_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.estimate_status == "failed"
        assert job.status == "queued"  # job.status must not be touched
        assert job.block_reason is None


@pytest.mark.asyncio
async def test_run_estimate_unexpected_exception_marks_failed_not_pending(db):
    """An unexpected exception anywhere in the estimate pipeline (not just the
    slice-failure path already handled) must still fail the estimate.
    spawn_estimate's done-callback only discards the task and never retrieves
    the exception, so without run_estimate's own safety net this would leave
    estimate_status stuck on 'pending' forever."""
    import tempfile, os
    from unittest.mock import patch, MagicMock
    from app.models import Job
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        job.estimate_status = "pending"
        job.estimate_token = 1
        await session.commit()
        printer = await session.get(Printer, printer_id)
        printer.current_orca_printer_profile = "M"
        printer.loaded_filaments = [{"filament_profile": "PLA", "type": "PLA", "color": ""}]
        await session.commit()

    with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False, mode="w") as f:
        f.write("; filament used [g] = 10.00\n")
        fake_gcode = f.name

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path(tempfile.mkdtemp())
    engine = QueueEngine(db, mgr, slicer)

    async def fake_put(item):
        _, _seq, coro = item
        await coro

    engine._slice_queue.put = fake_put

    # Simulate an unexpected error in a step that has no existing try/except of
    # its own (Step 4 gcode parsing), rather than the already-handled slice
    # failure path.
    with patch.object(slicer, "slice", return_value=fake_gcode), \
         patch("app.services.queue_engine._parse_gcode_estimates", side_effect=RuntimeError("boom")):
        await engine.run_estimate(job_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.estimate_status == "failed"

    os.unlink(fake_gcode)


@pytest.mark.asyncio
async def test_run_estimate_discards_result_when_token_incremented(db):
    """If estimate_token is incremented (re-trigger) before the write, results are discarded."""
    import tempfile, os
    from unittest.mock import patch, MagicMock
    from app.models import Job
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        job.estimate_status = "pending"
        job.estimate_token = 1  # original token
        await session.commit()
        printer = await session.get(Printer, printer_id)
        printer.current_orca_printer_profile = "M"
        printer.loaded_filaments = [{"filament_profile": "PLA", "type": "PLA", "color": ""}]
        await session.commit()

    with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False, mode="w") as f:
        f.write("; filament used [g] = 10.00\n; estimated printing time (normal mode) = 30m\n")
        fake_gcode = f.name

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path(tempfile.mkdtemp())
    engine = QueueEngine(db, mgr, slicer)

    # Simulate re-trigger: increment token BEFORE the coro runs
    async def fake_put(item):
        _, _seq, coro = item
        async with db() as session:
            j = await session.get(Job, job_id)
            j.estimate_token = 2  # bumped by re-trigger
            await session.commit()
        await coro

    engine._slice_queue.put = fake_put

    with patch.object(slicer, "slice", return_value=fake_gcode):
        await engine.run_estimate(job_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        # Token mismatch → rowcount=0 → results discarded; status still "pending"
        assert job.estimate_filament_grams is None
        assert job.estimate_status == "pending"  # not overwritten to "done"

    os.unlink(fake_gcode)


@pytest.mark.asyncio
async def test_handle_print_complete_fires_spoolman_deduction(db):
    """On completion, deducts actual_filament_grams from Spoolman using slot's spool_id."""
    from unittest.mock import patch, AsyncMock, MagicMock
    from app.models import Job, GcodeFile, SpoolmanConfig
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id, status="printing")

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        printer.loaded_filaments = [{"type": "PLA", "color": "", "filament_profile": "PLA",
                                      "spoolman_spool_id": 42}]
        job = await session.get(Job, job_id)
        job.status = "printing"
        job.assigned_printer_id = printer_id
        job.actual_filament_grams = 17.5
        session.add(GcodeFile(job_id=job_id, printer_id=printer_id, path="/fake.gcode"))
        session.add(SpoolmanConfig(id=1, enabled=True, url="http://spoolman.test", api_key=None))
        await session.commit()

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path("/tmp")
    engine = QueueEngine(db, mgr, slicer)

    deduction_calls = []

    async def fake_deduct(url, api_key, spool_id, grams):
        deduction_calls.append({"spool_id": spool_id, "grams": grams})

    with patch("app.services.queue_engine._deduct_spool", fake_deduct):
        await engine.handle_print_complete(printer_id)

    assert len(deduction_calls) == 1
    assert deduction_calls[0]["spool_id"] == 42
    assert deduction_calls[0]["grams"] == pytest.approx(17.5)


@pytest.mark.asyncio
async def test_handle_print_complete_skips_deduction_when_grams_none(db):
    """Deduction is skipped when actual_filament_grams is None."""
    from unittest.mock import patch, MagicMock
    from app.models import Job, GcodeFile, SpoolmanConfig
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id, status="printing")

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        printer.loaded_filaments = [{"spoolman_spool_id": 5}]
        job = await session.get(Job, job_id)
        job.status = "printing"
        job.assigned_printer_id = printer_id
        job.actual_filament_grams = None  # not captured
        session.add(GcodeFile(job_id=job_id, printer_id=printer_id, path="/fake.gcode"))
        session.add(SpoolmanConfig(id=1, enabled=True, url="http://spoolman.test", api_key=None))
        await session.commit()

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path("/tmp")
    engine = QueueEngine(db, mgr, slicer)

    deduction_calls = []
    async def fake_deduct(*a, **kw):
        deduction_calls.append(a)

    with patch("app.services.queue_engine._deduct_spool", fake_deduct):
        await engine.handle_print_complete(printer_id)

    assert deduction_calls == []


@pytest.mark.asyncio
async def test_handle_print_complete_concurrent_callers_dont_double_deduct(db):
    """The vendor client's completion callback and _reconcile_printing_jobs can both
    observe status=='printing' for the same job before either commits. Only the
    caller that wins the conditional-UPDATE claim should deduct the Spoolman spool
    and bump lifetime counters; the loser must be a no-op."""
    from unittest.mock import patch, MagicMock
    from app.models import Job, GcodeFile, SpoolmanConfig
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id, status="printing")

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        printer.loaded_filaments = [{"type": "PLA", "color": "", "filament_profile": "PLA",
                                      "spoolman_spool_id": 42}]
        job = await session.get(Job, job_id)
        job.status = "printing"
        job.assigned_printer_id = printer_id
        job.actual_filament_grams = 17.5
        session.add(GcodeFile(job_id=job_id, printer_id=printer_id, path="/fake.gcode"))
        session.add(SpoolmanConfig(id=1, enabled=True, url="http://spoolman.test", api_key=None))
        await session.commit()

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path("/tmp")
    engine = QueueEngine(db, mgr, slicer)

    deduction_calls = []

    async def fake_deduct(url, api_key, spool_id, grams):
        deduction_calls.append({"spool_id": spool_id, "grams": grams})

    # Force the interleave that the old code let happen implicitly: right after the
    # OUTER call's initial read finds the job "printing", run a full SECOND
    # handle_print_complete call to completion (the real winner) before the outer
    # call reaches its own claim UPDATE.
    orig_factory = db
    state = {"patched": False, "second_ran": False}

    def wrapped_factory():
        session = orig_factory()
        if not state["patched"]:
            state["patched"] = True
            orig_execute = session.execute

            async def spy_execute(stmt, *a, **kw):
                result = await orig_execute(stmt, *a, **kw)
                if not state["second_ran"]:
                    state["second_ran"] = True
                    await engine.handle_print_complete(printer_id)
                return result

            session.execute = spy_execute
        return session

    engine._factory = wrapped_factory

    with patch("app.services.queue_engine._deduct_spool", fake_deduct):
        await engine.handle_print_complete(printer_id)

    assert len(deduction_calls) == 1
    async with db() as session:
        printer = await session.get(Printer, printer_id)
        assert printer.lifetime_job_count == 1


@pytest.mark.asyncio
async def test_slice_worker_continues_after_exception(db):
    """_slice_worker logs the error and calls task_done; the next coro still runs."""
    mgr = _make_mock_printer_manager([])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path("/tmp")
    engine = QueueEngine(db, mgr, slicer)

    results = []

    async def raising_coro():
        raise RuntimeError("injected error")

    async def ok_coro():
        results.append("ok")

    worker = asyncio.create_task(engine._slice_worker())
    await engine._slice_queue.put((0, 0, raising_coro()))
    await engine._slice_queue.put((0, 1, ok_coro()))
    await engine._slice_queue.join()

    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)

    assert results == ["ok"]


@pytest.mark.asyncio
async def test_startup_resets_pending_estimates(db):
    """QueueEngine.start() resets all estimate_status='pending' to NULL."""
    from unittest.mock import patch, MagicMock
    from app.models import Job, QueueConfig
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        job.estimate_status = "pending"
        await session.commit()

    mgr = _make_mock_printer_manager([])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path("/tmp")
    engine = QueueEngine(db, mgr, slicer)

    # Patch _loop and _slice_worker so create_task returns immediately without
    # spawning real background tasks (which would hang the test event loop).
    with patch.object(engine, "_loop", new_callable=AsyncMock), \
         patch.object(engine, "_slice_worker", new_callable=AsyncMock):
        await engine.start()
        # Cancel the tasks to avoid pending-task warnings
        if engine._task:
            engine._task.cancel()
        if engine._slice_worker_task:
            engine._slice_worker_task.cancel()

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.estimate_status is None


@pytest.mark.asyncio
async def test_claim_conditional_update_guards_against_cancel_during_health_probe(db):
    """A user cancel that commits while _try_claim_for_printer awaits the Laminus
    health probe must not be silently overwritten with status='slicing' — that
    would let the printer start on a job the user believes was cancelled."""
    from unittest.mock import patch, MagicMock

    mgr = _make_mock_printer_manager([1])
    qe = QueueEngine(db, mgr, MagicMock())
    _install_fake_put(qe)
    job_id = await _seed_job(db, printer_id=1)

    loop = asyncio.get_running_loop()

    def fake_httpx_get(url, timeout=None):
        # Runs in the to_thread executor, simulating a user cancel that commits
        # while the health probe is in flight.
        async def _cancel():
            async with db() as session:
                job = await session.get(Job, job_id)
                job.status = "cancelled"
                await session.commit()
        fut = asyncio.run_coroutine_threadsafe(_cancel(), loop)
        fut.result(timeout=5)
        resp = MagicMock()
        resp.is_success = True
        return resp

    with patch("app.services.queue_engine.get_laminus_sidecar_url", return_value="http://laminus.test"), \
         patch("app.services.queue_engine.httpx.get", side_effect=fake_httpx_get):
        await qe._process_queue()
        await asyncio.sleep(0.1)

    async with db() as session:
        job = await session.get(Job, job_id)
        # Must stay cancelled, not resurrected to "slicing"/"printing".
        assert job.status == "cancelled"


@pytest.mark.asyncio
async def test_resume_sliced_job_conditional_update_guards_against_cancel(db, tmp_path):
    """Same race for the pre-sliced resume path: a cancel that commits between the
    row lookup and the claim UPDATE must not be overwritten with status='uploading'."""
    from unittest.mock import MagicMock

    printer_id = 1
    job_id = await _seed_job(db, printer_id)
    gcode_path = str(tmp_path / "output.gcode")
    Path(gcode_path).write_text("G28\n")

    async with db() as session:
        job = await session.get(Job, job_id)
        job.status = "sliced"
        session.add(GcodeFile(job_id=job_id, printer_id=printer_id, path=gcode_path))
        await session.commit()

    mgr = _make_mock_printer_manager([printer_id])
    qe = QueueEngine(db, mgr, MagicMock())

    async with db() as session:
        job = await session.get(Job, job_id)

        orig_get = session.get

        async def spy_get(model, pk, *a, **kw):
            if model is Printer:
                # Simulate a concurrent cancel committing between our config/printer
                # lookups and the claim UPDATE below.
                async with db() as s2:
                    j = await s2.get(Job, job_id)
                    j.status = "cancelled"
                    await s2.commit()
            return await orig_get(model, pk, *a, **kw)

        session.get = spy_get

        claimed = await qe._try_resume_sliced_job(session, printer_id)
        assert claimed is False

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.status == "cancelled"


@pytest.mark.asyncio
async def test_handle_print_complete_accrues_lifetime_counters(db):
    printer_id = 1
    job_id = await _seed_job(db, printer_id, status="printing")

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        printer.lifetime_job_count = 4
        printer.lifetime_print_seconds = 7200
        job = await session.get(Job, job_id)
        job.status = "printing"
        job.assigned_printer_id = printer_id
        job.actual_seconds = 3600
        await session.commit()

    mgr = _make_mock_printer_manager([])
    qe = QueueEngine(db, mgr, MagicMock())
    await qe.handle_print_complete(printer_id)

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        assert printer.lifetime_job_count == 5
        assert printer.lifetime_print_seconds == 10800


@pytest.mark.asyncio
async def test_handle_print_complete_accrues_job_count_even_without_actual_seconds(db):
    printer_id = 1
    job_id = await _seed_job(db, printer_id, status="printing")

    async with db() as session:
        job = await session.get(Job, job_id)
        job.status = "printing"
        job.assigned_printer_id = printer_id
        job.actual_seconds = None
        await session.commit()

    mgr = _make_mock_printer_manager([])
    qe = QueueEngine(db, mgr, MagicMock())
    await qe.handle_print_complete(printer_id)

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        assert printer.lifetime_job_count == 1
        assert printer.lifetime_print_seconds == 0
