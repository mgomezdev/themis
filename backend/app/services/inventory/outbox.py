"""The deduction outbox. A completion enqueues an ABSOLUTE `set remaining = target_g` row inside its own transaction; the
host flushes it to the provider (re-sending an absolute value is harmless, so a crash between send and mark is safe).

Per spool only the newest pending target is sent; older pending rows are marked `superseded`. Rows of a provider that is
no longer active are left alone (they apply if it is re-activated). No DB session is held across a provider call.

Conflict guard (BIZ-198): a row remembers the weight it was computed from (`pre_weight_g`). Before sending, the spool's
current weight is read: unchanged (or already at the target) is fine; anything else means it was changed in the provider
during the print, so the write is HELD as a `conflict` for the user to resolve instead of overwriting that change."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...models import InventoryPendingWrite
from . import cache, events, provider, tasks

logger = logging.getLogger("app")

_KEEP_DAYS = 30
_TOLERANCE_G = 0.5            # providers round weights; a difference this small is not an outside change
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
            printer_id: int | None, source: str, pre_weight_g: float | None = None) -> InventoryPendingWrite:
    """Add the row in the caller's transaction (no commit). `pre_weight_g` (the weight the target was computed from)
    switches the conflict guard on for this row."""
    row = InventoryPendingWrite(provider=provider_id, spool_ref=spool_ref, target_g=max(0.0, float(target_g)),
                                job_id=job_id, printer_id=printer_id, source=source, created_at=_now(),
                                pre_weight_g=None if pre_weight_g is None else float(pre_weight_g))
    session.add(row)
    return row


async def exists_for_job(session: AsyncSession, provider_id: str, spool_ref: str, job_id: int) -> bool:
    """Has this job's deduction already been enqueued for the spool (in any state)? The idempotency check that keeps a
    redelivered completion event from deducting twice."""
    return (await session.execute(select(InventoryPendingWrite.id).where(
        InventoryPendingWrite.provider == provider_id, InventoryPendingWrite.spool_ref == spool_ref,
        InventoryPendingWrite.job_id == job_id).limit(1))).first() is not None


def flush_soon(factory: async_sessionmaker[AsyncSession]) -> None:
    tasks.spawn(flush(factory), name="inventory-outbox-flush")


async def has_pending(factory: async_sessionmaker[AsyncSession]) -> bool:
    async with factory() as session:
        return (await session.execute(select(InventoryPendingWrite.id).where(
            InventoryPendingWrite.status == "pending").limit(1))).first() is not None


def _near(a: float, b: float) -> bool:
    return abs(a - b) <= _TOLERANCE_G


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
            held = set((await session.execute(select(InventoryPendingWrite.spool_ref).where(
                InventoryPendingWrite.provider == pid, InventoryPendingWrite.status == "conflict"))).scalars().all())
            by_spool: dict[str, list[tuple[int, float, float | None, int | None]]] = {}
            for r in rows:
                if r.spool_ref not in held:              # an unresolved conflict holds every later write for the spool
                    by_spool.setdefault(r.spool_ref, []).append((r.id, r.target_g, r.pre_weight_g, r.job_id))
        applied = 0
        for ref, items in by_spool.items():
            for chain in _segments(items):                # guarded and unguarded rows are never merged into one send
                outcome = await _flush_chain(factory, pid, ref, chain)
                if outcome == "applied":
                    applied += 1
                else:                                     # held / failed: later rows for the spool wait for the next flush
                    break
        await _prune(factory)
        return applied


_Item = tuple[int, float, float | None, int | None]       # (row id, target_g, pre_weight_g, job_id)


def _segments(items: list[_Item]) -> list[list[_Item]]:
    """Consecutive rows with a start weight (guarded) or without one (decided by the user after a conflict, or legacy) form a
    segment. A decided row is sent on its own first, so the weight chosen by the user is what the later rows are checked against
    instead of the later rows silently superseding it."""
    out: list[list[_Item]] = []
    for item in items:
        if out and (out[-1][0][2] is None) == (item[2] is None):
            out[-1].append(item)
        else:
            out.append([item])
    return out


async def _flush_chain(factory: async_sessionmaker[AsyncSession], pid: str, ref: str, items: list[_Item]) -> str:
    """Send the newest target of one segment of a spool's queue. Returns `applied`, `held` (conflict) or `failed`."""
    newest_id, target, _, newest_job = items[-1]
    older = [i[0] for i in items[:-1]]
    first_pre = items[0][2]
    already_there = False
    if first_pre is not None:                        # conflict guard: has someone changed the spool since the print started?
        read = await provider.call("get_spool", ref)
        if not read.ok:
            await _record_failure(factory, newest_id, ref, provider.describe_failure(read)[1])
            return "failed"
        current = read.value.remaining_g if read.value is not None else None
        if current is not None:
            known = [first_pre, *[t for _, t, _, _ in items]]          # earlier writes of the chain may already have landed
            if _near(current, target):
                already_there = True
            elif not any(_near(current, k) for k in known):
                await _hold_conflict(factory, pid, ref, newest_id, older, newest_job, float(current), target, first_pre)
                return "held"
    result = None if already_there else await provider.call("set_remaining", ref, target)
    async with factory() as session:
        if already_there or (result is not None and result.ok):
            await session.execute(update(InventoryPendingWrite).where(
                InventoryPendingWrite.id == newest_id, InventoryPendingWrite.status == "pending")
                .values(status="applied", last_attempt_at=_now(), last_error=None))
            if older:
                await session.execute(update(InventoryPendingWrite).where(
                    InventoryPendingWrite.id.in_(older), InventoryPendingWrite.status == "pending")
                    .values(status="superseded"))
            await session.commit()
            ok = True
        else:
            msg = provider.describe_failure(result)[1]
            row = await session.get(InventoryPendingWrite, newest_id)
            if row is not None and row.status == "pending":
                row.attempts += 1
                row.last_attempt_at = _now()
                row.last_error = msg
            logger.warning("Inventory write for spool %s not applied (will retry): %s", ref, msg)
            await session.commit()
            ok = False
    if not ok:
        return "failed"
    await cache.patch_spool_weight(pid, ref, target)         # after the commit: one writer at a time on SQLite
    return "applied"


