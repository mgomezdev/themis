from __future__ import annotations
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import GcodeFile, Job, JobItemFailure, JobPrinterConfig, Order, Printer, Project, ProjectItem, QueueConfig, UploadedFile
from ...services.mesh_3mf_builder import source_has_project_settings
from ...services.override_inspector import inspect_overrides, CURATED_KEYS
from ...services.printer_manager import printer_manager
from ...services.queue_engine import queue_engine, _slot_for_config
from ...services.slicer_service import SliceError, SliceRequest

logger = logging.getLogger(__name__)

_CURATED_KEYS_SET: frozenset[str] = frozenset(CURATED_KEYS)


def _clean_overrides(o: dict | None) -> dict | None:
    """Strip any key not in the curated allowlist before storing."""
    if not o:
        return None
    cleaned = {k: str(v) for k, v in o.items() if k in _CURATED_KEYS_SET}
    return cleaned or None


# Statuses where a printer is physically working on the job and must be told to stop.
_PRINTER_ACTIVE_STATUSES = {"printing", "paused", "uploading"}

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

_CANCELLABLE_STATUSES = {"queued", "blocked", "slicing", "sliced", "uploading", "printing", "paused", "failed"}


class PrinterConfigInput(BaseModel):
    printer_id: int
    print_profile: str
    filament_profile: str | None = None
    filament_id: int | None = None
    filament_type: str | None = None
    filament_color: str | None = None
    tool_index: int | None = None
    filament_map: list | None = None


class OverrideCheckRequest(BaseModel):
    uploaded_file_id: int
    printer_id: int
    print_profile: str
    filament_profile: str | None = None
    filament_color: str | None = None


class JobCreate(BaseModel):
    uploaded_file_id: int
    plate_number: int = 1
    order_id: int | None = None
    printer_configs: list[PrinterConfigInput]
    overrides: dict | None = None


def _to_dict(j: Job) -> dict:
    return {
        "id": j.id,
        "uploaded_file_id": j.uploaded_file_id,
        "plate_number": j.plate_number,
        "order_id": j.order_id,
        "project_id": j.project_id,
        "assigned_printer_id": j.assigned_printer_id,
        "queue_position": j.queue_position,
        "status": j.status,
        "overrides": j.overrides,
        "outcome": j.outcome,
        "project_item_quantities": json.loads(j.project_item_quantities) if j.project_item_quantities else None,
        "created_at": j.created_at,
        "updated_at": j.updated_at,
        "completed_at": j.completed_at,
        # Actual values (populated at production slice time)
        "actual_filament_grams": j.actual_filament_grams,
        "actual_seconds": j.actual_seconds,
        "actual_filament_breakdown": j.actual_filament_breakdown,
        "deduction_skipped": j.deduction_skipped,
        # Estimate values (populated after background test slice)
        "estimate_status": j.estimate_status,
        "estimate_seconds": j.estimate_seconds,
        "estimate_filament_grams": j.estimate_filament_grams,
        "estimate_filament_breakdown": j.estimate_filament_breakdown,
        "estimate_preset_label": j.estimate_preset_label,
    }


async def _get_or_404(job_id: int, session: AsyncSession) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, f"Job {job_id} not found")
    return job


async def _next_queue_position(session: AsyncSession) -> float:
    result = await session.execute(
        select(func.max(Job.queue_position)).where(
            Job.status.not_in(["complete", "failed", "cancelled"])
        )
    )
    current_max = result.scalar()
    return (current_max or 0.0) + 1.0


async def _front_queue_position(session: AsyncSession) -> float:
    """Position just ahead of the current queue front (for re-queueing at the top)."""
    result = await session.execute(
        select(func.min(Job.queue_position)).where(
            Job.status.in_(["queued", "blocked"])
        )
    )
    current_min = result.scalar()
    return (current_min or 1.0) - 1.0


