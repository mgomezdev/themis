"""What an operator does to an existing job: cancel, edit its printers, unblock, reorder, verify-slice, complete manually, schedule, link to a project, save its slice, set cost, report an outcome.

Part of the `jobs` routes: registered on the same `router` and imported at the bottom of jobs.py.
What tests replace on that module (_manual_complete_in_flight, get_format_provider, printer_manager, queue_engine) is read as `_jobs.<name>` at call time, so one patch
point covers every jobs route.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime, timezone
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from ...auth import require_scope
from ...database import get_session
from ...models import GcodeFile, Job, JobItemFailure, JobModelTarget, JobPrinterConfig, Printer, Project, ProjectItem, QueueConfig, UploadedFile
from ...services import completion_events, slice_saver
from ...eventing.hub import hub as event_hub
from ...services.library_scanner import is_presliced_file
from ...services import filament_matching, model_targets, slicer_profiles
from ...services.queue_engine import _slice_params, build_slice_request
from ...services.slicer_service import SliceError, SliceRequest
from ._filament_ask import ask_columns

from . import jobs as _jobs
from .jobs import (  # noqa: I001
    ModelTargetInput,
    PrinterConfigInput,
    _CANCELLABLE_STATUSES,
    _PRINTER_ACTIVE_STATUSES,
    _add_targets,
    _apply_gcode_estimate,
    _cached_version_of,
    _check_gcode_printers,
    _check_machine_eligibility,
    _check_version_printers,
    _clean_overrides,
    _front_queue_position,
    _get_or_404,
    _linkable_project,
    _normalise_not_before,
    _require_known_models,
    _to_dict,
    _validate_targets,
    logger,
    router,
)


@router.post(
    "/{job_id}/cancel",
    summary="Cancel job",
    responses={
        404: {"description": "Job not found"},
        422: {"description": "Job is in a non-cancellable status"},
    },
    dependencies=[Depends(require_scope("jobs:write"))],
)
async def cancel_job(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Cancel a queued, printing, paused, slicing, or failed job. If a printer is
    physically running the job it receives a stop command. A parked gcode file is deleted."""
    job = await _get_or_404(job_id, session)
    if job.status not in _CANCELLABLE_STATUSES:
        raise HTTPException(422, f"Job in status {job.status!r} cannot be cancelled")

    # If a printer is physically running this job, tell it to stop and free it.
    stop_printer_id = (
        job.assigned_printer_id
        if job.status in _PRINTER_ACTIVE_STATUSES else None
    )
    # "sliced" jobs have a parked gcode file on disk; clean it up.
    if job.status == "sliced":
        gcode_row = (await session.execute(
            select(GcodeFile).where(GcodeFile.job_id == job_id)
        )).scalar_one_or_none()
        if gcode_row is not None:
            try:
                os.remove(gcode_row.path)
            except OSError:
                pass
            await session.delete(gcode_row)

    if getattr(job, "estimate_status", None) == "pending":
        job.estimate_status = None
    if job.status in ("printing", "paused"):
        # A started print stopped mid-way still used filament: the attempt is settled from what the plugin reported (BIZ-366 v2).
        from ...services import completion_events
        printer = await session.get(Printer, job.assigned_printer_id) if job.assigned_printer_id is not None else None
        await completion_events.enqueue_attempt_ended(session, job.id, job.assigned_printer_id, result="cancelled",
                                                      loaded_filaments=printer.loaded_filaments if printer else None)
    job.status = "cancelled"
    job.completed_at = datetime.now(timezone.utc).isoformat()
    job.assigned_printer_id = None
    job.queue_position = None
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    event_hub.wake()                             # deliver a `job.attempt_ended` written above
    await session.refresh(job)
    _jobs.queue_engine.cancel_slice(job_id=job_id)     # a slice in flight stops at its next poll (BIZ-295)

    if stop_printer_id is not None:
        client = _jobs.printer_manager._clients.get(stop_printer_id)
        if client is not None and client.connected:
            # Cancelling a running job stops the printer — jobs:write alone grants hardware stop.
            # stop_print blocks on the websocket ack; don't stall the event loop.
            try:
                await asyncio.to_thread(client.stop_print)
            except Exception:  # best-effort — the job is already cancelled
                pass
        _jobs.queue_engine.wake()  # let the freed printer claim the next job once idle

    return _to_dict(job)


