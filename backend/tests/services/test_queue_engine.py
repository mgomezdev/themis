# backend/tests/services/test_queue_engine.py
import asyncio
import os
import pytest
from tests.fake_providers import FakeSlicingProvider
import pytest_asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select, text

from app.models import Job, JobPrinterConfig, Printer, UploadedFile, GcodeFile
from app.services.queue_engine import QueueEngine
from app.services.printer_manager import PrinterManager
from app.services.slicer_service import SliceError, SlicerService
from tests.waiting import settle_background_tasks, wait_until


@pytest_asyncio.fixture
async def db(session_factory):
    """The shared per-test SQLite file (see conftest.session_factory) under the name this module uses."""
    return session_factory


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

    async def _status():
        async with db() as session:
            return (await session.get(Job, job_id)).status

    async def _is_printing():
        return await _status() == "printing"

    await wait_until(_is_printing, what="job to reach printing")  # background slice+upload task
    assert await _status() == "printing"


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
    await settle_background_tasks()

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
    await settle_background_tasks()

    async with db() as session:
        job = await session.get(Job, job_id)
        # A slicing issue blocks the job (per queue policy), with a reason.
        assert job.status == "blocked"
        assert "slicing failed" in (job.block_reason or "")


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
    await settle_background_tasks()

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
    await settle_background_tasks()

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
    await settle_background_tasks()

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
    await settle_background_tasks()

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
    await settle_background_tasks()
    async with db() as session:
        assert (await session.get(Job, job_id)).status == "blocked"

    # load the matching filament, then re-check
    async with db() as session:
        (await session.get(Printer, 1)).loaded_filaments = [{"type": "PLA", "color": "#FFFFFF"}]
        await session.commit()
    await qe._process_queue()
    await settle_background_tasks()
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
    await settle_background_tasks()

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
    await settle_background_tasks()
    async with db() as session:
        assert (await session.get(Job, job_id)).status == "sliced"

    # Printer comes online
    mgr.get_all_printer_ids.return_value = [1]
    mgr.is_printer_ready.side_effect = lambda pid: pid == 1

    # Second cycle: ready → resume upload+print
    await qe._process_queue()
    await settle_background_tasks()

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
    await settle_background_tasks()
    assert mock_slicer.slice.call_count == 1

    # Second cycle with printer still offline: must NOT slice again
    await qe._process_queue()
    await settle_background_tasks()
    assert mock_slicer.slice.call_count == 1  # no additional calls


def test_parse_gcode_estimates_single_extruder(tmp_path):
    from app.services.providers.laminus.gcode import parse_gcode_estimates as _parse_gcode_estimates
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
    from app.services.providers.laminus.gcode import parse_gcode_estimates as _parse_gcode_estimates
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
    from app.services.providers.laminus.gcode import parse_gcode_estimates as _parse_gcode_estimates
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


