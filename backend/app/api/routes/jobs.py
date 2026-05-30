from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Job, JobPrinterConfig, Printer, UploadedFile
from ...services.queue_engine import queue_engine

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

_CANCELLABLE_STATUSES = {"queued", "slicing", "uploading", "printing", "paused", "failed"}


class PrinterConfigInput(BaseModel):
    printer_id: int
    print_profile: str
    filament_profile: str | None = None
    filament_id: int | None = None
    filament_type: str | None = None
    filament_color: str | None = None


class JobCreate(BaseModel):
    uploaded_file_id: int
    plate_number: int = 1
    project_id: int | None = None
    printer_configs: list[PrinterConfigInput]


def _to_dict(j: Job) -> dict:
    return {
        "id": j.id,
        "uploaded_file_id": j.uploaded_file_id,
        "plate_number": j.plate_number,
        "project_id": j.project_id,
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

    now = datetime.now(timezone.utc).isoformat()
    pos = await _next_queue_position(session)

    job = Job(
        uploaded_file_id=body.uploaded_file_id,
        plate_number=body.plate_number,
        project_id=body.project_id,
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
        )
        session.add(config)

    await session.commit()
    await session.refresh(job)

    queue_engine.wake()

    return _to_dict(job)


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


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    job = await _get_or_404(job_id, session)
    if job.status not in _CANCELLABLE_STATUSES:
        raise HTTPException(422, f"Job in status {job.status!r} cannot be cancelled")
    job.status = "cancelled"
    job.queue_position = None
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    await session.refresh(job)
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
