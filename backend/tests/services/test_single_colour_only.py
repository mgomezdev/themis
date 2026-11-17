"""BIZ-358: a "single colour only" printer takes any loaded colour but never a multicolour job (2+ colours in filament_map). It skips
such work (the job stays queued) and claims what is behind it; when no printer in the fleet could take it the sweep blocks it."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models import Job, JobPrinterConfig, Printer
from app.services import filament_matching
from app.services.queue_engine import QueueEngine
from tests.services.test_queue_engine import _install_fake_put, _make_mock_printer_manager, _seed_job
from tests.waiting import cancel_background_tasks

SLOTS = [{"slot": 0, "type": "PLA", "color": "#FF0000", "filament_profile": "PLA"},
         {"slot": 1, "type": "PLA", "color": "#0000FF", "filament_profile": "PLA"}]
TWO = [{"model_filament": 1, "filament_type": "PLA", "filament_subtype": "any", "filament_color": "#FF0000"},
       {"model_filament": 2, "filament_type": "PLA", "filament_subtype": "any", "filament_color": "#0000FF"}]
ONE = TWO[:1]


async def _setup(factory, job_id, fmap, *, single_colour_only=True, position=None):
    async with factory() as s:
        cfg = (await s.execute(JobPrinterConfig.__table__.select().where(JobPrinterConfig.job_id == job_id))).first()
        c = await s.get(JobPrinterConfig, cfg.id)
        c.filament_map = fmap
        p = await s.get(Printer, c.printer_id)
        p.single_colour_only, p.loaded_filaments = single_colour_only, SLOTS
        if position is not None:
            (await s.get(Job, job_id)).queue_position = position
        await s.commit()


def test_evaluate_for_printer_refuses_multicolour_on_a_single_colour_printer():
    single = SimpleNamespace(name="Mini", single_colour_only=True, loaded_filaments=SLOTS)
    multi = SimpleNamespace(name="X1", single_colour_only=False, loaded_filaments=SLOTS)
    v = filament_matching.evaluate_for_printer(SimpleNamespace(filament_map=TWO, tool_index=None), single)
    assert (v.eligible, v.reasons) == (False, ["Mini takes single-colour work only"])
    assert filament_matching.evaluate_for_printer(SimpleNamespace(filament_map=TWO, tool_index=None), multi).eligible
    assert filament_matching.evaluate_for_printer(SimpleNamespace(filament_map=ONE, tool_index=None), single).eligible
    assert filament_matching.is_multicolour(SimpleNamespace(filament_map=TWO))
    assert not filament_matching.is_multicolour(SimpleNamespace(filament_map=None))


@pytest.mark.asyncio
async def test_single_colour_printer_skips_multicolour_and_claims_the_job_behind_it(session_factory, tmp_path):
    multi = await _seed_job(session_factory, printer_id=1)
    single = await _seed_job(session_factory, printer_id=1)
    await _setup(session_factory, multi, TWO, position=1.0)
    await _setup(session_factory, single, ONE, position=2.0)
    gcode = tmp_path / "o.gcode"
    gcode.write_text("G28\n")
    slicer = MagicMock()
    slicer.slice.return_value = str(gcode)
    qe = QueueEngine(session_factory, _make_mock_printer_manager([1]), slicer)
    _install_fake_put(qe)
    async with session_factory() as s:
        await qe._try_claim_for_printer(s, 1, slice_only=True)
    await cancel_background_tasks("slice-")                 # only the claim is under test; its slice task must not outlive the loop
    async with session_factory() as s:
        assert (await s.get(Job, multi)).status == "queued"
        assert (await s.get(Job, single)).assigned_printer_id == 1


@pytest.mark.asyncio
async def test_sweep_blocks_multicolour_work_when_the_only_printer_is_single_colour(session_factory):
    job_id = await _seed_job(session_factory, printer_id=1)
    await _setup(session_factory, job_id, TWO)
    mgr = _make_mock_printer_manager([1])
    mgr.liveness.return_value = "online"
    qe = QueueEngine(session_factory, mgr, MagicMock())
    await qe._sweep_unmatched_jobs()
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert job.status == "blocked"
        assert "single-colour work only" in job.block_reason


async def test_job_detail_says_why(client, create_job, create_printer):
    pid = await create_printer(loaded_filaments=SLOTS)
    job_id = await create_job(printer_id=pid, filament_map=TWO)
    assert (await client.patch(f"/api/v1/printers/{pid}", json={"single_colour_only": True})).status_code == 200
    (cfg,) = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()["printer_configs"]
    assert cfg["filament_match"]["eligible"] is False
    assert any("single-colour work only" in r for r in cfg["filament_match"]["reasons"])