async def test_slice_queue_orders_by_priority_then_arrival_via_the_engines_counter(db):
    """The slice queue is a PriorityQueue of (priority, seq, coroutine): lower priority numbers first, and equal
    priorities fall back to the engine's `_slice_seq` counter, so two coroutines are never compared (TypeError)
    and arrival order is kept. (The 0/1/2 values used are the ones the engine enqueues for production /
    estimate / verify slices today; this test pins the queue mechanics, not those constants.)"""
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    engine = QueueEngine(db, _make_mock_printer_manager([]), MagicMock(spec=SlicerService))
    ran: list[str] = []

    def job(label: str):
        async def run():
            ran.append(label)
        return run()

    for priority, label in [(2, "verify"), (1, "estimate-a"), (0, "prod-a"), (1, "estimate-b"), (0, "prod-b")]:
        await engine._slice_queue.put((priority, next(engine._slice_seq), job(label)))
    for _ in range(5):
        _, _seq, coro = await engine._slice_queue.get()
        await coro
        engine._slice_queue.task_done()

    assert ran == ["prod-a", "prod-b", "estimate-a", "estimate-b", "verify"]


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
async def test_run_estimate_takes_its_numbers_from_the_slicing_providers_parser(db):
    """The estimate on the job comes from SlicingProvider.parse_estimates, not from a gcode parser baked into the
    queue: the artifact's own text says 10 g / 30 min, the fake provider says otherwise, and the fake wins."""
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
        printer = await session.get(Printer, printer_id)
        printer.current_orca_printer_profile = "Test Machine"
        printer.loaded_filaments = [{"filament_profile": "PLA Generic", "type": "PLA", "color": ""}]
        await session.commit()

    with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False, mode="w") as f:
        f.write("; filament used [g] = 10.00\n; estimated printing time (normal mode) = 30m 0s\n")
        artifact = f.name
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path(tempfile.mkdtemp())
    engine = QueueEngine(db, _make_mock_printer_manager([printer_id]), slicer)

    async def inline_put(item):
        _, _seq, coro = item
        await coro

    engine._slice_queue.put = inline_put
    fake = FakeSlicingProvider()
    fake.estimates = (77.5, 4242, [77.5])
    with patch.object(slicer, "slice", return_value=artifact), \
         patch("app.services.queue_engine.get_format_provider", return_value=fake):
        await engine.run_estimate(job_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert (job.estimate_status, job.estimate_filament_grams, job.estimate_seconds) == ("done", 77.5, 4242)
    assert ("parse_estimates", artifact, None) in fake.calls
    os.unlink(artifact)


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
        # FKs are ON (as in prod), which forbids this state; turn them off on this connection to
        # exercise the defensive branch for a dangling config.
        await session.execute(text("PRAGMA foreign_keys=OFF"))
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
    broken = FakeSlicingProvider()
    broken.parse_estimates = MagicMock(side_effect=RuntimeError("boom"))
    with patch.object(slicer, "slice", return_value=fake_gcode), \
         patch("app.services.queue_engine.get_format_provider", return_value=broken):
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
async def test_handle_print_complete_writes_the_snapshot_minus_grams(db):
    """The absolute target is `start-of-print weight - spent`, taken from the job's snapshot; the row remembers that start
    weight (the conflict guard compares the provider against it) and the unchanged spool receives the write."""
    from app.services.inventory import tasks as inventory_tasks
    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import spool, use_provider

    engine, printer_id, job_id = await _seed_completing_job(db)
    fake = FakeInventoryProvider(spools=[spool("42", 300.0)])
    await use_provider(fake)
    await _snapshot(db, job_id, 300.0)

    await engine.handle_print_complete(printer_id)
    await inventory_tasks.drain()

    assert fake.writes == [("42", pytest.approx(282.5))]
    rows = await _outbox_rows(db)
    assert [(r.spool_ref, r.status, r.pre_weight_g) for r in rows] == [("42", "applied", 300.0)]


@pytest.mark.asyncio
async def test_handle_print_complete_holds_the_write_when_the_spool_was_changed_during_the_print(db):
    """The live reading (500) is not the start-of-print weight (300): someone changed the spool mid-print, so the deduction is
    held for the user instead of overwriting that change (BIZ-198)."""
    from app.services.inventory import tasks as inventory_tasks
    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import spool, use_provider

    engine, printer_id, job_id = await _seed_completing_job(db)
    fake = FakeInventoryProvider(spools=[spool("42", 500.0)])
    await use_provider(fake)
    await _snapshot(db, job_id, 300.0)

    await engine.handle_print_complete(printer_id)
    await inventory_tasks.drain()

    assert fake.writes == []
    rows = await _outbox_rows(db)
    assert [(r.spool_ref, r.status, r.conflict_current_g) for r in rows] == [("42", "conflict", 500.0)]


async def _seed_completing_job(db, grams=17.5):
    from unittest.mock import MagicMock
    from app.models import Job, GcodeFile
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id, status="printing")
    async with db() as session:
        printer = await session.get(Printer, printer_id)
        printer.loaded_filaments = [{"type": "PLA", "color": "", "filament_profile": "PLA",
                                      "spoolman_spool_id": 42}]
        job = await session.get(Job, job_id)
        job.assigned_printer_id = printer_id
        job.actual_filament_grams = grams
        session.add(GcodeFile(job_id=job_id, printer_id=printer_id, path="/fake.gcode"))
        await session.commit()
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path("/tmp")
    return QueueEngine(db, _make_mock_printer_manager([printer_id]), slicer), printer_id, job_id


