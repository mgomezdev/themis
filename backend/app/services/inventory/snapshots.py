"""Print-start spool snapshots: the spool's weight when a job started printing, the base for the absolute
`pre - spent` write at completion. Taken in a host task AFTER the "printing" commit — never on the queue loop.

Source order: the newest pending outbox target (the provider may not have our last write yet) -> the provider's live
reading -> the last-known cache (none yet; BIZ-219) -> `missing` (no starting weight: tracking suspends at completion)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...models import InventoryPendingWrite, JobSpoolSnapshot
from . import cache, provider

logger = logging.getLogger("app")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def newest_pending_target(session: AsyncSession, provider_id: str, spool_ref: str) -> float | None:
    row = (await session.execute(
        select(InventoryPendingWrite.target_g).where(
            InventoryPendingWrite.provider == provider_id, InventoryPendingWrite.spool_ref == spool_ref,
            InventoryPendingWrite.status == "pending").order_by(InventoryPendingWrite.id.desc()).limit(1))).first()
    return float(row[0]) if row else None


async def read_pre_weight(factory: async_sessionmaker[AsyncSession], provider_id: str,
                          spool_ref: str) -> tuple[float | None, str]:
    """(grams, source). The pending-target lookup uses its own short session, closed before the provider call (never
    held across provider I/O). The provider call is contained (never raises)."""
    async with factory() as session:
        pending = await newest_pending_target(session, provider_id, spool_ref)
    if pending is not None:
        return pending, "pending"
    read = await provider.call("get_spool", spool_ref)
    spool = read.value if read.ok else None
    if spool is not None and spool.remaining_g is not None:
        return float(spool.remaining_g), "live"
    if not read.ok:                                      # unreachable (not "unknown spool"): fall back to the last-known weight
        cached = await cache.cached_weight(provider_id, spool_ref)
        if cached is not None:
            return cached, "cached"
    return None, "missing"


async def take(factory: async_sessionmaker[AsyncSession], job_id: int, printer_id: int | None, spool_ref: str) -> None:
    """Record the snapshot for (job, active provider, spool). Idempotent (unique per job/provider/spool)."""
    pid = provider.provider_id()
    if pid is None:
        return
    async with factory() as session:
        if await get(session, job_id, pid, spool_ref) is not None:
            return
    pre, source = await read_pre_weight(factory, pid, spool_ref)           # no session open during provider I/O
    async with factory() as session:
        if await get(session, job_id, pid, spool_ref) is not None:         # a concurrent take won
            return
        session.add(JobSpoolSnapshot(job_id=job_id, printer_id=printer_id, provider=pid, spool_ref=spool_ref,
                                     pre_weight_g=pre, source=source, taken_at=_now()))
        try:
            await session.commit()
        except IntegrityError:
            logger.info("Spool snapshot for job %s spool %s not stored (job gone or already taken)", job_id, spool_ref)


async def get(session: AsyncSession, job_id: int, provider_id: str, spool_ref: str) -> JobSpoolSnapshot | None:
    return (await session.execute(select(JobSpoolSnapshot).where(
        JobSpoolSnapshot.job_id == job_id, JobSpoolSnapshot.provider == provider_id,
        JobSpoolSnapshot.spool_ref == spool_ref))).scalar_one_or_none()
