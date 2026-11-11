"""BIZ-358: a job config with print_profile "" ("Printer default") slices with the printer's default process profile; with
none set the claim blocks with an actionable reason (never fails). Pre-sliced files need no process profile."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.models import Job, JobPrinterConfig, Printer
from app.services.queue_engine import QueueEngine, _slice_params, effective_process_profile, missing_process_reason
from app.services.slicer_service import SlicerService
from tests.services.test_queue_engine import _install_fake_put, _make_mock_printer_manager, _seed_job
from tests.waiting import wait_until


async def _set(factory, job_id, *, print_profile, default):
    async with factory() as s:
        cfg = (await s.execute(JobPrinterConfig.__table__.select().where(JobPrinterConfig.job_id == job_id))).first()
        c = await s.get(JobPrinterConfig, cfg.id)
        c.print_profile = print_profile
        p = await s.get(Printer, c.printer_id)
        p.default_process_profile = default
        await s.commit()


def test_effective_process_profile_prefers_the_job_then_the_printer():
    printer = SimpleNamespace(default_process_profile="P-A")
    assert effective_process_profile(SimpleNamespace(print_profile=""), printer) == "P-A"
    assert effective_process_profile(SimpleNamespace(print_profile="P-B"), printer) == "P-B"
    assert effective_process_profile(SimpleNamespace(print_profile=""), SimpleNamespace(default_process_profile=None)) == ""


def test_missing_process_reason_ignores_presliced_files():
    printer = SimpleNamespace(name="Shelf 2", default_process_profile=None)
    cfg = SimpleNamespace(print_profile="")
    assert missing_process_reason(SimpleNamespace(original_filename="part.gcode"), cfg, printer) is None
    assert missing_process_reason(SimpleNamespace(original_filename="part.gcode.3mf"), cfg, printer) is None
    reason = missing_process_reason(SimpleNamespace(original_filename="part.3mf"), cfg, printer)
    assert "Shelf 2" in reason and "default process profile" in reason
    assert missing_process_reason(SimpleNamespace(original_filename="part.3mf"), SimpleNamespace(print_profile="P"), printer) is None


@pytest.mark.asyncio
async def test_slice_params_use_the_printer_default(session_factory):
    job_id = await _seed_job(session_factory, printer_id=1)
    await _set(session_factory, job_id, print_profile="", default="P-A")
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        cfg = (await s.execute(JobPrinterConfig.__table__.select().where(JobPrinterConfig.job_id == job_id))).first()
        cfg = await s.get(JobPrinterConfig, cfg.id)
        printer = await s.get(Printer, 1)
        assert _slice_params(job, cfg, printer, None)["print_profile"] == "P-A"
        cfg.print_profile = "P-B"
        assert _slice_params(job, cfg, printer, None)["print_profile"] == "P-B"


@pytest.mark.asyncio
async def test_production_slice_uses_the_printer_default(session_factory, tmp_path):
    job_id = await _seed_job(session_factory, printer_id=1)
    await _set(session_factory, job_id, print_profile="", default="P-A")
    gcode = tmp_path / "out.gcode"
    gcode.write_text("G28\n")
    slicer = MagicMock()
    slicer.slice.return_value = str(gcode)
    qe = QueueEngine(session_factory, _make_mock_printer_manager([1]), slicer)
    _install_fake_put(qe)
    await qe._process_queue()
    async def _printing():
        async with session_factory() as s:
            return (await s.get(Job, job_id)).status == "printing"

    await wait_until(_printing, what="job to reach printing")      # let the background slice+upload finish inside the test
    assert slicer.slice.call_args.args[0].process_preset == "P-A"


@pytest.mark.asyncio
async def test_a_printer_without_a_default_skips_the_job_and_the_sweep_blocks_it_when_no_printer_can(session_factory):
    job_id = await _seed_job(session_factory, printer_id=1)
    await _set(session_factory, job_id, print_profile="", default=None)
    slicer = MagicMock()
    mgr = _make_mock_printer_manager([1])
    mgr.liveness.return_value = "online"
    qe = QueueEngine(session_factory, mgr, slicer)
    _install_fake_put(qe)
    async with session_factory() as s:
        await qe._try_claim_for_printer(s, 1)
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert (job.status, job.assigned_printer_id) == ("queued", None)      # skipped, not blocked: another printer might
    slicer.slice.assert_not_called()
    await qe._sweep_unmatched_jobs()
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert job.status == "blocked"
        assert "Printer 1" in job.block_reason and "default process profile" in job.block_reason


@pytest.mark.asyncio
async def test_a_printer_with_a_default_takes_the_job_a_printer_without_one_skipped(session_factory, tmp_path):
    job_id = await _seed_job(session_factory, printer_id=1)
    await _set(session_factory, job_id, print_profile="", default=None)
    async with session_factory() as s:
        s.add(Printer(configured=True, id=2, name="Printer 2", printer_type="elegoo_centauri", connection_config={},
                      machine_profile_override="Test Machine Preset", default_process_profile="P-2"))
        await s.flush()
        s.add(JobPrinterConfig(job_id=job_id, printer_id=2, print_profile="", filament_profile="PLA"))
        await s.commit()
    gcode = tmp_path / "o.gcode"
    gcode.write_text("G28\n")
    slicer = MagicMock()
    slicer.slice.return_value = str(gcode)
    qe = QueueEngine(session_factory, _make_mock_printer_manager([1, 2]), slicer)
    _install_fake_put(qe)
    async with session_factory() as s:
        await qe._try_claim_for_printer(s, 1, slice_only=True)
    async with session_factory() as s:
        assert (await s.get(Job, job_id)).status == "queued"
    await qe._process_queue()

    async def _printing():
        async with session_factory() as s:
            return (await s.get(Job, job_id)).status == "printing"

    await wait_until(_printing, what="printer 2 to take the job")
    async with session_factory() as s:
        assert (await s.get(Job, job_id)).assigned_printer_id == 2
    assert slicer.slice.call_args.args[0].process_preset == "P-2"


@pytest.mark.asyncio
async def test_estimate_without_any_process_fails_with_the_actionable_reason(session_factory, caplog):
    job_id = await _seed_job(session_factory, printer_id=1)
    await _set(session_factory, job_id, print_profile="", default=None)
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.estimate_status, job.estimate_token = "pending", 1
        await s.commit()
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path(tempfile.mkdtemp())
    engine = QueueEngine(session_factory, _make_mock_printer_manager([1]), slicer)
    await engine.run_estimate(job_id)
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert job.estimate_status == "failed"
    assert "default process profile" in caplog.text            # the reason is logged (estimates store no message)
    slicer.slice.assert_not_called()


@pytest.mark.asyncio
async def test_estimate_uses_the_printer_default(session_factory):
    job_id = await _seed_job(session_factory, printer_id=1)
    await _set(session_factory, job_id, print_profile="", default="P-A")
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.estimate_status, job.estimate_token = "pending", 1
        p = await s.get(Printer, 1)
        p.loaded_filaments = [{"filament_profile": "PLA Generic", "type": "PLA", "color": ""}]
        await s.commit()
    with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False, mode="w") as f:
        f.write("; filament used [g] = 10.00\n; estimated printing time (normal mode) = 30m 0s\n")
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path(tempfile.mkdtemp())
    engine = QueueEngine(session_factory, _make_mock_printer_manager([1]), slicer)
    _install_fake_put(engine)
    with patch.object(slicer, "slice", return_value=f.name):
        await engine.run_estimate(job_id)
        assert slicer.slice.call_args.args[0].process_preset == "P-A"