async def _outbox_rows(db):
    from sqlalchemy import select
    from app.models import InventoryPendingWrite
    async with db() as session:
        return list((await session.execute(select(InventoryPendingWrite).order_by(InventoryPendingWrite.id))).scalars())


async def _snapshot(db, job_id, pre, ref="42", provider="spoolman"):
    from app.models import JobSpoolSnapshot
    async with db() as session:
        session.add(JobSpoolSnapshot(job_id=job_id, printer_id=1, provider=provider, spool_ref=ref, pre_weight_g=pre,
                                     source="live" if pre is not None else "missing", taken_at="2026-01-01T00:00:00+00:00"))
        await session.commit()


@pytest.mark.asyncio
async def test_print_start_snapshots_off_the_loop_and_completion_never_calls_the_provider_on_the_loop(db):
    """Provider I/O is only ever scheduled as a host task: with `tasks.spawn` intercepted (coroutines collected, never run),
    starting and completing a print touch no provider method, yet the outbox row is already durable."""
    from app.models import JobSpoolSnapshot
    from sqlalchemy import select
    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import spool, use_provider

    engine, printer_id, job_id = await _seed_completing_job(db)
    fake = FakeInventoryProvider(spools=[spool("42", 500.0)])
    await use_provider(fake)
    spawned = []

    def spy(coro, name=None):
        spawned.append((name, coro))

    gcode = "/tmp/guard.gcode"
    open(gcode, "w").write("G28\n")
    with patch("app.services.inventory.tasks.spawn", spy):
        await engine._do_upload_and_print(job_id, printer_id, gcode, 1, None)
        assert [n for n, _ in spawned] == [f"inventory-snapshot-{job_id}"]
        assert fake.calls == []                                           # nothing ran on the loop
        # run the scheduled snapshot "in the host", as the real task would
        await spawned[0][1]
        async with db() as session:
            snap = (await session.execute(select(JobSpoolSnapshot))).scalar_one()
        assert (snap.pre_weight_g, snap.source, snap.spool_ref) == (500.0, "live", "42")

        fake.calls.clear()
        spawned.clear()
        await engine.handle_print_complete(printer_id)
        assert fake.calls == [] and fake.writes == []                     # completion called no provider method
        rows = await _outbox_rows(db)
        assert [(r.spool_ref, r.target_g, r.status) for r in rows] == [("42", pytest.approx(482.5), "pending")]
        assert [n for n, _ in spawned] == ["inventory-outbox-flush"]
        for _, c in spawned:
            c.close()                                                     # never awaited: avoid the un-awaited warning
    os.unlink(gcode)


@pytest.mark.asyncio
async def test_print_start_schedules_no_snapshot_when_deduction_cannot_apply(db):
    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import spool, use_provider
    engine, printer_id, job_id = await _seed_completing_job(db)
    await use_provider(FakeInventoryProvider(spools=[spool("42", 500.0)], capabilities=frozenset({"TRACKS_WEIGHT"})))
    spawned = []
    gcode = "/tmp/guard2.gcode"
    open(gcode, "w").write("G28\n")
    with patch("app.services.inventory.tasks.spawn", lambda coro, name=None: spawned.append(coro)):
        await engine._do_upload_and_print(job_id, printer_id, gcode, 1, None)
    assert spawned == []
    os.unlink(gcode)


@pytest.mark.asyncio
async def test_completion_sets_the_spool_weight_through_the_inventory_provider(db):
    """No start snapshot (the job was already printing at upgrade): the weight is taken at completion, then written."""
    from app.models import Job
    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import spool, use_provider
    from app.services.inventory import tasks as inventory_tasks

    engine, printer_id, job_id = await _seed_completing_job(db)
    fake = FakeInventoryProvider(spools=[spool("42", 500.0)])
    await use_provider(fake)

    await engine.handle_print_complete(printer_id)
    await inventory_tasks.drain()

    assert fake.writes == [("42", pytest.approx(482.5))]
    async with db() as session:
        assert (await session.get(Job, job_id)).status == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize("caps", [None, frozenset({"TRACKS_WEIGHT"}), frozenset({"WRITE_WEIGHT"})])   # None: no provider at all
