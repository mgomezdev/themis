"""A printer's plate and part waivers: waived parts and test prints, reporting what printed on a cleared plate, and the plate-cleared (ready for new work) gate.

Part of the `printers` routes: registered on the same `router` and imported at the bottom of printers.py.
What tests replace on that module is read as `_printers.<name>` at call time, so one patch
point covers every printers route.
"""
from __future__ import annotations

from typing import Optional
from fastapi import Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from ...auth import require_scope
from ...database import get_session
from ...models import Job, JobModelTarget, JobPrinterConfig, Printer
from ...services import print_start, print_results

from . import printers as _printers
from .printers import (  # noqa: I001
    PlateResults,
    _get_or_404,
    router,
)


@router.get("/{printer_id}/waivers", summary="Parts this printer is waived for (BIZ-365)",
            responses={404: {"description": "Printer not found"}}, dependencies=[Depends(require_scope("printers:read"))])
async def list_waivers(printer_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Each part this printer keeps failing and no longer gets: failures here, last failure, projects it failed in."""
    from ...services import part_waivers
    await _get_or_404(printer_id, session)
    return await part_waivers.listing(session, printer_id)


@router.delete("/{printer_id}/waivers", status_code=204, summary="Clear all of a printer's waivers (BIZ-365)",
               responses={404: {"description": "Printer not found"}}, dependencies=[Depends(require_scope("printers:write"))])
async def clear_all_waivers(printer_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    from ...services import part_waivers
    await _get_or_404(printer_id, session)
    await part_waivers.clear(session, printer_id)
    await session.commit()
    _printers.queue_engine.wake()
    return Response(status_code=204)


@router.delete("/{printer_id}/waivers/{waiver_id}", status_code=204, summary="Clear one waiver (BIZ-365)",
               responses={404: {"description": "Printer or waiver not found"}},
               dependencies=[Depends(require_scope("printers:write"))])
async def clear_waiver(printer_id: int, waiver_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    from ...services import part_waivers
    await _get_or_404(printer_id, session)
    if not await part_waivers.clear(session, printer_id, waiver_id):
        raise HTTPException(404, f"Waiver {waiver_id} not found on printer {printer_id}")
    await session.commit()
    _printers.queue_engine.wake()
    return Response(status_code=204)


@router.post("/{printer_id}/waivers/{waiver_id}/test-print", summary="Test-print a waived part on this printer (BIZ-365)",
             description="One copy of the part, on this printer only (its default process profile + loaded filament). The printer "
                         "claims nothing else until it has printed it; its plate-clear dialog then offers to clear the waiver.",
             responses={404: {"description": "Printer or waiver not found"}, 409: {"description": "A test print is already pending"}},
             dependencies=[Depends(require_scope("printers:control")), Depends(require_scope("jobs:write"))])
async def test_print_waiver(printer_id: int, waiver_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    from ...models import PartWaiver
    from ...services import part_waivers, print_results
    from sqlalchemy import delete as _delete, select as _select
    printer = await _get_or_404(printer_id, session)
    waiver = await session.get(PartWaiver, waiver_id)
    if waiver is None or waiver.printer_id != printer_id:
        raise HTTPException(404, f"Waiver {waiver_id} not found on printer {printer_id}")
    if printer.test_print_job_id is not None:
        raise HTTPException(409, f"{printer.name} already has a test print pending")
    source, item = await part_waivers.source_of(session, waiver)
    if source is None or item is None:
        raise HTTPException(409, "The part's project is gone: there is nothing left to test-print it from")
    from ...models import Project
    proj = await session.get(Project, source.project_id)
    if proj is None or proj.stage != "queued":
        raise HTTPException(409, "The part's project is not in the queue: queue it to test-print this part")
    used = (await session.execute(_select(JobPrinterConfig.print_profile).where(
        JobPrinterConfig.job_id == source.id, JobPrinterConfig.printer_id == printer_id))).scalar()
    if not printer.default_process_profile and not used:
        raise HTTPException(422, f"{printer.name} has no default process profile to test-print with: set one first")
    (job_id, *_) = await print_results.requeue_parts(session, source, {item.id: 1}) or [None]
    if job_id is None:
        raise HTTPException(422, "The part could not be prepared for a test print")
    # This printer only, with its own default process profile (else the profile it used for the failed job); the ask is the part's.
    profile = "" if printer.default_process_profile else used
    await session.execute(_delete(JobModelTarget).where(JobModelTarget.job_id == job_id))
    await session.execute(_delete(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))
    from ._filament_ask import tuple_columns, row_tuple
    session.add(JobPrinterConfig(job_id=job_id, printer_id=printer_id, print_profile=profile, **tuple_columns(row_tuple(item))))
    printer = await session.get(Printer, printer_id)
    printer.test_print_job_id, printer.test_print_waiver_id = job_id, waiver_id
    await session.commit()
    _printers.queue_engine.wake()
    return {"job_id": job_id}


@router.delete("/{printer_id}/test-print", status_code=204, summary="Cancel a printer's pending test print (BIZ-365)",
               responses={404: {"description": "Printer not found"}},
               dependencies=[Depends(require_scope("printers:control"))])
async def cancel_test_print(printer_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    """The printer goes back to queue work; a test job that has not started is cancelled."""
    printer = await _get_or_404(printer_id, session)
    if printer.test_print_job_id is not None:
        job = await session.get(Job, printer.test_print_job_id)
        if job is not None and job.status in ("queued", "blocked"):
            job.status = "cancelled"
        printer.test_print_job_id = printer.test_print_waiver_id = None
        await session.commit()
        _printers.queue_engine.wake()
    return Response(status_code=204)


@router.get(
    "/{printer_id}/plate-results",
    summary="The project job whose print results are still to report on this plate (BIZ-364)",
    responses={404: {"description": "Printer not found"}},
    dependencies=[Depends(require_scope("printers:read"))],
)
async def plate_results(printer_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """The latest finished project job on this printer with no reported results yet, and its parts (name × quantity on the
    plate), so the plate-clear dialog can ask what printed. `job_id` null = nothing to report: a plain clear."""
    import json as _json
    from sqlalchemy import select as _select
    from ...models import Job, JobItemFailure, ProjectItem, UploadedFile
    await _get_or_404(printer_id, session)
    reported = _select(JobItemFailure.job_id).where(JobItemFailure.resolution.is_not(None))
    printer = await session.get(Printer, printer_id)
    job = (await session.execute(
        _select(Job).where(Job.assigned_printer_id == printer_id, Job.project_id.is_not(None),
                           Job.project_item_quantities.is_not(None), Job.status.in_(["complete", "failed"]),
                           Job.id.not_in(reported), Job.outcome.is_(None))
        .order_by(Job.updated_at.desc(), Job.id.desc()).limit(1))).scalar_one_or_none()
    if job is None:
        return {"job_id": None, "project_id": None, "parts": [], "test_print_waiver_id": None, "print_failed": False}
    parts = []
    for key, qty in _json.loads(job.project_item_quantities).items():
        item = await session.get(ProjectItem, int(key))
        if item is None or int(qty) <= 0:
            continue
        f = await session.get(UploadedFile, item.file_id)
        parts.append({"project_item_id": item.id, "file_name": f.original_filename if f else f"[file {item.file_id}]",
                      "quantity": int(qty)})
    test_waiver = printer.test_print_waiver_id if printer is not None and printer.test_print_job_id == job.id else None
    # `print_failed`: the dialog opens on "whole plate failed" for a print that ended in failure, "nothing failed" otherwise.
    return {"job_id": job.id, "project_id": job.project_id, "parts": parts, "test_print_waiver_id": test_waiver,
            "print_failed": job.status == "failed"}


@router.post(
    "/{printer_id}/plate-cleared",
    summary="Confirm plate cleared",
    responses={
        404: {"description": "Printer not found"},
        409: {"description": "Printer is busy or the supplied job is not a finished project job"},
    },
    dependencies=[],      # printers:control is checked by the `key` dependency below (results also need jobs:write)
)
async def plate_cleared(
    printer_id: int,
    body: Optional[PlateResults] = None,
    key=Depends(require_scope("printers:control")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Mark the printer's bed as cleared so the queue engine can assign the next job. With `results` (BIZ-364), also record
    what printed on `job_id` (a project job this printer ran): failed parts are requeued in their print group, or held once
    they have failed on `part_retry_limit` different printers. No body = a plain clear (the QR / home-automation hook)."""
    if body is not None and body.job_id is not None and await print_results.has_pending(session, body.job_id):
        return await _resume_interrupted_requeue(printer_id, body.job_id, key, session)
    lock = print_start.lock_for(printer_id)
    if lock.locked():
        raise HTTPException(409, "Printer is busy. Confirm clearance once it is ready.")
    async with lock:
        job, recorded, assumed = await _save_plate_clear(printer_id, body, key, session)
    outcome: dict = {}
    if recorded:
        requeued = await print_results.requeue_reported(session, job, recorded["requeue"])
        outcome = {**requeued, "held": recorded["held"] + requeued["held"]}
    for a in assumed:                      # a plate counted as failed by default: its parts are packed again
        if a["requeue"]:
            again = await print_results.requeue_reported(session, await session.get(Job, a["job_id"]), a["requeue"])
            a["requeued_job_ids"] = again["requeued_job_ids"]
            for key in ("held", "requeue_errors"):          # a repack that could not be built holds the parts for a person
                if again.get(key):
                    a[key] = again[key]
    if assumed:
        outcome["assumed"] = [{k: v for k, v in a.items() if k != "requeue"} for a in assumed]
    _printers.queue_engine.wake()
    return {"ok": True, **outcome}


async def _resume_interrupted_requeue(printer_id: int, job_id: int, key, session: AsyncSession) -> dict:
    """GH #227: this job's results (and the clear) were committed, but repacking its failed parts was interrupted. The
    recorded results stand; a retry only finishes the repack — nothing is recorded or counted twice."""
    if "jobs:write" not in (getattr(key, "scopes", None) or []):
        raise HTTPException(403, "API key lacks required scope: jobs:write (reporting print results changes the job's project)")
    job = await session.get(Job, job_id)
    if job is None or job.assigned_printer_id != printer_id:
        raise HTTPException(422, f"Job {job_id} did not print on this printer")
    requeued = await print_results.requeue_reported(session, job)
    _printers.queue_engine.wake()
    return {"ok": True, **requeued}


async def _save_plate_clear(printer_id: int, body: PlateResults | None, key, session: AsyncSession):
    printer = await _get_or_404(printer_id, session)
    client = _printers.printer_manager._clients.get(printer_id)
    if client is not None and client.connected and not client.is_idle:
        raise HTTPException(409, "Printer is busy. Confirm clearance once it is ready.")
    recorded: dict | None = None
    job = None
    if body is not None and body.job_id is not None:
        if "jobs:write" not in (getattr(key, "scopes", None) or []):
            raise HTTPException(403, "API key lacks required scope: jobs:write (reporting print results changes the job's project)")
        job = await session.get(Job, body.job_id)
        if job is None or job.assigned_printer_id != printer_id:
            raise HTTPException(422, f"Job {body.job_id} did not print on {printer.name}")
        if job.project_id is None or job.status not in ("complete", "failed"):
            raise HTTPException(409, f"Job {body.job_id} is not a finished project job")
        if printer.test_print_job_id == job.id:
            job.outcome = "reviewed"           # a test print (BIZ-365) is an extra copy: it credits nothing to the project
        else:
            recorded = await print_results.record_and_restore(
                session, job, printer_id, {r.project_item_id: r.quantity_failed for r in body.results})
    # Every other finished plate of this printer that nobody reported (all of them, on a clear without results) gets the
    # Settings default for how its print ended: nothing is left in limbo, and what was assumed is recorded on the job.
    # A write first, so the transaction is open before the savepoint: with SQLite a SAVEPOINT that opens the transaction is
    # committed for good by its RELEASE, and assumed results could then outlive a failed clear.
    await session.execute(sa_update(Printer).where(Printer.id == printer_id).values(awaiting_plate_clear=Printer.awaiting_plate_clear))
    try:
        async with session.begin_nested():     # bookkeeping must never fail the clear itself (the QR / home-automation hook)
            assumed = await print_results.apply_clear_defaults(session, printer)
    except Exception:                          # noqa: BLE001 - the plates stay awaiting results; History can still report them
        import logging
        logging.getLogger("app").exception("Applying the plate-clear defaults for printer %s failed", printer_id)
        assumed = []
    if job is None and printer.test_print_job_id is not None:          # a plain clear (QR / home automation) after a test print
        test_job = await session.get(Job, printer.test_print_job_id)
        if test_job is not None and test_job.status in ("complete", "failed"):
            test_job.outcome = test_job.outcome or "reviewed"
            printer.test_print_job_id = printer.test_print_waiver_id = None
    if job is not None and printer.test_print_job_id == job.id:       # BIZ-365: the test print is done
        if body is not None and body.clear_waiver and printer.test_print_waiver_id is not None:
            from ...services import part_waivers
            await part_waivers.clear(session, printer_id, printer.test_print_waiver_id)
        printer.test_print_job_id = printer.test_print_waiver_id = None
    printer.awaiting_plate_clear = False
    await session.commit()                 # results + the clear are in before any repacking
    _printers.printer_manager.set_awaiting_plate_clear(printer_id, False)
    return job, recorded, assumed
