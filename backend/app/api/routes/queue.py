from __future__ import annotations
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import Job, JobPrinterConfig, Printer, SpoolmanConfig, UploadedFile
from ...services.queue_engine import _slot_for_config
from ...services.spool_check import check_spool_sufficiency
from ...services.spoolman_service import fetch_spools

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/queue", tags=["queue"])

_ACTIVE_STATUSES = {"queued", "slicing", "sliced", "uploading", "printing", "paused", "blocked", "failed"}


class PositionUpdate(BaseModel):
    job_id: int
    queue_position: float


class ReorderRequest(BaseModel):
    positions: list[PositionUpdate]


def _base_dict(j: Job) -> dict:
    return {
        "id": j.id,
        "uploaded_file_id": j.uploaded_file_id,
        "plate_number": j.plate_number,
        "order_id": j.order_id,
        "assigned_printer_id": j.assigned_printer_id,
        "queue_position": j.queue_position,
        "status": j.status,
        "block_reason": j.block_reason,
        "created_at": j.created_at,
        "updated_at": j.updated_at,
    }


async def _needed_grams(j: Job, session: AsyncSession) -> float | None:
    """Grams needed for the spool preflight check: the background test-slice
    estimate if we have one, else the 3MF-parsed plate estimate."""
    needed_g = j.estimate_filament_grams
    if needed_g is not None:
        return needed_g
    uploaded_file = await session.get(UploadedFile, j.uploaded_file_id)
    if uploaded_file is None:
        return None
    plate = next(
        (p for p in (uploaded_file.plates or []) if p.get("plate_number") == j.plate_number),
        None,
    )
    return plate.get("filament_g") if plate else None


async def _enrich(j: Job, session: AsyncSession, spools_by_id: dict[str, dict]) -> dict:
    d = _base_dict(j)
    cfg_result = await session.execute(
        select(JobPrinterConfig).where(JobPrinterConfig.job_id == j.id)
    )
    configs = cfg_result.scalars().all()
    d["materials"] = sorted({c.filament_type for c in configs if c.filament_type})

    needed_g = await _needed_grams(j, session)
    eligible = []
    low_stock_warning = None
    for c in configs:
        p = await session.get(Printer, c.printer_id)
        if not p:
            continue
        eligible.append({"id": p.id, "name": p.name})
        if low_stock_warning is None:
            slot = _slot_for_config(c, p.loaded_filaments or [])
            if slot and slot.get("spoolman_spool_id") is not None:
                spool = spools_by_id.get(str(slot["spoolman_spool_id"]))
                if spool is not None:
                    low_stock_warning = check_spool_sufficiency(needed_g, spool)
    d["eligible_printers"] = eligible
    d["low_stock_warning"] = low_stock_warning
    return d


async def _active_jobs_enriched(session: AsyncSession) -> list[dict]:
    """Fetch all active-status jobs and enrich each with materials, eligible
    printers, and a low_stock_warning — batching the Spoolman lookup into a
    single fetch_spools() call across every job/config in the list rather than
    one per job (this endpoint is polled frequently by the frontend)."""
    result = await session.execute(
        select(Job)
        .where(Job.status.in_(list(_ACTIVE_STATUSES)))
        .order_by(Job.queue_position.asc())
    )
    jobs = result.scalars().all()

    # First pass: resolve each config's loaded-filament slot so we know whether
    # a Spoolman lookup is needed at all, and collect the distinct spool ids.
    spool_ids_needed: set[str] = set()
    for j in jobs:
        cfg_result = await session.execute(
            select(JobPrinterConfig).where(JobPrinterConfig.job_id == j.id)
        )
        for c in cfg_result.scalars().all():
            p = await session.get(Printer, c.printer_id)
            if not p:
                continue
            slot = _slot_for_config(c, p.loaded_filaments or [])
            if slot and slot.get("spoolman_spool_id") is not None:
                spool_ids_needed.add(str(slot["spoolman_spool_id"]))

    spools_by_id: dict[str, dict] = {}
    if spool_ids_needed:
        spoolman_cfg = await session.get(SpoolmanConfig, 1)
        if spoolman_cfg and spoolman_cfg.enabled and spoolman_cfg.url:
            try:
                spools = await fetch_spools(spoolman_cfg.url, spoolman_cfg.api_key)
                spools_by_id = {str(s.get("id")): s for s in spools}
            except Exception:
                logger.warning("Spoolman unreachable while checking spool sufficiency for queue", exc_info=True)

    # Second pass: build the enriched dicts using the precomputed spool lookup.
    return [await _enrich(j, session, spools_by_id) for j in jobs]


@router.get("", summary="Get active queue", dependencies=[Depends(require_scope("queue:read"))])
async def get_queue(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """All jobs in an active status (queued, slicing, uploading, printing, paused, blocked, failed)
    ordered by queue position ascending."""
    return await _active_jobs_enriched(session)


@router.patch(
    "/reorder",
    summary="Reorder queue",
    responses={
        404: {"description": "Job not found"},
        422: {"description": "Job is not in an active status and cannot be reordered"},
    },
    dependencies=[Depends(require_scope("queue:write"))],
)
async def reorder_queue(
    body: ReorderRequest,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Set explicit queue positions for one or more active jobs. Returns the full updated queue."""
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
    return await _active_jobs_enriched(session)