async def test_completion_skips_deduction_when_the_provider_lacks_weight_capabilities_or_is_absent(db, caps):
    from unittest.mock import AsyncMock, patch
    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import spool, use_provider

    engine, printer_id, _ = await _seed_completing_job(db)
    fake = None
    if caps is not None:
        fake = FakeInventoryProvider(spools=[spool("42", 500.0)], capabilities=caps)
        await use_provider(fake)
    from app.services.inventory import tasks as inventory_tasks
    await engine.handle_print_complete(printer_id)
    await inventory_tasks.drain()
    assert await _outbox_rows(db) == []
    if fake is not None:
        assert fake.calls == [] and fake.writes == []


@pytest.mark.asyncio
async def test_completion_skips_deduction_when_deduct_on_complete_is_off(db):
    from unittest.mock import AsyncMock, patch
    from app.models import InventoryConfig
    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import spool, use_provider

    engine, printer_id, _ = await _seed_completing_job(db)
    fake = FakeInventoryProvider(spools=[spool("42", 500.0)])
    await use_provider(fake)
    async with db() as session:
        session.add(InventoryConfig(id=1, deduct_on_complete=False, low_stock_overrides={}, low_stock_alerted=[]))
        await session.commit()

    from app.services.inventory import tasks as inventory_tasks
    await engine.handle_print_complete(printer_id)
    await inventory_tasks.drain()

    assert await _outbox_rows(db) == []
    assert fake.calls == [] and fake.writes == []


@pytest.mark.asyncio
async def test_a_slot_bound_to_a_spoolman_spool_is_ignored_while_another_provider_is_active(db):
    """The slot's `spoolman_spool_id` is a Spoolman id: applying it to a different provider would hit an unrelated spool."""
    from unittest.mock import AsyncMock, patch
    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import spool, use_provider

    engine, printer_id, _ = await _seed_completing_job(db)
    other = FakeInventoryProvider(spools=[spool("42", 500.0)])
    await use_provider(other, plugin_id="other_inventory")
    from app.services.inventory import tasks as inventory_tasks
    await engine.handle_print_complete(printer_id)
    await inventory_tasks.drain()
    assert await _outbox_rows(db) == []
    assert other.calls == [] and other.writes == []


@pytest.mark.asyncio
async def test_a_failing_provider_never_breaks_completion(db):
    from app.models import Job
    from app.plugins.capabilities.filament_inventory import InventoryProviderError
    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import spool, use_provider
    from app.services.inventory import tasks as inventory_tasks

    engine, printer_id, job_id = await _seed_completing_job(db)
    fake = FakeInventoryProvider(spools=[spool("42", 500.0)])
    fake.fail_with = InventoryProviderError("down", code="503", status=503)
    await use_provider(fake)

    await engine.handle_print_complete(printer_id)
    await inventory_tasks.drain()
    assert "get_spool" in fake.calls

    async with db() as session:
        assert (await session.get(Job, job_id)).status == "complete"


@pytest.mark.asyncio
async def test_handle_print_complete_skips_deduction_when_grams_none(db):
    """Deduction is skipped when actual_filament_grams is None."""
    from unittest.mock import patch, MagicMock
    from app.models import Job, GcodeFile
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
        await session.commit()

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path("/tmp")
    engine = QueueEngine(db, mgr, slicer)

    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import use_provider
    fake = FakeInventoryProvider()
    await use_provider(fake)

    await engine.handle_print_complete(printer_id)
    from app.services.inventory import tasks as inventory_tasks
    await inventory_tasks.drain()

    assert await _outbox_rows(db) == [] and fake.calls == []


