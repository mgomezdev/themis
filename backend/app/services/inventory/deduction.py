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
from ...plugins.capabilities.filament_inventory import TRACKS_WEIGHT, WRITE_WEIGHT
from .. import filament_matching
from . import events, outbox, provider, refs, snapshots, tasks

logger = logging.getLogger("app")

EVENT_UNAVAILABLE = events.TRACKING_UNAVAILABLE
EVENT_RESTORED = events.TRACKING_RESTORED
EVENT_NO_SPOOL = events.NO_SPOOL_TRACKED
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
    if await outbox.exists_for_job(session, pid, spool_ref, job.id):     # a redelivered completion event: already planned
        return Plan("skipped")
    snap = await snapshots.get(session, job.id, pid, spool_ref)
    job.deduction_skipped = False
    job.deduction_note = None
    if snap is None:
        return Plan("deferred", pid, spool_ref, job.id, printer_id, grams, source)
    if snap.pre_weight_g is None:
        await suspend(session, pid, spool_ref, NOTE_NO_WEIGHT, job)
        return Plan("skipped")
    outbox.enqueue(session, pid, spool_ref, snap.pre_weight_g - grams, job_id=job.id, printer_id=printer_id, source=source,
                   pre_weight_g=snap.pre_weight_g)
    return Plan("enqueued", pid, spool_ref, job.id, printer_id, grams, source)


def completion_uses(config, loaded: list | None, grams: float, breakdown: list | None) -> tuple[list[dict], list[str]]:
    """The spool deductions of one finished print and the notes for what could not be tracked: `([{"spool_ref", "grams"}], [note])`.

    The spool is the one bound to the slot that PRINTED (the claim-time plan), per used slot for a multi-material job; the job's own
    ask never selects a spool. One used slot takes all the grams (two colours sharing a slot included); several take their share from
    the per-extruder breakdown (extruder index = tool index). A slot with no bound spool is skipped and named in a note, so the
    operator can bind a spool and correct the weight."""
    plan = filament_matching.plan_for(config, loaded)
    slots = list(dict.fromkeys(u.slot for u in plan))
    if not slots:
        return [], ["No filament slot was recorded for this print, so no spool was tracked"]
    loaded = list(loaded or [])
    by_extruder = {}
    for entry in breakdown or []:
        by_extruder[entry.get("extruder_index")] = by_extruder.get(entry.get("extruder_index"), 0.0) + float(entry.get("grams") or 0.0)
    merged: dict[str, float] = {}
    unbound: list[str] = []
    for slot in slots:
        ref = refs.slot_spool_ref(loaded[slot]) if 0 <= slot < len(loaded) else None
        if len(slots) == 1:
            share = float(grams)
        elif by_extruder:
            share = by_extruder.get(slot, 0.0)
        else:
            return [], ["The filament used per slot was not reported, so no spool was tracked"]
        if ref is None:
            unbound.append(f"T{slot}")
        elif share > 0:
            merged[str(ref)] = merged.get(str(ref), 0.0) + share
    notes = [f"No spool is bound to {', '.join(unbound)}, so nothing was deducted for it — bind a spool and correct its weight"] if unbound else []
    return [{"spool_ref": r, "grams": g} for r, g in merged.items()], notes


def snapshot_refs(config, loaded: list | None) -> list[str]:
    """The spool refs to snapshot at print start: those bound to the planned slots (DB only)."""
    out = []
    for u in filament_matching.plan_for(config, loaded):
        ref = refs.slot_spool_ref((loaded or [])[u.slot]) if 0 <= u.slot < len(loaded or []) else None
        if ref is not None and str(ref) not in out:
            out.append(str(ref))
    return out


async def warn_untracked(factory: async_sessionmaker[AsyncSession], job_id: int, printer_id: int | None, notes: list[str]) -> None:
    """Flag the job (its detail shows "usage was not recorded: ...") and warn the operator ONCE per job (a redelivered completion
    finds the note already there)."""
    note = "; ".join(notes)
    async with factory() as session:
        job = await session.get(Job, job_id)
        if job is None or (job.deduction_skipped and job.deduction_note and note in job.deduction_note):
            return
        job.deduction_note = f"{job.deduction_note}; {note}" if job.deduction_skipped and job.deduction_note else note
        job.deduction_skipped = True
        await events.emit(session, EVENT_NO_SPOOL, {"job_id": job_id, "printer_id": printer_id, "notes": notes},
                          "Themis: filament usage not tracked", f"Job {job_id}: {note}.", job_id)
        await session.commit()


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
        if await outbox.exists_for_job(session, plan.provider, plan.spool_ref, plan.job_id):
            return                                                       # another delivery of the same completion got here first
        if pre is None:
            await suspend(session, plan.provider, plan.spool_ref, NOTE_NO_WEIGHT, job)
            await session.commit()
            return
        outbox.enqueue(session, plan.provider, plan.spool_ref, pre - plan.grams, job_id=plan.job_id,
                       printer_id=plan.printer_id, source=plan.source, pre_weight_g=pre)
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
