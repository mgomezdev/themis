"""BIZ-366 v2: cancelling a STARTED print (job cancel, or a printer stop) settles that print attempt through `job.attempt_ended`;
cancelling a job that never started publishes nothing."""
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from app.models import EventOutbox, Job
from app.services import job_details


async def _printing(session_factory, job_id, printer_id, *, started=True):
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.status, job.assigned_printer_id = "printing", printer_id
        if started:
            await job_details.set_details(s, job_id, "core", {"filament_usage_context": {
                "attempt_id": "job.started:test", "printer_id": printer_id, "slot_plan": [], "loaded_filaments": []}})
        await s.commit()


async def _ended(session_factory):
    async with session_factory() as s:
        return [r.envelope for r in (await s.execute(select(EventOutbox).where(EventOutbox.name == "job.attempt_ended"))).scalars()]


async def test_cancelling_a_printing_job_settles_its_attempt(client, create_job, create_printer, session_factory):
    printer_id = await create_printer()
    job_id = await create_job(printer_id=printer_id)
    await _printing(session_factory, job_id, printer_id)
    with patch("app.api.routes.jobs.printer_manager") as pm:
        pm._clients.get.return_value = None
        r = await client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert r.status_code == 200, r.text
    (env,) = await _ended(session_factory)
    assert env["payload"]["result"] == "cancelled" and env["payload"]["attempt_id"] == "job.started:test"
    assert env["payload"]["context"]["attempt_id"] == "job.started:test" and env["entities"]["printer_id"] == printer_id


async def test_cancelling_a_job_that_never_started_publishes_no_attempt_end(client, create_job, create_printer, session_factory):
    printer_id = await create_printer()
    job_id = await create_job(printer_id=printer_id)
    r = await client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert r.status_code == 200, r.text
    assert await _ended(session_factory) == []


async def test_stopping_a_printer_settles_the_attempt_it_was_printing(client, create_job, create_printer, session_factory):
    printer_id = await create_printer()
    job_id = await create_job(printer_id=printer_id)
    await _printing(session_factory, job_id, printer_id)
    fake = MagicMock(connected=True)
    with patch("app.api.routes.printers.printer_manager") as pm:
        pm._clients.get.return_value = fake
        r = await client.post(f"/api/v1/printers/{printer_id}/stop")
    assert r.status_code in (200, 204), r.text
    fake.stop_print.assert_called_once()
    (env,) = await _ended(session_factory)
    assert env["payload"]["result"] == "cancelled"
    async with session_factory() as s:
        assert (await s.get(Job, job_id)).status == "cancelled"