@router.post(
    "",
    status_code=201,
    summary="Create job",
    responses={
        404: {"description": "File, printer, or order not found"},
        422: {"description": "printer_configs is empty"},
    },
)
async def create_job(
    body: JobCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue a new print job for a specific file plate. At least one printer config is required.
    The job is added at the end of the queue and the engine is woken immediately."""
    # Validate file exists
    uploaded_file = await session.get(UploadedFile, body.uploaded_file_id)
    if uploaded_file is None:
        raise HTTPException(404, f"File {body.uploaded_file_id} not found")

    if not body.printer_configs:
        raise HTTPException(422, "printer_configs must not be empty")

    for cfg in body.printer_configs:
        printer = await session.get(Printer, cfg.printer_id)
        if printer is None:
            raise HTTPException(404, f"Printer {cfg.printer_id} not found")

    if body.order_id is not None:
        order = await session.get(Order, body.order_id)
        if order is None:
            raise HTTPException(404, f"Order {body.order_id} not found")

    now = datetime.now(timezone.utc).isoformat()
    pos = await _next_queue_position(session)

    job = Job(
        uploaded_file_id=body.uploaded_file_id,
        plate_number=body.plate_number,
        order_id=body.order_id,
        overrides=_clean_overrides(body.overrides),
        queue_position=pos,
        status="queued",
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    await session.flush()

    for cfg in body.printer_configs:
        config = JobPrinterConfig(
            job_id=job.id,
            printer_id=cfg.printer_id,
            print_profile=cfg.print_profile,
            filament_profile=cfg.filament_profile,
            filament_id=cfg.filament_id,
            filament_type=cfg.filament_type,
            filament_color=cfg.filament_color,
            tool_index=cfg.tool_index,
            filament_map=cfg.filament_map,
        )
        session.add(config)

    await session.commit()
    await session.refresh(job)

    queue_engine.wake()

    # Trigger background estimate if enabled
    queue_cfg = await session.get(QueueConfig, 1)
    estimates_enabled = queue_cfg is not None and queue_cfg.estimates_enabled
    if estimates_enabled:
        job.estimate_token = (job.estimate_token or 0) + 1
        job.estimate_status = "pending"
        await session.commit()
        queue_engine.spawn_estimate(job.id)

    return _to_dict(job)


@router.post(
    "/check-overrides",
    summary="Check embedded settings vs presets",
    responses={
        404: {"description": "File or printer not found"},
    },
)
async def check_overrides(
    body: OverrideCheckRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Flag settings baked into the uploaded 3MF that the chosen presets would
    change, so the New Job flow can warn before slicing replaces them."""
    uploaded_file = await session.get(UploadedFile, body.uploaded_file_id)
    if uploaded_file is None:
        raise HTTPException(404, f"File {body.uploaded_file_id} not found")
    printer = await session.get(Printer, body.printer_id)
    if printer is None:
        raise HTTPException(404, f"Printer {body.printer_id} not found")

    empty = {"has_findings": False, "setting_changes": [], "slot_warning": None}
    # Bare/geometry-only uploads carry no settings to lose.
    if not source_has_project_settings(uploaded_file.stored_path):
        return {**empty, "has_embedded_settings": False}
    if not printer.current_orca_printer_profile:
        return {**empty, "has_embedded_settings": True}

    # Resolve profile names to UUIDs via the Orca sidecar, then fetch the merged
    # project config. If the sidecar is unavailable or any required UUID is missing,
    # skip the diff rather than blocking job creation.
    from ...config import get_laminus_sidecar_url
    from ...services.laminus_sidecar_client import LaminusSidecarClient, SidecarError

    sidecar_url = get_laminus_sidecar_url()
    if not sidecar_url:
        return {**empty, "has_embedded_settings": True, "error": "Override check requires Laminus sidecar"}

    from ..routes.laminus import get_cached_catalog
    try:
        catalog = await get_cached_catalog()
    except Exception as e:
        return {**empty, "has_embedded_settings": True, "error": f"Catalog unavailable: {e}"}

    machine_name = printer.current_orca_printer_profile
    machine_map = {m["name"]: m["uuid"] for m in catalog.get("machine", [])}
    process_map = {p["name"]: p["uuid"] for p in catalog.get("process", [])}
    filament_map = {f["name"]: f["uuid"] for f in catalog.get("filament", [])}

    machine_uuid = machine_map.get(machine_name)
    process_uuid = process_map.get(body.print_profile)
    if not machine_uuid or not process_uuid:
        return {**empty, "has_embedded_settings": True,
                "error": f"Profile not found in sidecar: machine={machine_name!r} process={body.print_profile!r}"}

    # Filament content doesn't affect the curated (process) diff. If the named
    # filament isn't in the catalog, pick the first compatible one as a stand-in.
    filament_uuid = filament_map.get(body.filament_profile or "")
    if not filament_uuid:
        compat = [f["uuid"] for f in catalog.get("filament", [])
                  if machine_name in (f.get("compatible_printers") or [])]
        filament_uuid = compat[0] if compat else next(iter(filament_map.values()), None)
    if not filament_uuid:
        return {**empty, "has_embedded_settings": True, "error": "No filament profiles found in sidecar catalog"}

    try:
        client = LaminusSidecarClient(sidecar_url)
        config = await asyncio.get_running_loop().run_in_executor(
            None, client.get_merged_config, machine_uuid, process_uuid, [filament_uuid]
        )
    except SidecarError as e:
        return {**empty, "has_embedded_settings": True, "error": str(e)}

    slots = len(printer.loaded_filaments or []) or 1
    return inspect_overrides(uploaded_file.stored_path, config, slots)


@router.get("", summary="List active jobs")
async def list_jobs(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """All jobs ordered by queue position. Includes jobs in all statuses."""
    result = await session.execute(select(Job).order_by(Job.queue_position))
    return [_to_dict(j) for j in result.scalars().all()]


@router.get("/history", summary="List job history")
async def list_history(
    status: str = "complete,cancelled,failed",
    project_id: Optional[int] = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Completed/cancelled/failed jobs enriched with file name, printer name, and project name.
    `status` is a comma-separated list; `project_id` filters to a single project."""
    statuses = [s.strip() for s in status.split(",") if s.strip()]
    q = select(Job).where(Job.status.in_(statuses))
    if project_id is not None:
        q = q.where(Job.project_id == project_id)
    q = q.order_by(Job.updated_at.desc()).limit(limit)
    jobs = (await session.execute(q)).scalars().all()

    out = []
    for j in jobs:
        d = _to_dict(j)
        # Enrich with file name
        f = await session.get(UploadedFile, j.uploaded_file_id)
        d["file_name"] = f.original_filename if f else None
        # Enrich with assigned printer name
        p = await session.get(Printer, j.assigned_printer_id) if j.assigned_printer_id else None
        d["printer_name"] = p.name if p else None
        # Enrich with project name
        proj = await session.get(Project, j.project_id) if j.project_id else None
        d["project_name"] = proj.name if proj else None
        out.append(d)
    return out


@router.get(
    "/{job_id}",
    summary="Get job",
    responses={
        404: {"description": "Job not found"},
    },
)
async def get_job(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return _to_dict(await _get_or_404(job_id, session))


@router.get(
    "/{job_id}/details",
    summary="Get job details",
    responses={
        404: {"description": "Job not found"},
    },
)
async def get_job_details(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Full job detail: file name, plate stats, per-printer slicing configs, assigned printer."""
    job = await _get_or_404(job_id, session)

    # File + plate metadata
    file_info = None
    plate_info = None
    uploaded_file = await session.get(UploadedFile, job.uploaded_file_id)
    if uploaded_file:
        file_info = {"id": uploaded_file.id, "original_filename": uploaded_file.original_filename}
        plate = next(
            (p for p in (uploaded_file.plates or []) if p.get("plate_number") == job.plate_number),
            None,
        )
        if plate:
            plate_info = {
                "estimated_time": plate.get("estimated_time"),
                "filament_g": plate.get("filament_g"),
                "thumbnail_path": plate.get("thumbnail_path"),
            }

    # Per-printer slicing configs
    result = await session.execute(
        select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id)
    )
    printer_configs = []
    for cfg in result.scalars().all():
        p = await session.get(Printer, cfg.printer_id)
        printer_configs.append({
            "printer_id": cfg.printer_id,
            "printer_name": p.name if p else f"Printer {cfg.printer_id}",
            "printer_type": p.printer_type if p else "unknown",
            "print_profile": cfg.print_profile,
            "filament_profile": cfg.filament_profile,
            "filament_id": cfg.filament_id,
            "filament_type": cfg.filament_type,
            "filament_color": cfg.filament_color,
            "tool_index": cfg.tool_index,
            "filament_map": cfg.filament_map,
            "slice_failed": cfg.slice_failed,
            "slice_error": cfg.slice_error,
        })

    # Assigned printer (if claimed)
    assigned_printer = None
    if job.assigned_printer_id:
        p = await session.get(Printer, job.assigned_printer_id)
        if p:
            assigned_printer = {"id": p.id, "name": p.name, "printer_type": p.printer_type}

    gcode_result = await session.execute(
        select(GcodeFile).where(GcodeFile.job_id == job_id).limit(1)
    )
    gcode_rec = gcode_result.scalar_one_or_none()

    return {
        **_to_dict(job),
        "block_reason": job.block_reason,
        "file": file_info,
        "plate": plate_info,
        "printer_configs": printer_configs,
        "assigned_printer": assigned_printer,
        "filament_grams_live": gcode_rec.filament_grams if gcode_rec else None,
        "estimated_seconds_live": gcode_rec.estimated_seconds if gcode_rec else None,
    }


@router.post(
    "/{job_id}/cancel",
    summary="Cancel job",
    responses={
        404: {"description": "Job not found"},
        422: {"description": "Job is in a non-cancellable status"},
    },
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
    job.status = "cancelled"
    job.completed_at = datetime.now(timezone.utc).isoformat()
    job.assigned_printer_id = None
    job.queue_position = None
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    await session.refresh(job)

    if stop_printer_id is not None:
        client = printer_manager._clients.get(stop_printer_id)
        if client is not None and client.connected:
            # stop_print blocks on the websocket ack; don't stall the event loop.
            try:
                await asyncio.to_thread(client.stop_print)
            except Exception:  # best-effort — the job is already cancelled
                pass
        queue_engine.wake()  # let the freed printer claim the next job once idle

    return _to_dict(job)


class JobConfigsUpdate(BaseModel):
    printer_configs: list[PrinterConfigInput]
    overrides: dict | None = None


@router.patch(
    "/{job_id}/configs",
    summary="Update job configs",
    responses={
        404: {"description": "Job or printer not found"},
        422: {"description": "Job is not in an editable status or printer_configs is empty"},
    },
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
    if not body.printer_configs:
        raise HTTPException(422, "printer_configs must not be empty")
    for cfg in body.printer_configs:
        if await session.get(Printer, cfg.printer_id) is None:
            raise HTTPException(404, f"Printer {cfg.printer_id} not found")

    existing = await session.execute(
        select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id)
    )
    for row in existing.scalars().all():
        await session.delete(row)

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
            # Mirror the New Job convention: manual filaments store the type as the
            # profile name. Never null — legacy DBs have a NOT NULL constraint here.
            filament_profile=cfg.filament_profile or cfg.filament_type or "",
            filament_id=cfg.filament_id,
            filament_type=cfg.filament_type,
            filament_color=cfg.filament_color,
            tool_index=cfg.tool_index,
            filament_map=cfg.filament_map,
            slice_failed=False,
            slice_error=None,
        ))

    job.status = "queued"
    job.block_reason = None
    job.assigned_printer_id = None
    job.overrides = _clean_overrides(body.overrides)
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    await session.refresh(job)
    queue_engine.wake()

    # Re-trigger background estimate if enabled
    queue_cfg = await session.get(QueueConfig, 1)
    estimates_enabled = queue_cfg is not None and queue_cfg.estimates_enabled
    if estimates_enabled:
        job.estimate_token = (job.estimate_token or 0) + 1
        job.estimate_status = "pending"
        await session.commit()
        queue_engine.spawn_estimate(job.id)

    return _to_dict(job)


@router.post(
    "/{job_id}/unblock",
    summary="Unblock job",
    responses={
        404: {"description": "Job not found"},
        422: {"description": "Job is not in blocked status"},
    },
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
    job.assigned_printer_id = None
    job.queue_position = await _front_queue_position(session)
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    await session.refresh(job)
    queue_engine.wake()
    return _to_dict(job)


class VerifySliceBody(BaseModel):
    printer_id: int


@router.post(
    "/{job_id}/verify-slice",
    summary="Test-slice job",
    responses={
        404: {"description": "Job, printer, file, or printer config not found"},
    },
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

    if not printer.current_orca_printer_profile:
        return {"ok": False, "error": "Printer has no OrcaSlicer machine preset configured"}

    # Mirror _run_slice_and_print: resolve the filament slot and build the SliceRequest.
    loaded = printer.loaded_filaments or []
    slot = _slot_for_config(config, loaded)
    filament_profile = config.filament_profile or (slot or {}).get("filament_profile") or None

    stem = os.path.splitext(os.path.basename(uploaded_file.original_filename or "model"))[0]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "model"
    file_base = f"{safe}_p{job.plate_number}_j{job_id}"

    client = printer_manager._clients.get(body.printer_id)
    export_args = client.orca_export_args(file_base) if client else []

    cfg_tool_index = config.tool_index
    cfg_filament_map = config.filament_map
    prepare_hook = None
    if client is not None and (cfg_tool_index is not None or cfg_filament_map):
        prepare_hook = (
            lambda p, c=client, ti=cfg_tool_index, fm=cfg_filament_map:
            c.remap_sliceable_3mf(p, tool_index=ti, filament_map=fm)
        )

    if cfg_filament_map:
        ordered = sorted(loaded, key=lambda s: s.get("slot", 0))
        filament_presets = [s.get("filament_profile") for s in ordered if s.get("filament_profile")]
    else:
        filament_presets = [filament_profile] if filament_profile else []

    plate_config = {"curr_bed_type": printer.build_plate_type} if printer.build_plate_type else {}
    plate_config.update(job.overrides or {})
    req = SliceRequest(
        job_id=job_id,
        source_3mf=uploaded_file.stored_path,
        plate_number=job.plate_number,
        machine_preset=printer.current_orca_printer_profile,
        process_preset=config.print_profile,
        filament_presets=filament_presets,
        filament_colours=[config.filament_color] if config.filament_color else [],
        export_args=export_args,
        prepare_hook=prepare_hook,
        extra_config=plate_config,
    )

    loop = asyncio.get_running_loop()
    try:
        gcode_path: str = await loop.run_in_executor(
            queue_engine._executor, queue_engine._slicer.slice, req
        )
        try:
            Path(gcode_path).unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": True, "error": None}
    except SliceError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected error in verify-slice for job %s", job_id)
        return {"ok": False, "error": f"Unexpected error: {exc}"}


@router.get(
    "/{job_id}/slice-failures",
    summary="Get slice failures",
    responses={
        404: {"description": "Job not found"},
    },
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
