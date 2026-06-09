from __future__ import annotations
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Job, JobPrinterConfig, Order, Printer, UploadedFile
from ...services.mesh_3mf_builder import source_has_project_settings
from ...services.override_inspector import inspect_overrides
from ...services.preset_resolver import PresetNotFoundError, PresetResolver
from ...services.printer_manager import printer_manager
from ...services.project_config_builder import build_project_config
from ...services.queue_engine import queue_engine

# Statuses where a printer is physically working on the job and must be told to stop.
_PRINTER_ACTIVE_STATUSES = {"printing", "paused", "uploading"}

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

_CANCELLABLE_STATUSES = {"queued", "slicing", "uploading", "printing", "paused", "failed"}


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


def _to_dict(j: Job) -> dict:
    return {
        "id": j.id,
        "uploaded_file_id": j.uploaded_file_id,
        "plate_number": j.plate_number,
        "order_id": j.order_id,
        "assigned_printer_id": j.assigned_printer_id,
        "queue_position": j.queue_position,
        "status": j.status,
        "created_at": j.created_at,
        "updated_at": j.updated_at,
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


@router.post("", status_code=201)
async def create_job(
    body: JobCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
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

    return _to_dict(job)


@router.post("/check-overrides")
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

    resolver = PresetResolver()
    try:
        machine = resolver.resolve(printer.current_orca_printer_profile, "machine")
        process = resolver.resolve(body.print_profile, "process")
    except PresetNotFoundError as e:
        # Can't compare without the chosen presets; don't block job creation.
        return {**empty, "has_embedded_settings": True, "error": str(e)}
    # Filament content doesn't affect the curated (process) diff; best-effort.
    try:
        filaments = [resolver.resolve(body.filament_profile, "filament")] if body.filament_profile else []
    except PresetNotFoundError:
        filaments = []
    if not filaments:
        filaments = [{"name": body.filament_profile or "filament", "filament_type": ["PLA"]}]

    config = build_project_config(machine, process, filaments,
                                  [body.filament_color] if body.filament_color else None)
    slots = len(printer.loaded_filaments or []) or 1
    return inspect_overrides(uploaded_file.stored_path, config, slots)


@router.get("")
async def list_jobs(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(select(Job).order_by(Job.queue_position))
    return [_to_dict(j) for j in result.scalars().all()]


@router.get("/{job_id}")
async def get_job(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return _to_dict(await _get_or_404(job_id, session))


@router.get("/{job_id}/details")
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

    return {
        **_to_dict(job),
        "block_reason": job.block_reason,
        "file": file_info,
        "plate": plate_info,
        "printer_configs": printer_configs,
        "assigned_printer": assigned_printer,
    }


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    job = await _get_or_404(job_id, session)
    if job.status not in _CANCELLABLE_STATUSES:
        raise HTTPException(422, f"Job in status {job.status!r} cannot be cancelled")

    # If a printer is physically running this job, tell it to stop and free it.
    stop_printer_id = (
        job.assigned_printer_id
        if job.status in _PRINTER_ACTIVE_STATUSES else None
    )
    job.status = "cancelled"
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


@router.patch("/{job_id}/configs")
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
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    await session.refresh(job)
    queue_engine.wake()
    return _to_dict(job)


@router.post("/{job_id}/unblock")
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


@router.get("/{job_id}/slice-failures")
async def get_slice_failures(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
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