@pytest.mark.asyncio
async def test_handle_print_complete_concurrent_callers_dont_double_deduct(db):
    """The vendor client's completion callback and _reconcile_printing_jobs can both
    observe status=='printing' for the same job before either commits. Only the
    caller that wins the conditional-UPDATE claim should deduct the Spoolman spool
    and bump lifetime counters; the loser must be a no-op."""
    from unittest.mock import patch, MagicMock
    from app.models import Job, GcodeFile
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
        await session.commit()

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path("/tmp")
    engine = QueueEngine(db, mgr, slicer)

    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import spool, use_provider
    await use_provider(FakeInventoryProvider(spools=[spool("42", 500.0)]))
    await _snapshot(db, job_id, 500.0)

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

    await engine.handle_print_complete(printer_id)

    assert len(await _outbox_rows(db)) == 1
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

    def cancel_during_probe():
        # Runs in the to_thread executor, simulating a user cancel that commits
        # while the health probe is in flight.
        async def _cancel():
            async with db() as session:
                job = await session.get(Job, job_id)
                job.status = "cancelled"
                await session.commit()
        fut = asyncio.run_coroutine_threadsafe(_cancel(), loop)
        fut.result(timeout=5)
        return {"status": "ok"}

    probe = FakeSlicingProvider()
    probe.health = lambda timeout=None: cancel_during_probe()
    with patch("app.services.queue_engine.get_slicing_provider", return_value=probe):
        await qe._process_queue()
        await settle_background_tasks()

    async with db() as session:
        job = await session.get(Job, job_id)
        # Must stay cancelled, not resurrected to "slicing"/"printing".
        assert job.status == "cancelled"


@pytest.mark.asyncio
async def test_block_job_conditional_update_guards_against_cancel_during_health_probe(db):
    """Same race, but for the unreachable-Laminus block path: a user cancel that
    commits while the health probe is in flight must not be silently
    overwritten with status='blocked'."""
    from unittest.mock import patch, MagicMock

    mgr = _make_mock_printer_manager([1])
    qe = QueueEngine(db, mgr, MagicMock())
    _install_fake_put(qe)
    job_id = await _seed_job(db, printer_id=1)

    loop = asyncio.get_running_loop()

    def cancel_then_fail_probe():
        # Runs in the to_thread executor, simulating a user cancel that commits
        # while the health probe is in flight.
        async def _cancel():
            async with db() as session:
                job = await session.get(Job, job_id)
                job.status = "cancelled"
                await session.commit()
        fut = asyncio.run_coroutine_threadsafe(_cancel(), loop)
        fut.result(timeout=5)
        raise ConnectionError("simulated Laminus unreachable")

    probe = FakeSlicingProvider()
    probe.health = lambda timeout=None: cancel_then_fail_probe()
    with patch("app.services.queue_engine.get_slicing_provider", return_value=probe):
        await qe._process_queue()
        await settle_background_tasks()

    async with db() as session:
        job = await session.get(Job, job_id)
        # Must stay cancelled, not resurrected to "blocked".
        assert job.status == "cancelled"
        assert job.block_reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario, reason", [
    ("unconfigured", "Laminus sidecar not configured — slicing paused"),
    ("not_ready", "Laminus is not ready — slicing paused"),
    ("unreachable", "Laminus is unreachable — slicing paused"),
])
async def test_an_unusable_slicing_provider_blocks_the_job_with_a_specific_reason(db, scenario, reason):
    """No provider / provider says not ready / provider unreachable → the job is blocked (not failed) with its own
    reason, and the health probe keeps its 2 s timeout."""
    from unittest.mock import patch, MagicMock
    from app.services.providers.slicing import SlicingProviderError, SlicingProviderNotReady

    qe = QueueEngine(db, _make_mock_printer_manager([1]), MagicMock())
    _install_fake_put(qe)
    job_id = await _seed_job(db, printer_id=1)

    provider = None
    if scenario != "unconfigured":
        provider = FakeSlicingProvider()
        provider.fail_on["health"] = (SlicingProviderNotReady("health check returned 503") if scenario == "not_ready"
                                      else SlicingProviderError("health check request failed: refused"))
    with patch("app.services.queue_engine.get_slicing_provider", return_value=provider):
        await qe._process_queue()
        await settle_background_tasks()

    async with db() as session:
        job = await session.get(Job, job_id)
        assert (job.status, job.block_reason) == ("blocked", reason)
    if provider is not None:
        assert provider.calls == [("health", 2)]


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


