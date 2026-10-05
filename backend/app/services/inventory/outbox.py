"""The deduction outbox. A completion enqueues an ABSOLUTE `set remaining = target_g` row inside its own transaction; the
host flushes it to the provider (re-sending an absolute value is harmless, so a crash between send and mark is safe).

Per spool only the newest pending target is sent; older pending rows are marked `superseded`. Rows of a provider that is
no longer active are left alone (they apply if it is re-activated). No DB session is held across a provider call."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...models import InventoryPendingWrite
from . import cache, provider, tasks

logger = logging.getLogger("app")

_KEEP_DAYS = 30
_lock: asyncio.Lock | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def flush_lock() -> asyncio.Lock:
    """Held by a flush for its whole run, and by anything that changes pending rows or writes a weight by hand, so a
    send already in flight cannot land after (and overwrite) a user's correction or discard."""
    global _lock
    loop = asyncio.get_running_loop()
    if _lock is None or getattr(_lock, "_themis_loop", None) is not loop:
        _lock = asyncio.Lock()
        _lock._themis_loop = loop                                        # type: ignore[attr-defined]
    return _lock


def enqueue(session: AsyncSession, provider_id: str, spool_ref: str, target_g: float, *, job_id: int | None,
            printer_id: int | None, source: str) -> InventoryPendingWrite:
    """Add the row in the caller's transaction (no commit)."""
    row = InventoryPendingWrite(provider=provider_id, spool_ref=spool_ref, target_g=max(0.0, float(target_g)),
                                job_id=job_id, printer_id=printer_id, source=source, created_at=_now())
    session.add(row)
    return row


def flush_soon(factory: async_sessionmaker[AsyncSession]) -> None:
    tasks.spawn(flush(factory), name="inventory-outbox-flush")


async def has_pending(factory: async_sessionmaker[AsyncSession]) -> bool:
    async with factory() as session:
        return (await session.execute(select(InventoryPendingWrite.id).where(
            InventoryPendingWrite.status == "pending").limit(1))).first() is not None


async def flush(factory: async_sessionmaker[AsyncSession]) -> int:
    """Send the newest pending target per spool of the ACTIVE provider. Returns how many spools were applied."""
    pid = provider.provider_id()
    if pid is None:
        return 0
    async with flush_lock():
        async with factory() as session:
            rows = (await session.execute(select(InventoryPendingWrite).where(
                InventoryPendingWrite.provider == pid, InventoryPendingWrite.status == "pending")
                .order_by(InventoryPendingWrite.id))).scalars().all()
            by_spool: dict[str, list[tuple[int, float]]] = {}
            for r in rows:
                by_spool.setdefault(r.spool_ref, []).append((r.id, r.target_g))
        applied = 0
        for ref, items in by_spool.items():
            newest_id, target = items[-1]
            older = [i for i, _ in items[:-1]]
            result = await provider.call("set_remaining", ref, target)
            patch_cache = False
            async with factory() as session:
                if result.ok:
                    await session.execute(update(InventoryPendingWrite).where(
                        InventoryPendingWrite.id == newest_id, InventoryPendingWrite.status == "pending")
                        .values(status="applied", last_attempt_at=_now(), last_error=None))
                    if older:
                        await session.execute(update(InventoryPendingWrite).where(
                            InventoryPendingWrite.id.in_(older), InventoryPendingWrite.status == "pending")
                            .values(status="superseded"))
                    applied += 1
                    patch_cache = True
                else:
                    msg = provider.describe_failure(result)[1]
                    row = await session.get(InventoryPendingWrite, newest_id)
                    if row is not None and row.status == "pending":
                        row.attempts += 1
                        row.last_attempt_at = _now()
                        row.last_error = msg
                    logger.warning("Inventory write for spool %s not applied (will retry): %s", ref, msg)
                await session.commit()
            if patch_cache:                                      # after the commit: one writer at a time on SQLite
                await cache.patch_spool_weight(pid, ref, target)
        await _prune(factory)
        return applied


async def _prune(factory: async_sessionmaker[AsyncSession]) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_KEEP_DAYS)).isoformat()
    async with factory() as session:
        await session.execute(delete(InventoryPendingWrite).where(
            InventoryPendingWrite.status != "pending", InventoryPendingWrite.created_at < cutoff))
        await session.commit()
