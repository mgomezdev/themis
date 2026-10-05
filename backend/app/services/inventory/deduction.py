"""Usage deduction (BIZ-218): snapshot at print start, ABSOLUTE write at completion through the outbox.

target = max(0, pre_weight - spent). No delta is ever sent, so a replay/duplicate flush is harmless. A spool with no
obtainable starting weight is *suspended* (one event, later prints skipped and flagged) until a user corrects its weight.
Needs TRACKS_WEIGHT + WRITE_WEIGHT. Provider calls only ever run in host tasks, never on the queue loop."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...models import InventorySpoolStatus, Job
from ...plugins.kinds.filament_inventory import TRACKS_WEIGHT, WRITE_WEIGHT
from . import events, outbox, provider, snapshots, tasks

logger = logging.getLogger("app")

EVENT_UNAVAILABLE = events.TRACKING_UNAVAILABLE
EVENT_RESTORED = events.TRACKING_RESTORED
NOTE_SUSPENDED = "Spool tracking is suspended — correct its weight to resume"
NOTE_NO_WEIGHT = "No starting weight was available for this spool"


def can_deduct() -> bool:
    return provider.has(TRACKS_WEIGHT) and provider.has(WRITE_WEIGHT)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Plan:
    """What `plan_completion` decided, for the caller to act on AFTER its commit (`after_commit`)."""
    kind: str                    # enqueued | deferred | skipped
    provider: str = ""
    spool_ref: str = ""
    job_id: int = 0
    printer_id: int | None = None
    grams: float = 0.0
    source: str = "queue"


async def is_suspended(session: AsyncSession, provider_id: str, spool_ref: str) -> bool:
    row = await session.get(InventorySpoolStatus, (provider_id, spool_ref))
    return row is not None and row.tracking == "suspended"


def _skip(job: Job, note: str) -> Plan:
    job.deduction_skipped = True
    job.deduction_note = note
    return Plan("skipped")


async def plan_completion(session: AsyncSession, *, job: Job, printer_id: int | None, spool_ref: str, grams: float,
                          source: str) -> Plan:
    """Inside the completion transaction (DB only — no provider call). Enqueues the absolute write when a start snapshot
    exists; otherwise defers (job was already printing at upgrade / manual completion) to `after_commit`."""
    pid = provider.provider_id()
    if pid is None:
        return Plan("skipped")
    if await is_suspended(session, pid, spool_ref):
        return _skip(job, NOTE_SUSPENDED)
    snap = await snapshots.get(session, job.id, pid, spool_ref)
    job.deduction_skipped = False
    job.deduction_note = None
    if snap is None:
        return Plan("deferred", pid, spool_ref, job.id, printer_id, grams, source)
    if snap.pre_weight_g is None:
        await suspend(session, pid, spool_ref, NOTE_NO_WEIGHT, job)
        return Plan("skipped")
    outbox.enqueue(session, pid, spool_ref, snap.pre_weight_g - grams, job_id=job.id, printer_id=printer_id, source=source)
    return Plan("enqueued", pid, spool_ref, job.id, printer_id, grams, source)


def factory_for(session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    """A session factory on the same engine as `session` (for request handlers, which only have a session)."""
    return async_sessionmaker(session.bind, expire_on_commit=False)


def after_commit(plan: Plan, factory: async_sessionmaker[AsyncSession]) -> None:
    """Schedule the provider-touching part off the caller's path."""
    if plan.kind == "enqueued":
        outbox.flush_soon(factory)
    elif plan.kind == "deferred":
        tasks.spawn(complete_deferred(factory, plan), name=f"inventory-deferred-{plan.job_id}")


async def complete_deferred(factory: async_sessionmaker[AsyncSession], plan: Plan) -> None:
    """No start snapshot: take the weight now (the provider has not seen this print yet) and enqueue the write."""
    if provider.provider_id() != plan.provider:
        return
    pre, _ = await snapshots.read_pre_weight(factory, plan.provider, plan.spool_ref)     # no session during provider I/O
    async with factory() as session:
        job = await session.get(Job, plan.job_id)
        if pre is None:
            await suspend(session, plan.provider, plan.spool_ref, NOTE_NO_WEIGHT, job)
            await session.commit()
            return
        outbox.enqueue(session, plan.provider, plan.spool_ref, pre - plan.grams, job_id=plan.job_id,
                       printer_id=plan.printer_id, source=plan.source)
        await session.commit()
    await outbox.flush(factory)


async def suspend(session: AsyncSession, provider_id: str, spool_ref: str, reason: str, job: Job | None) -> bool:
    """Suspend tracking for the spool and flag the job. Emits the event only when newly suspended. Returns that."""
    if job is not None:
        job.deduction_skipped = True
        job.deduction_note = reason
    row = await session.get(InventorySpoolStatus, (provider_id, spool_ref))
    if row is not None and row.tracking == "suspended":
        return False
    if row is None:
        session.add(InventorySpoolStatus(provider=provider_id, spool_ref=spool_ref, tracking="suspended", reason=reason,
                                         since=_now(), job_id=job.id if job else None))
    else:
        row.tracking, row.reason, row.since, row.job_id = "suspended", reason, _now(), job.id if job else None
    await _emit(session, EVENT_UNAVAILABLE, provider_id, spool_ref, reason, job.id if job else None)
    return True


async def restore(session: AsyncSession, provider_id: str, spool_ref: str) -> bool:
    """Clear a suspension (after the weight was corrected). Emits `tracking_restored` if there was one."""
    row = await session.get(InventorySpoolStatus, (provider_id, spool_ref))
    if row is None or row.tracking != "suspended":
        return False
    await session.delete(row)
    await _emit(session, EVENT_RESTORED, provider_id, spool_ref, "Weight corrected", None)
    return True


async def suspended(session: AsyncSession, provider_id: str | None = None) -> list[InventorySpoolStatus]:
    q = select(InventorySpoolStatus).where(InventorySpoolStatus.tracking == "suspended")
    if provider_id:
        q = q.where(InventorySpoolStatus.provider == provider_id)
    return list((await session.execute(q.order_by(InventorySpoolStatus.since))).scalars().all())


async def _emit(session: AsyncSession, event: str, provider_id: str, spool_ref: str, reason: str, job_id: int | None) -> None:
    title = ("Themis: spool tracking unavailable" if event == EVENT_UNAVAILABLE else "Themis: spool tracking restored")
    await events.emit(session, event, {"provider": provider_id, "spool_ref": spool_ref, "reason": reason, "job_id": job_id},
                      title, f"Spool {spool_ref}: {reason}", job_id)