# --- Notification dispatch wiring -------------------------------------------------

@pytest.mark.asyncio
async def test_fire_notifications_dispatches_when_channel_enabled_and_event_matches(db):
    from unittest.mock import patch, AsyncMock
    from app.models import NotificationConfig

    job_id = await _seed_job(db, printer_id=1)

    async with db() as session:
        session.add(NotificationConfig(
            id=1, ntfy_enabled=True, ntfy_server_url="https://ntfy.sh",
            ntfy_topic="themis", ntfy_events=["job.complete"],
        ))
        await session.commit()

    qe = QueueEngine(db, _make_mock_printer_manager([]), MagicMock())

    with patch("app.services.queue_engine.notification_service.dispatch", new_callable=AsyncMock) as mock_dispatch:
        await qe._fire_notifications(job_id, "job.complete", printer_id=1)
        # dispatch runs as a background task (fire-and-forget); yield once so it runs.
        await asyncio.sleep(0)

    mock_dispatch.assert_awaited_once()
    args = mock_dispatch.call_args[0]
    cfg_arg, event_arg, job_id_arg, title_arg, message_arg = args
    assert event_arg == "job.complete"
    assert job_id_arg == job_id
    assert "test.3mf" in message_arg


@pytest.mark.asyncio
async def test_fire_notifications_does_not_block_on_slow_dispatch(db):
    """_fire_notifications must return without waiting for channel delivery to
    finish — it's awaited from _reconcile_printing_jobs, which runs before new
    jobs are claimed each _process_queue iteration, so a slow ntfy/Discord/SMTP
    call must not stall claiming work onto idle printers."""
    from unittest.mock import patch
    from app.models import NotificationConfig

    job_id = await _seed_job(db, printer_id=1)

    async with db() as session:
        session.add(NotificationConfig(
            id=1, ntfy_enabled=True, ntfy_server_url="https://ntfy.sh",
            ntfy_topic="themis", ntfy_events=["job.complete"],
        ))
        await session.commit()

    qe = QueueEngine(db, _make_mock_printer_manager([]), MagicMock())

    dispatch_started = asyncio.Event()
    dispatch_may_finish = asyncio.Event()

    async def slow_dispatch(*args, **kwargs):
        dispatch_started.set()
        await dispatch_may_finish.wait()

    with patch("app.services.queue_engine.notification_service.dispatch", side_effect=slow_dispatch):
        # If _fire_notifications blocked on dispatch, this would hang until the
        # timeout since dispatch_may_finish is never set beforehand.
        await asyncio.wait_for(qe._fire_notifications(job_id, "job.complete", printer_id=1), timeout=1.0)
        await asyncio.wait_for(dispatch_started.wait(), timeout=1.0)

    dispatch_may_finish.set()  # let the background task finish cleanly