class JobConfigsUpdate(BaseModel):
    printer_configs: list[PrinterConfigInput] = []
    model_targets: list[ModelTargetInput] = []
    overrides: dict | None = None
    confirm_unknown_eligibility: bool | None = None     # None = keep the job's current confirmation


@router.patch(
    "/{job_id}/configs",
    summary="Update job configs",
    responses={
        404: {"description": "Job or printer not found"},
        422: {"description": "Job is not in an editable status, or no printer_configs/model_targets given"},
    },
    dependencies=[Depends(require_scope("jobs:write"))],
)
async def update_job_configs(
    job_id: int,
    body: JobConfigsUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Replace printer configs for a queued/blocked/failed job and re-queue it."""
    _EDITABLE = {"queued", "blocked", "failed"}
    job = await _get_or_404(job_id, session)
    if job.status not in _EDITABLE:
        raise HTTPException(422, f"Job in status {job.status!r} cannot be edited")
    if not body.printer_configs and not body.model_targets:
        raise HTTPException(422, "printer_configs or model_targets must not be empty")
    _validate_targets(body.model_targets)
    await _require_known_models(session, body.model_targets)
    for cfg in body.printer_configs:
        if await session.get(Printer, cfg.printer_id) is None:
            raise HTTPException(404, f"Printer {cfg.printer_id} not found")
    edited_file = await session.get(UploadedFile, job.uploaded_file_id)
    if is_presliced_file(edited_file) and any(c.overrides for c in [*body.printer_configs,*body.model_targets]):
        raise HTTPException(422,"Target overrides cannot apply to pre-sliced G-code")
    await _check_gcode_printers(session, edited_file, body.printer_configs, body.model_targets)
    confirmed = job.eligibility_confirmed if body.confirm_unknown_eligibility is None else body.confirm_unknown_eligibility
    await _check_machine_eligibility(session, edited_file, body.printer_configs, body.model_targets, confirmed)
    job.eligibility_confirmed = bool(confirmed and is_presliced_file(edited_file))
    await _check_version_printers(
        session, await _cached_version_of(session, edited_file), body.printer_configs, body.model_targets)

    # Configs first (they reference the targets), then the targets themselves.
    await session.execute(delete(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))
    await session.execute(delete(JobModelTarget).where(JobModelTarget.job_id == job_id))

    # Clear stale estimate fields
    job.estimate_status = None
    job.estimate_seconds = None
    job.estimate_filament_grams = None
    job.estimate_filament_breakdown = None
    job.estimate_preset_label = None

    for cfg in body.printer_configs:
        session.add(JobPrinterConfig(
            job_id=job_id,
            printer_id=cfg.printer_id,
            print_profile=cfg.print_profile,
            # Never null — legacy DBs have a NOT NULL constraint here. Blank = the matched slot's preset supplies it.
            filament_profile=cfg.filament_profile or "",
            **ask_columns(cfg),
            tool_index=cfg.tool_index,
            filament_map=cfg.filament_map,
            overrides=cfg.overrides,
            slice_failed=False,
            slice_error=None,
        ))
    _add_targets(session, job_id, body.model_targets)
    await session.flush()
    await model_targets.materialize_job(session, job_id)

    job.status = "queued"
    job.block_reason = None
    job.unmatched_filaments = None
    job.assigned_printer_id = None
    uploaded_file = await session.get(UploadedFile, job.uploaded_file_id)
    gcode_job = is_presliced_file(uploaded_file)
    job.overrides = None if gcode_job else _clean_overrides(body.overrides)
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    await session.refresh(job)
    _jobs.queue_engine.wake()

    if gcode_job:
        _apply_gcode_estimate(job, uploaded_file)
        await session.commit()
        return _to_dict(job)

    queue_cfg = await session.get(QueueConfig, 1)
    estimates_enabled = queue_cfg is not None and queue_cfg.estimates_enabled
    if estimates_enabled:
        job.estimate_token = (job.estimate_token or 0) + 1
        job.estimate_status = "pending"
        await session.commit()
        _jobs.queue_engine.spawn_estimate(job.id)

    return _to_dict(job)


@router.post(
    "/{job_id}/unblock",
    summary="Unblock job",
    responses={
        404: {"description": "Job not found"},
        422: {"description": "Job is not in blocked status"},
    },
    dependencies=[Depends(require_scope("jobs:write"))],
)
async def unblock_job(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-queue a blocked job at the top of the queue and wake the engine.

    Clears any prior slice-failure flags so the job is actually re-sliced — a
    config left with ``slice_failed`` would otherwise be re-blocked on the next
    claim with its stale error, never retrying."""
    job = await _get_or_404(job_id, session)
    if job.status != "blocked":
        raise HTTPException(422, f"Job {job_id} has status {job.status!r} — only blocked jobs can be unblocked")
    configs = await session.execute(
        select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id)
    )
    for cfg in configs.scalars().all():
        cfg.slice_failed = False
        cfg.slice_error = None
    job.status = "queued"
    job.block_reason = None
    job.unmatched_filaments = None
    job.assigned_printer_id = None
    job.queue_position = await _front_queue_position(session)
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    await session.refresh(job)
    _jobs.queue_engine.wake()
    return _to_dict(job)