async def _record_failure(factory: async_sessionmaker[AsyncSession], row_id: int, ref: str, msg: str) -> None:
    async with factory() as session:
        row = await session.get(InventoryPendingWrite, row_id)
        if row is not None and row.status == "pending":
            row.attempts += 1
            row.last_attempt_at = _now()
            row.last_error = msg
        await session.commit()
    logger.warning("Inventory weight check for spool %s failed (will retry): %s", ref, msg)


async def _hold_conflict(factory: async_sessionmaker[AsyncSession], pid: str, ref: str, newest_id: int, older: list[int],
                         job_id: int | None, current: float, target: float, pre: float) -> None:
    """The provider's weight is none of the weights Themis expected: keep the write, do not send it, tell the user."""
    async with factory() as session:
        row = await session.get(InventoryPendingWrite, newest_id)
        if row is None or row.status != "pending":
            return
        # `pre_weight_g` becomes the chain's start weight, so `pre - target` is the grams of EVERY job in the chain (older rows are
        # superseded below and their usage lives on in this row's target).
        row.status, row.conflict_current_g, row.last_attempt_at, row.last_error = "conflict", current, _now(), None
        row.pre_weight_g = pre
        if older:
            await session.execute(update(InventoryPendingWrite).where(
                InventoryPendingWrite.id.in_(older), InventoryPendingWrite.status == "pending").values(status="superseded"))
        await events.emit(
            session, events.WEIGHT_CONFLICT,
            {"provider": pid, "spool_ref": ref, "job_id": job_id, "expected_g": pre, "found_g": current, "target_g": target},
            "Themis: spool weight changed during a print",
            f"Spool {ref} was {current:g} g in the inventory, not the {pre:g} g it had when the print started. "
            f"Choose which weight to keep.", job_id)
        await session.commit()
    logger.warning("Inventory write for spool %s held: weight is %s g, expected %s g", ref, current, pre)


async def _prune(factory: async_sessionmaker[AsyncSession]) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_KEEP_DAYS)).isoformat()
    async with factory() as session:
        await session.execute(delete(InventoryPendingWrite).where(
            InventoryPendingWrite.status.notin_(("pending", "conflict")), InventoryPendingWrite.created_at < cutoff))
        await session.commit()
