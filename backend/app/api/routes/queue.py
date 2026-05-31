from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Job

router = APIRouter(prefix="/api/v1/queue", tags=["queue"])

_ACTIVE_STATUSES = {"queued", "slicing", "uploading", "printing", "paused", "blocked", "failed"}


class PositionUpdate(BaseModel):
    job_id: int
    queue_position: float


class ReorderRequest(BaseModel):
    positions: list[PositionUpdate]


def _to_dict(j: Job) -> dict:
    return {
        "id": j.id,
        "uploaded_file_id": j.uploaded_file_id,
        "plate_number": j.plate_number,
        "project_id": j.project_id,
        "assigned_printer_id": j.assigned_printer_id,
        "queue_position": j.queue_position,
        "status": j.status,
        "block_reason": j.block_reason,
        "created_at": j.created_at,
        "updated_at": j.updated_at,
    }


@router.get("")
async def get_queue(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(
        select(Job)
        .where(Job.status.in_(list(_ACTIVE_STATUSES)))
        .order_by(Job.queue_position.asc())
    )
    return [_to_dict(j) for j in result.scalars().all()]


@router.patch("/reorder")
async def reorder_queue(
    body: ReorderRequest,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    for update in body.positions:
        job = await session.get(Job, update.job_id)
        if job is None:
            raise HTTPException(404, f"Job {update.job_id} not found")
        if job.status not in _ACTIVE_STATUSES:
            raise HTTPException(422, f"Job {update.job_id} has status {job.status!r} and cannot be reordered")
        job.queue_position = update.queue_position
        job.updated_at = now
    await session.commit()
    # Return updated queue
    result = await session.execute(
        select(Job)
        .where(Job.status.in_(list(_ACTIVE_STATUSES)))
        .order_by(Job.queue_position.asc())
    )
    return [_to_dict(j) for j in result.scalars().all()]