_REORDERABLE_STATUSES = {"queued", "blocked"}


class ReorderBody(BaseModel):
    action: str  # "promote" | "demote" | "front" | "back"


@router.post(
    "/{job_id}/reorder",
    summary="Reorder job in queue",
    responses={
        404: {"description": "Job not found"},
        422: {"description": "Job is not in a reorderable status or action is invalid"},
    },
    dependencies=[Depends(require_scope("jobs:write"))],
)
async def reorder_job(
    job_id: int,
    body: ReorderBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Move a queued or blocked job within the queue.
    Actions: 'front', 'back', 'promote' (one step up), 'demote' (one step down)."""
    if body.action not in ("promote", "demote", "front", "back"):
        raise HTTPException(422, f"Invalid action {body.action!r}")

    job = await _get_or_404(job_id, session)
    if job.status not in _REORDERABLE_STATUSES:
        raise HTTPException(422, f"Job in status {job.status!r} cannot be reordered")

    # All queued/blocked jobs ordered by position
    result = await session.execute(
        select(Job)
        .where(Job.status.in_(list(_REORDERABLE_STATUSES)))
        .order_by(Job.queue_position)
    )
    ordered = result.scalars().all()
    idx = next((i for i, j in enumerate(ordered) if j.id == job_id), None)
    if idx is None:
        raise HTTPException(422, "Job not found in reorderable queue")

    positions = [j.queue_position for j in ordered]

    if body.action == "front":
        new_pos = (positions[0] or 1.0) - 1.0
    elif body.action == "back":
        new_pos = (positions[-1] or 1.0) + 1.0
    elif body.action == "promote":
        if idx == 0:
            new_pos = job.queue_position  # already at front, no-op
        elif idx == 1:
            new_pos = (positions[0] or 1.0) - 1.0
        else:
            new_pos = (positions[idx - 2] + positions[idx - 1]) / 2.0
    else:  # demote
        if idx == len(ordered) - 1:
            new_pos = job.queue_position  # already at back, no-op
        elif idx == len(ordered) - 2:
            new_pos = (positions[-1] or 1.0) + 1.0
        else:
            new_pos = (positions[idx + 1] + positions[idx + 2]) / 2.0

    job.queue_position = new_pos
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    await session.refresh(job)
    _jobs.queue_engine.wake()
    return _to_dict(job)


class VerifySliceBody(BaseModel):
    printer_id: int


class CompleteManuallyBody(BaseModel):
    printer_id: int


def _build_slice_request(
    job: Job, config: JobPrinterConfig, printer: Printer, uploaded_file: UploadedFile, machine_preset: str | None,
) -> SliceRequest:
    """Build the SliceRequest for a debug/manual slice of `job` against `printer`'s
    matched `config` - shared by verify-slice and complete-manually, both of which
    slice in an isolated directory and never touch the job's production gcode path."""
    fit = filament_matching.evaluate(config, printer.loaded_filaments)
    if not fit.eligible:
        raise HTTPException(422, f"{printer.name}: " + "; ".join(fit.reasons))
    params = _slice_params(job, config, printer, uploaded_file, machine_preset, fit.plan)
    client = _jobs.printer_manager._clients.get(printer.id)
    try:
        req, _ = build_slice_request(job.id, printer.id, job.plate_number, params, client)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return req


@router.post(
    "/{job_id}/verify-slice",
    summary="Test-slice job",
    responses={
        404: {"description": "Job, printer, file, or printer config not found"},
    },
    dependencies=[Depends(require_scope("jobs:write"))],
)
async def verify_slice(
    job_id: int,
    body: VerifySliceBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Test-slice without printing or modifying job state. Debug use only.
    Returns `{ok: true}` on success or `{ok: false, error: "..."}` on failure."""
    job = await _get_or_404(job_id, session)

    printer = await session.get(Printer, body.printer_id)
    if printer is None:
        raise HTTPException(404, f"Printer {body.printer_id} not found")

    cfg_result = await session.execute(
        select(JobPrinterConfig).where(
            JobPrinterConfig.job_id == job_id,
            JobPrinterConfig.printer_id == body.printer_id,
        )
    )
    config = cfg_result.scalar_one_or_none()
    if config is None:
        raise HTTPException(404, f"Job {job_id} has no config for printer {body.printer_id}")

    uploaded_file = await session.get(UploadedFile, job.uploaded_file_id)
    if uploaded_file is None:
        raise HTTPException(404, f"File {job.uploaded_file_id} not found")

    if is_presliced_file(uploaded_file):
        raise HTTPException(422, "This job prints a pre-sliced file; there is nothing to slice")

    resolution = await slicer_profiles.resolve_for_printer(session, printer)
    if not resolution.profile:
        return {"ok": False, "error": resolution.reason or "Printer has no slicer machine profile"}

    req = _build_slice_request(job, config, printer, uploaded_file, resolution.profile)

    # Isolated from the production gcode dir (<data_dir>/gcode/<job_id>) — slice()
    # unlinks *.gcode/*.gcode.3mf in its output dir before writing, which would
    # otherwise destroy a parked job's already-produced production artifact.
    output_dir = _jobs.queue_engine._slicer._data_dir / "gcode_verify" / str(job_id)
    try:
        # Routed through the same serialized _slice_queue as production/estimate
        # slices (lowest priority) rather than queue_engine._executor directly —
        # that pool is also what upload_file/start_print depend on, and a
        # debug-only test-slice can block for minutes.
        await _jobs.queue_engine.run_verify_slice(req, output_dir)
        return {"ok": True, "error": None}
    except SliceError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected error in verify-slice for job %s", job_id)
        return {"ok": False, "error": f"Unexpected error: {exc}"}
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


_MANUAL_COMPLETE_TERMINAL_STATUSES = {"complete", "cancelled", "failed"}
_manual_complete_in_flight: set[int] = set()


@router.post(
    "/{job_id}/complete-manually",
    summary="Manually complete a job without printing it",
    responses={
        404: {"description": "Job, printer, or printer config not found"},
        409: {"description": "Job is terminal, or already being manually completed"},
        422: {"description": "No OrcaSlicer machine preset on the printer, the job prints pre-sliced gcode, or the slice failed"},
    },
    dependencies=[Depends(require_scope("jobs:write"))],
)
async def complete_job_manually(
    job_id: int,
    body: CompleteManuallyBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Slice the job for the chosen printer - a real slice, in an isolated directory,
    never the job's production gcode path - and mark it complete without ever
    printing it or sending anything to the printer. For work that was already done
    physically: printed before Themis tracked it, printed manually, or a job
    Themis's own tracking is stuck on but which really did finish. Deducts inventory
    filament on success. Fires no webhooks or notifications either way - this is an
    out-of-band admin action, not a real completion or a real failure as far as
    integrations are concerned."""
    job = await _get_or_404(job_id, session)
    if job.status in _MANUAL_COMPLETE_TERMINAL_STATUSES:
        raise HTTPException(409, f"Job in status {job.status!r} is already terminal")

    printer = await session.get(Printer, body.printer_id)
    if printer is None:
        raise HTTPException(404, f"Printer {body.printer_id} not found")

    cfg_result = await session.execute(
        select(JobPrinterConfig).where(
            JobPrinterConfig.job_id == job_id,
            JobPrinterConfig.printer_id == body.printer_id,
        )
    )
    config = cfg_result.scalar_one_or_none()
    if config is None:
        raise HTTPException(404, f"Job {job_id} has no config for printer {body.printer_id}")

    uploaded_file = await session.get(UploadedFile, job.uploaded_file_id)
    if uploaded_file is None:
        raise HTTPException(404, f"File {job.uploaded_file_id} not found")

    if is_presliced_file(uploaded_file):
        raise HTTPException(422, "This job prints a pre-sliced file; there is nothing to slice")

    resolution = await slicer_profiles.resolve_for_printer(session, printer)
    if not resolution.profile:
        raise HTTPException(422, resolution.reason or "Printer has no slicer machine profile")

    # No status/assigned_printer_id change before the slice: the job may genuinely be
    # printing/uploading, and clobbering either would orphan the live print from
    # _reconcile_printing_jobs. The in-flight set stops a double-click/second tab from
    # completing (and double-counting counters + inventory) the same job twice.
    if job_id in _jobs._manual_complete_in_flight:
        raise HTTPException(409, f"Job {job_id} is already being manually completed")
    _jobs._manual_complete_in_flight.add(job_id)

    req = _build_slice_request(job, config, printer, uploaded_file, resolution.profile)
    output_dir = _jobs.queue_engine._slicer._data_dir / "gcode_manual_complete" / str(job_id)
    try:
        gcode_path = await _jobs.queue_engine.run_verify_slice(req, output_dir)
        # Parse before the `finally` cleanup below removes output_dir - the gcode
        # file must be read while it still exists.
        est = _jobs.get_format_provider().parse_estimates(gcode_path)
        grams, secs, extruder_grams = est.grams, est.seconds, est.per_extruder_grams
    except SliceError as exc:
        raise HTTPException(422, f"Slicing failed: {exc}")
    except Exception as exc:
        logger.exception("Unexpected error in complete-manually slice for job %s", job_id)
        raise HTTPException(422, f"Slicing failed: Unexpected error: {exc}")
    finally:
        _jobs._manual_complete_in_flight.discard(job_id)
        shutil.rmtree(output_dir, ignore_errors=True)

    # Re-check after the (slow) slice: the job may have been cancelled or completed
    # by the queue engine / a real print finishing while we were slicing.
    await session.refresh(job)
    if job.status in _MANUAL_COMPLETE_TERMINAL_STATUSES:
        raise HTTPException(409, f"Job in status {job.status!r} is already terminal")
    job.assigned_printer_id = body.printer_id
    job.printed_on_printer_id = body.printer_id
    job.block_reason = None
    job.unmatched_filaments = None

    job.actual_filament_grams = grams
    job.actual_seconds = secs
    if extruder_grams is not None:
        job.actual_filament_breakdown = [
            {
                "extruder_index": i,
                "filament_profile": req.filament_presets[i] if i < len(req.filament_presets) else None,
                "grams": g,
            }
            for i, g in enumerate(extruder_grams)
        ]
    job.status = "complete"
    job.completed_at = datetime.now(timezone.utc).isoformat()
    job.outcome = None
    job.updated_at = datetime.now(timezone.utc).isoformat()

    printer.awaiting_plate_clear = True
    _jobs.printer_manager.set_awaiting_plate_clear(body.printer_id, True)

    # Wear counters and the spool deduction run from the durable `job.complete` event written in this transaction.
    await completion_events.enqueue_job_complete(session, job, body.printer_id, source="manual",
                                                 loaded_filaments=printer.loaded_filaments)

    await session.commit()
    await session.refresh(job)
    event_hub.wake()

    return _to_dict(job)


@router.get(
    "/{job_id}/slice-failures",
    summary="Get slice failures",
    responses={
        404: {"description": "Job not found"},
    },
    dependencies=[Depends(require_scope("jobs:read"))],
)
async def get_slice_failures(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Printer configs that failed slicing, with the error message for each."""
    await _get_or_404(job_id, session)
    result = await session.execute(
        select(JobPrinterConfig).where(
            JobPrinterConfig.job_id == job_id,
            JobPrinterConfig.slice_failed == True,  # noqa: E712
        )
    )
    return [
        {
            "printer_id": c.printer_id,
            "print_profile": c.print_profile,
            "filament_profile": c.filament_profile,
            "slice_error": c.slice_error,
        }
        for c in result.scalars().all()
    ]


class JobSchedulePatch(BaseModel):
    not_before: str | None = None

    @field_validator("not_before")
    @classmethod
    def _valid_not_before(cls, v: str | None) -> str | None:
        return _normalise_not_before(v)


@router.patch(
    "/{job_id}/schedule",
    summary="Set or clear a job's earliest start time",
    responses={
        404: {"description": "Job not found"},
        409: {"description": "Job already started or finished"},
    },
    dependencies=[Depends(require_scope("jobs:write"))],
)
async def set_job_schedule(
    job_id: int,
    body: JobSchedulePatch,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Hold a queued/blocked job until `not_before` (UTC ISO; null clears it and releases the job now)."""
    job = await _get_or_404(job_id, session)
    if job.status not in ("queued", "blocked"):
        raise HTTPException(409, f"Job is {job.status}; only queued or blocked jobs can be rescheduled")
    job.not_before = body.not_before
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    await session.refresh(job)
    _jobs.queue_engine.wake()
    return _to_dict(job)


def _clean_save_name(name: str | None) -> str | None:
    return (name or "").strip() or None


class JobSaveSlicePatch(BaseModel):
    save_slice: bool
    name: str | None = Field(default=None, max_length=200)


_TERMINAL_STATUSES = ("complete", "failed", "cancelled")


class JobProjectLink(BaseModel):
    project_id: int | None = None


@router.patch(
    "/{job_id}/project",
    summary="Link or unlink a job's project",
    responses={
        404: {"description": "Job or project not found"},
        409: {"description": "The job already belongs to another project, or the project is still a draft"},
    },
    dependencies=[Depends(require_scope("jobs:write"))],
)
async def set_job_project(
    job_id: int,
    body: JobProjectLink,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Attach an existing job to a project (so it counts toward the project's progress and costs), or detach it
    with `project_id: null`. A job moves between projects only by being unlinked first. A project that has an
    order grouping replaces the job's own `order_id`; unlinking clears `order_id` only when it is that project's. While the project is not
    yet queued, the queue engine holds its queued/blocked jobs back (already sliced or running ones are unaffected)."""
    job = await _get_or_404(job_id, session)
    if body.project_id is None:
        current = await session.get(Project, job.project_id) if job.project_id is not None else None
        if current is not None and job.order_id is not None and job.order_id == current.order_id:
            job.order_id = None
        job.project_id = None
    else:
        project = await _linkable_project(session, body.project_id)
        if job.project_id is not None and job.project_id != project.id:
            raise HTTPException(409, f"Job {job_id} already belongs to project {job.project_id}; unlink it first")
        job.project_id = project.id
        if project.order_id is not None:       # the project's grouping wins; a project without one keeps the job's own
            job.order_id = project.order_id
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    await session.refresh(job)
    _jobs.queue_engine.wake()
    return _to_dict(job)


@router.patch(
    "/{job_id}/save-slice",
    summary="Save (or stop saving) a job's slice to the library",
    responses={
        404: {"description": "Job not found"},
        409: {"description": "Job already finished (its slice is gone)"},
        422: {"description": "Job prints a pre-sliced file or a cached version — there is no new slice to save"},
    },
    dependencies=[Depends(require_scope("jobs:write"))],
)
async def set_job_save_slice(
    job_id: int,
    body: JobSaveSlicePatch,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Flag any unfinished job to keep its production slice as a cached library version (BIZ-192), optionally under
    `name`. If the job is already sliced, its slice is saved right away; otherwise it is saved when slicing finishes.
    Re-sending `save_slice: true` after a failed save retries it. Turning the flag off cancels a pending save but never
    deletes a version already saved."""
    job = await _get_or_404(job_id, session)
    if job.status in _TERMINAL_STATUSES:
        raise HTTPException(409, f"Job is {job.status}; its slice is no longer available to save")
    if is_presliced_file(await session.get(UploadedFile, job.uploaded_file_id)):
        raise HTTPException(422, "This job prints a pre-sliced file; there is no new slice to save")
    if body.save_slice and job.sliced_version_id is not None:
        raise HTTPException(422, "This job prints a cached version; there is no new slice to save")
    last_save = (job.slice_cache_info or {}).get("save") or {}
    # Save now if this turns the flag on, or retries a save that failed; a flag that was already on is the engine's job.
    save_now = body.save_slice and (not job.save_slice or last_save.get("outcome") == "failed")
    job.save_slice = body.save_slice
    job.save_slice_name = _clean_save_name(body.name) if body.save_slice else None
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()

    gcode = (await session.execute(
        select(GcodeFile).where(GcodeFile.job_id == job_id).order_by(GcodeFile.id.desc()).limit(1)
    )).scalar_one_or_none() if save_now else None
    # Already sliced: save now (the engine only saves at slice time).
    if gcode is not None and os.path.exists(gcode.path):
        if gcode.slice_inputs:
            await slice_saver.save_slice_version(
                session, job_id=job_id, printer_id=gcode.printer_id, artifact_path=gcode.path,
                inputs=slice_saver.inputs_from_dict(gcode.slice_inputs))
        else:
            await slice_saver.save_slice_version(
                session, job_id=job_id, printer_id=gcode.printer_id, artifact_path=gcode.path, inputs=None,
                uncacheable_reason="this slice was made before its inputs were recorded; it can't be keyed")
    await session.refresh(job)
    return _to_dict(job)


class JobCostPatch(BaseModel):
    filament_cost: float | None = None

    @field_validator("filament_cost")
    @classmethod
    def _non_negative(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("filament_cost must not be negative")
        return v


@router.patch(
    "/{job_id}/cost",
    summary="Set job filament cost",
    responses={
        404: {"description": "Job not found"},
    },
    dependencies=[Depends(require_scope("jobs:write"))],
)
async def set_job_cost(
    job_id: int,
    body: JobCostPatch,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Record the cost of the filament used for this job, for profit/loss reporting."""
    job = await _get_or_404(job_id, session)
    job.filament_cost = body.filament_cost
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    await session.refresh(job)
    return _to_dict(job)


class OutcomeFailureItem(BaseModel):
    project_item_id: int
    quantity_failed: int


class OutcomeBody(BaseModel):
    failures: list[OutcomeFailureItem] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.put(
    "/{job_id}/outcome",
    summary="Mark job outcome",
    responses={
        400: {"description": "Job has no project items to mark"},
        404: {"description": "Job not found"},
    },
    dependencies=[Depends(require_scope("jobs:write"))],
)
async def mark_job_outcome(
    job_id: int,
    body: OutcomeBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Record per-item pass/fail counts for a completed project job.
    Increments `quantity_completed` and `quantity_failed` on each ProjectItem.
    Calling again replaces the previous outcome (idempotent re-review)."""
    job = await _get_or_404(job_id, session)

    if job.project_item_quantities is None:
        raise HTTPException(400, "This job has no project items to mark")

    plate_quantities: dict[str, int] = json.loads(job.project_item_quantities)
    reported = (await session.execute(select(JobItemFailure.id).where(
        JobItemFailure.job_id == job_id, JobItemFailure.resolution.is_not(None)).limit(1))).first()
    if reported:      # BIZ-364: results reported at plate clear already requeued / held its failed parts
        raise HTTPException(409, "This job's print results were reported when its plate was cleared")

    # Reverse previous increments
    old_jifs = (await session.execute(
        select(JobItemFailure).where(JobItemFailure.job_id == job_id)
    )).scalars().all()

    for jif in old_jifs:
        pi = await session.get(ProjectItem, jif.project_item_id)
        if pi is not None:
            pi.quantity_failed = max(0, pi.quantity_failed - jif.quantity_failed)
            pi.quantity_completed = max(0, pi.quantity_completed - (jif.quantity_on_plate - jif.quantity_failed))

    # Delete existing failure records
    await session.execute(delete(JobItemFailure).where(JobItemFailure.job_id == job_id))

    # Parse new failures
    failures_in: dict[int, int] = {
        f.project_item_id: f.quantity_failed
        for f in body.failures
    }

    # Apply new increments
    new_failures: list[dict] = []
    for k, qty_on_plate in plate_quantities.items():
        item_id = int(k)
        qty_failed = max(0, min(failures_in.get(item_id, 0), qty_on_plate))
        qty_succeeded = qty_on_plate - qty_failed

        if qty_on_plate > 0:
            jif = JobItemFailure(
                job_id=job.id,
                project_item_id=item_id,
                quantity_failed=qty_failed,
                quantity_on_plate=qty_on_plate,
            )
            session.add(jif)

        pi = await session.get(ProjectItem, item_id)
        if pi is not None:
            pi.quantity_failed += qty_failed
            pi.quantity_completed += qty_succeeded

        new_failures.append({"project_item_id": item_id, "quantity_failed": qty_failed})

    job.outcome = "reviewed"
    job.updated_at = _now()
    await session.commit()
    await session.refresh(job)

    return {**_to_dict(job), "failures": new_failures}
