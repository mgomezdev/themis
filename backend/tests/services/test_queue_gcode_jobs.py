"""Queue engine with a pre-sliced .gcode job (BIZ-188): never sliced, printed from a private copy."""
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.models import GcodeFile, Job, JobPrinterConfig, Printer, UploadedFile
from app.services.queue_engine import QueueEngine
from tests.services.test_queue_engine import _install_fake_put, _make_mock_printer_manager
from tests.waiting import settle_background_tasks, wait_until

GCODE = b"; filament used [g] = 3.5\n; estimated printing time (normal mode) = 10m 5s\nG28\n"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _seed(factory, library, name="part.gcode") -> tuple[int, int]:
    """A queued job on a gcode file in the library -> (job_id, file_id)."""
    library.mkdir(parents=True, exist_ok=True)
    (library / name).write_bytes(GCODE)
    async with factory() as s:
        s.add(Printer(id=1, name="P1", printer_type="elegoo_centauri", connection_config={}))   # no OrcaSlicer machine preset
        f = UploadedFile(original_filename=name, relative_path=name, folder="/", plates=[], uploaded_at=_now())
        s.add(f)
        await s.flush()
        j = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status="queued",
                created_at=_now(), updated_at=_now())
        s.add(j)
        await s.flush()
        s.add(JobPrinterConfig(job_id=j.id, printer_id=1, print_profile="", filament_type="any", filament_color="any"))
        await s.commit()
        return j.id, f.id


@pytest.mark.asyncio
async def test_gcode_job_prints_without_slicing_and_keeps_the_library_file(session_factory, tmp_path, monkeypatch):
    library = tmp_path / "library"
    monkeypatch.setenv("THEMIS_LIBRARY_DIR", str(library))
    mgr = _make_mock_printer_manager([1])
    slicer = MagicMock()
    slicer._data_dir = tmp_path / "data"
    qe = QueueEngine(session_factory, mgr, slicer)
    _install_fake_put(qe)
    job_id, _ = await _seed(session_factory, library)

    await qe._process_queue()

    async def _printing():
        async with session_factory() as s:
            return (await s.get(Job, job_id)).status == "printing"
    await wait_until(_printing, what="gcode job to start printing")
    slicer.slice.assert_not_called()
    started = mgr.get_client.return_value.start_print.call_args.args[0]
    assert started.endswith(".gcode")
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        gcode = (await s.execute(select(GcodeFile).where(GcodeFile.job_id == job_id))).scalar_one()
        assert (job.actual_filament_grams, job.actual_seconds) == (3.5, 605)
        assert gcode.path != str(library / "part.gcode") and (tmp_path / "data" / "gcode" / str(job_id)) in Path(gcode.path).parents
        staged = gcode.path
    assert (library / "part.gcode").read_bytes() == GCODE

    # Finishing the print deletes the staged copy, never the library's file.
    async with session_factory() as s:
        (await s.get(Job, job_id)).assigned_printer_id = 1
        await s.commit()
    await qe.handle_print_complete(1)
    await settle_background_tasks()
    assert not os.path.exists(staged)
    assert (library / "part.gcode").read_bytes() == GCODE


@pytest.mark.asyncio
async def test_gcode_job_missing_from_library_blocks_instead_of_failing(session_factory, tmp_path, monkeypatch):
    library = tmp_path / "library"
    monkeypatch.setenv("THEMIS_LIBRARY_DIR", str(library))
    mgr = _make_mock_printer_manager([1])
    slicer = MagicMock()
    slicer._data_dir = tmp_path / "data"
    qe = QueueEngine(session_factory, mgr, slicer)
    _install_fake_put(qe)
    job_id, _ = await _seed(session_factory, library)
    (library / "part.gcode").unlink()

    await qe._process_queue()
    await settle_background_tasks()

    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert job.status == "blocked"
        assert "gcode" in (job.block_reason or "")
