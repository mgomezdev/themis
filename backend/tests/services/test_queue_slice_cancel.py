"""BIZ-295: a production slice carries a CancelToken; job cancel and printer stop set it, and the dispatch then ends quietly
(the job stays cancelled, no G-code is recorded, the printer's config is never marked slice_failed)."""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models import GcodeFile, Job, JobPrinterConfig, Printer
from app.services.queue_engine import QueueEngine
from app.services.slicer_service import SliceCancelled, SlicerService
from tests.services.test_queue_engine import _install_fake_put, _make_mock_printer_manager, _seed_job


async def _engine_with_a_blocking_slice(db, tmp_path):
    job_id = await _seed_job(db, 1)
    async with db() as s:
        (await s.get(Printer, 1)).machine_profile_override = "Test Machine"
        await s.commit()
    started, seen = threading.Event(), {}

    def slow_slice(req, output_dir=None, cancel=None):
        seen["token"] = cancel
        started.set()
        for _ in range(500):                      # what Laminus's poll loop does: stop once the token is set
            if cancel is not None and cancel.cancelled:
                raise SliceCancelled("cancelled")
            threading.Event().wait(0.01)
        raise AssertionError("the slice was never cancelled")

    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = tmp_path
    slicer.slice.side_effect = slow_slice
    engine = QueueEngine(db, _make_mock_printer_manager([1]), slicer)
    _install_fake_put(engine)
    return engine, job_id, started, seen


async def _cancel_job_row(db, job_id):
    async with db() as s:
        (await s.get(Job, job_id)).status = "cancelled"
        await s.commit()


@pytest.mark.parametrize("by", ["job", "printer"])
async def test_cancelling_stops_the_running_slice_and_fails_nothing(session_factory, tmp_path, by):
    engine, job_id, started, seen = await _engine_with_a_blocking_slice(session_factory, tmp_path)
    with patch.object(engine, "_do_upload_and_print", new_callable=AsyncMock) as upload:
        run = asyncio.create_task(engine._run_slice_and_print(job_id, 1, 1))
        assert await asyncio.to_thread(started.wait, 5)
        assert seen["token"].cancelled is False
        await _cancel_job_row(session_factory, job_id)
        hit = engine.cancel_slice(job_id=job_id) if by == "job" else engine.cancel_slice(printer_id=1)
        assert hit == [job_id]
        await asyncio.wait_for(run, 10)

    assert seen["token"].cancelled is True
    upload.assert_not_called()
    async with session_factory() as s:
        assert (await s.get(Job, job_id)).status == "cancelled"
        assert (await s.execute(select(GcodeFile))).first() is None
        cfg = (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))).scalar_one()
        assert not cfg.slice_failed
    assert engine._slice_cancels == {}                          # nothing left registered


async def test_cancel_slice_only_touches_the_named_job_or_printer(session_factory, tmp_path):
    engine, job_id, started, seen = await _engine_with_a_blocking_slice(session_factory, tmp_path)
    with patch.object(engine, "_do_upload_and_print", new_callable=AsyncMock):
        run = asyncio.create_task(engine._run_slice_and_print(job_id, 1, 1))
        assert await asyncio.to_thread(started.wait, 5)
        assert engine.cancel_slice(job_id=job_id + 1) == [] and engine.cancel_slice(printer_id=2) == []
        assert seen["token"].cancelled is False
        await _cancel_job_row(session_factory, job_id)
        engine.cancel_slice(job_id=job_id)
        await asyncio.wait_for(run, 10)


async def _job(client, upload_3mf, create_printer):
    file_id, pid = await upload_3mf(), await create_printer()
    with patch("app.api.routes.jobs.queue_engine"):
        job = (await client.post("/api/v1/jobs", json={"uploaded_file_id": file_id, "plate_number": 1,
                                                       "printer_configs": [{"printer_id": pid, "print_profile": "0.20mm"}]})).json()
    return job["id"], pid


async def test_the_cancel_job_route_cancels_its_slice(client, upload_3mf, create_printer):
    from app.services.queue_engine import queue_engine
    job_id, _ = await _job(client, upload_3mf, create_printer)
    with patch.object(queue_engine, "cancel_slice", return_value=[]) as cancel:
        r = await client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert r.status_code == 200, r.text
    cancel.assert_called_once_with(job_id=job_id)


async def test_stopping_a_printer_cancels_the_job_it_is_slicing_and_its_slice(client, create_printer, session_factory, upload_3mf):
    from app.services.queue_engine import queue_engine
    job_id, pid = await _job(client, upload_3mf, create_printer)
    job = {"id": job_id}
    async with session_factory() as s:
        j = await s.get(Job, job["id"])
        j.status, j.assigned_printer_id = "slicing", pid
        await s.commit()
    client_mock = MagicMock(connected=True)
    with patch("app.api.routes.printers._get_connected_client", return_value=client_mock), \
         patch.object(queue_engine, "cancel_slice", return_value=[job["id"]]) as cancel:
        r = await client.post(f"/api/v1/printers/{pid}/stop")
    assert r.status_code == 200, r.text
    cancel.assert_called_once_with(printer_id=pid)
    async with session_factory() as s:
        assert (await s.get(Job, job["id"])).status == "cancelled"


async def test_a_slice_failure_after_the_job_was_cancelled_leaves_it_cancelled(session_factory, tmp_path):
    """The cancel committed between the provider's failure and its handling: the job stays cancelled, the config unmarked."""
    job_id = await _seed_job(session_factory, 1)
    await _cancel_job_row(session_factory, job_id)
    engine = QueueEngine(session_factory, _make_mock_printer_manager([1]), MagicMock(spec=SlicerService))
    await engine._handle_slice_failure(job_id, 1, "engine exploded")
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert (job.status, job.block_reason) == ("cancelled", None)
        cfg = (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))).scalar_one()
        assert not cfg.slice_failed and cfg.slice_error is None