@pytest.mark.asyncio
async def test_fire_notifications_noop_when_all_channels_disabled(db):
    from unittest.mock import patch, AsyncMock
    from app.models import NotificationConfig

    job_id = await _seed_job(db, printer_id=1)

    async with db() as session:
        session.add(NotificationConfig(id=1))  # all channels default disabled
        await session.commit()

    qe = QueueEngine(db, _make_mock_printer_manager([]), MagicMock())

    with patch("app.services.queue_engine.notification_service.dispatch", new_callable=AsyncMock) as mock_dispatch:
        await qe._fire_notifications(job_id, "job.complete", printer_id=1)

    mock_dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_fire_notifications_noop_when_no_config_row(db):
    from unittest.mock import patch, AsyncMock

    job_id = await _seed_job(db, printer_id=1)
    qe = QueueEngine(db, _make_mock_printer_manager([]), MagicMock())

    with patch("app.services.queue_engine.notification_service.dispatch", new_callable=AsyncMock) as mock_dispatch:
        await qe._fire_notifications(job_id, "job.complete", printer_id=1)

    mock_dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_fail_job_post_slice_fires_notifications(db):
    """Integration-style: the real _fail_job_post_slice call site actually
    triggers _fire_notifications when the job reaches 'failed'."""
    from unittest.mock import patch, AsyncMock
    from app.models import NotificationConfig

    job_id = await _seed_job(db, printer_id=1)

    async with db() as session:
        session.add(NotificationConfig(
            id=1, email_enabled=True, email_host="smtp.example.com", email_port=587,
            email_from_addr="themis@example.com", email_to_addrs=["me@example.com"],
            email_events=["job.failed"],
        ))
        await session.commit()

    qe = QueueEngine(db, _make_mock_printer_manager([]), MagicMock())

    with patch("app.services.queue_engine.notification_service.dispatch", new_callable=AsyncMock) as mock_dispatch:
        await qe._fail_job_post_slice(job_id, 1, "printer disconnected")
        # dispatch runs as a background task (fire-and-forget); yield once so it runs.
        await asyncio.sleep(0)

    mock_dispatch.assert_awaited_once()
    args = mock_dispatch.call_args[0]
    assert args[1] == "job.failed"
    assert args[2] == job_id
    assert "printer disconnected" in args[4]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["draft", "planning"])
async def test_job_of_unqueued_project_is_not_claimed(db, stage):
    from app.models import Project
    mgr = _make_mock_printer_manager([1])
    qe = QueueEngine(db, mgr, MagicMock())
    _install_fake_put(qe)
    job_id = await _seed_job(db, printer_id=1)
    async with db() as session:
        now = datetime.now(timezone.utc).isoformat()
        p = Project(name="P", stage=stage, created_at=now, updated_at=now)
        session.add(p)
        await session.flush()
        (await session.get(Job, job_id)).project_id = p.id
        await session.commit()

    await qe._process_queue()

    async with db() as session:
        assert (await session.get(Job, job_id)).status == "queued"


@pytest.mark.asyncio
async def test_planning_project_job_is_claimed_once_project_is_queued(db, tmp_path):
    """Jobs generated in planning sit unclaimed; promoting the project to queued releases them."""
    from app.models import Project
    mgr = _make_mock_printer_manager([1])
    mock_slicer = MagicMock()
    gp = str(tmp_path / "o.gcode"); Path(gp).write_text("G28")
    mock_slicer.slice.return_value = gp
    qe = QueueEngine(db, mgr, mock_slicer)
    _install_fake_put(qe)
    job_id = await _seed_job(db, printer_id=1)
    async with db() as session:
        now = datetime.now(timezone.utc).isoformat()
        p = Project(name="P", stage="planning", created_at=now, updated_at=now)
        session.add(p)
        await session.flush()
        project_id = p.id
        (await session.get(Job, job_id)).project_id = project_id
        await session.commit()

    await qe._process_queue()
    await settle_background_tasks()
    async with db() as session:
        assert (await session.get(Job, job_id)).status == "queued"
    assert mock_slicer.slice.call_count == 0

    async with db() as session:
        (await session.get(Project, project_id)).stage = "queued"
        await session.commit()

    await qe._process_queue()
    await settle_background_tasks()
    async with db() as session:
        assert (await session.get(Job, job_id)).status == "printing"
    assert mock_slicer.slice.call_count == 1


def test_parse_gcode_estimates_reads_the_summary_at_the_end_of_a_long_file(tmp_path):
    """OrcaSlicer writes "filament used" / "estimated printing time" after the toolpaths — far past the header."""
    from app.services.providers.laminus.gcode import parse_gcode_estimates as _parse_gcode_estimates
    gcode = tmp_path / "long.gcode"
    gcode.write_text("; HEADER_BLOCK_START\n" + "G1 X1 Y1\n" * 20000 +
                     "; filament used [g] = 1.25, 2.75\n; estimated printing time (normal mode) = 1d 2h 3m 4s\n")
    assert _parse_gcode_estimates(str(gcode)) == (4.0, 93784, [1.25, 2.75])
