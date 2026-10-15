"""BIZ-294: the queue slices with the machine profile its printer's MODEL maps to (per nozzle variant), and blocks a job when the model has
no profile for the printer's variant, instead of slicing with a stale per-printer preset."""
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.models import Job, JobPrinterConfig, Printer, PrinterModelRecord, PrinterModelSlicerProfile, UploadedFile
from app.services.queue_engine import QueueEngine
from app.services.slicer_service import SlicerService
from tests.services.test_queue_engine import _install_fake_put, _make_mock_printer_manager
from tests.waiting import settle_background_tasks

@pytest_asyncio.fixture
async def db(session_factory):
    return session_factory


MODEL = "model-p1s"
P04, P06 = "Machine 0.4", "Machine 0.6"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _seed(factory, *, variant=None, override=None, mappings=((0.4, P04, True), (0.6, P06, False))) -> tuple[int, int]:
    async with factory() as s:
        s.add(PrinterModelRecord(id=MODEL, plugin_id="bambu", manufacturer_id="bambu", model_id="p1s", display_name="P1S"))
        await s.flush()
        for d, name, default in mappings:
            s.add(PrinterModelSlicerProfile(model_uuid=MODEL, provider_plugin_id="laminus", nozzle_diameter_mm=d, flow="standard",
                                            machine_profile=name, is_default=default))
        s.add(Printer(id=1, name="P1", printer_type="bambu", connection_config={}, model_uuid=MODEL, nozzle_variant=variant,
                      machine_profile_override=override, loaded_filaments=[{"filament_profile": "PLA Generic", "type": "PLA", "color": ""}]))
        f = UploadedFile(original_filename="t.3mf", relative_path="t.3mf", plates=[], uploaded_at=_now())
        s.add(f)
        await s.flush()
        j = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status="queued", created_at=_now(), updated_at=_now())
        s.add(j)
        await s.flush()
        s.add(JobPrinterConfig(job_id=j.id, printer_id=1, print_profile="0.20mm", filament_profile="PLA Generic"))
        await s.commit()
        return j.id, 1


async def _slice_request_for(db, job_id, printer_id) -> object:
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path(tempfile.mkdtemp())
    gcode = Path(tempfile.mkdtemp()) / "o.gcode"
    gcode.write_text("; filament used [g] = 1.0\n")
    engine = QueueEngine(db, _make_mock_printer_manager([printer_id]), slicer)
    _install_fake_put(engine)               # run the queued slice inline (no worker task in a unit test)
    with patch.object(slicer, "slice", return_value=str(gcode)) as sl, \
            patch.object(engine, "_do_upload_and_print", new_callable=AsyncMock):
        await engine._run_slice_and_print(job_id, printer_id, 1)
    return sl.call_args.args[0]


@pytest.mark.asyncio
async def test_a_printer_with_no_variant_slices_with_the_models_default_profile(db):
    job_id, pid = await _seed(db)
    assert (await _slice_request_for(db, job_id, pid)).machine_preset == P04


@pytest.mark.asyncio
async def test_a_printers_nozzle_variant_picks_its_own_profile_without_any_per_printer_profile(db):
    job_id, pid = await _seed(db, variant={"diameter_mm": 0.6, "flow": "standard"})
    assert (await _slice_request_for(db, job_id, pid)).machine_preset == P06


@pytest.mark.asyncio
async def test_a_raw_override_beats_the_model_mapping(db):
    job_id, pid = await _seed(db, override="Custom preset")
    assert (await _slice_request_for(db, job_id, pid)).machine_preset == "Custom preset"


@pytest.mark.asyncio
async def test_a_job_is_blocked_with_the_reason_when_the_models_variant_has_no_profile(db):
    job_id, pid = await _seed(db, variant={"diameter_mm": 0.8, "flow": "standard"})
    qe = QueueEngine(db, _make_mock_printer_manager([pid]), MagicMock())

    await qe._process_queue()
    await settle_background_tasks()

    async with db() as s:
        job = await s.get(Job, job_id)
    assert job.status == "blocked"
    assert job.block_reason == "no machine profile for P1S 0.8mm — slicing paused"


@pytest.mark.asyncio
async def test_a_job_is_blocked_when_the_model_has_no_mapping_at_all_and_unblocks_once_it_is_mapped(db):
    job_id, pid = await _seed(db, mappings=())
    qe = QueueEngine(db, _make_mock_printer_manager([pid]), MagicMock())
    await qe._process_queue()
    await settle_background_tasks()
    async with db() as s:
        job = await s.get(Job, job_id)
        assert (job.status, job.block_reason) == ("blocked", "no machine profile for P1S default nozzle — slicing paused")
        s.add(PrinterModelSlicerProfile(model_uuid=MODEL, provider_plugin_id="laminus", nozzle_diameter_mm=0.4, flow="standard",
                                        machine_profile=P04, is_default=True))
        await s.commit()

    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path(tempfile.mkdtemp())
    gcode = Path(tempfile.mkdtemp()) / "o.gcode"
    gcode.write_text("; filament used [g] = 1.0\n")
    qe = QueueEngine(db, _make_mock_printer_manager([pid]), slicer)
    _install_fake_put(qe)
    with patch.object(slicer, "slice", return_value=str(gcode)) as sl, patch.object(qe, "_do_upload_and_print", new_callable=AsyncMock):
        await qe._process_queue()
        await settle_background_tasks()

    assert sl.call_args.args[0].machine_preset == P04
