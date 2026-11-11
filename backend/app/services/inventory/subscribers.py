"""Inventory as an observer of print events (BIZ-308). The printer and the queue do not drive spool tracking: they publish
`job.started` / `job.complete` / `job.attempt_ended` and inventory reacts.

* `job_started.inventory` — snapshots the weight of each spool loaded when a physical print attempt starts (`snapshots.take`),
  per attempt, so a reprint snapshots again;
* `job_complete.inventory` / `job_attempt_ended.inventory` — settle the attempt that just ended (BIZ-366 v2): what each loaded slot
  fed, from the printer plugin's usage report (`filament_usage.allocate`), else — for a completed print only — the slicer estimate.
  The result is frozen per attempt in the job's `filament_accounting` before any write, so a redelivery or a later report revision
  never deducts again or differently; each spool's absolute `snapshot - spent` write is planned per attempt (`deduction.plan_completion`).

Deductions happen only when the active provider can track and write weight and the operator left deduction on; the accounting
(what each attempt used, and the job's total) is recorded either way. A job that started before attempt contexts existed keeps the
legacy completion path (the `inventory` payload of an old event, else the completion-time slots)."""
from __future__ import annotations

import asyncio
import weakref

from sqlalchemy import select

from ...eventing import EventEnvelope
from ...models import EventOutbox, Job, JobPrinterConfig
from .. import filament_usage, job_details
from ..job_reporter import REPORT_FIELD, REPORTS_FIELD
from . import config, deduction, outbox, provider, refs, snapshots


# Settling an attempt is read-modify-write of the job's `filament_accounting`; `job_complete.inventory` and
# `job_attempt_ended.inventory` run as separate subscribers, so one job's settlements are serialized here (one process).
_job_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[int, asyncio.Lock]]" = weakref.WeakKeyDictionary()


def _lock(job_id: int) -> asyncio.Lock:
    """The job's settlement lock on the running loop (a lock belongs to one loop)."""
    return _job_locks.setdefault(asyncio.get_running_loop(), {}).setdefault(job_id, asyncio.Lock())


class PreviousAttemptPending(Exception):
    """`job.started` arrived before the job's previous attempt was settled: retried by the hub (its writes must exist first)."""


async def _config_for(session, job_id: int, printer_id: int | None) -> JobPrinterConfig | None:
    if printer_id is None:
        return None
    return (await session.execute(select(JobPrinterConfig).where(
        JobPrinterConfig.job_id == job_id, JobPrinterConfig.printer_id == printer_id))).scalar_one_or_none()


async def snapshot_on_start(envelope: EventEnvelope) -> None:
    from ...eventing.hub import hub
    job_id, printer_id = envelope.entities["job_id"], envelope.entities.get("printer_id")
    factory = hub.session_factory
    async with factory() as session:
        if not (deduction.can_deduct() and await config.deduct_enabled(session)):
            return
        job = await session.get(Job, job_id)
        cfg = await _config_for(session, job_id, printer_id)
        context = (await job_details.get_details_in(session, job_id, ["filament_usage_context"], origin="core"))["filament_usage_context"]
        attempt_id = ""
        if context["status"] == "ok":
            # The plugin may report any loaded slot (an AMS rollover onto a backup spool): snapshot every bound spool loaded at start.
            attempt_id = context["value"]["attempt_id"]
            spool_refs = list(dict.fromkeys(r for slot in context["value"]["loaded_filaments"]
                                            if not slot.get("empty") and (r := refs.slot_spool_ref(slot)) is not None))
        else:
            spool_refs = deduction.snapshot_refs(cfg, envelope.payload.get("loaded_filaments") or []) if cfg is not None else []
        pid = provider.provider_id()
        if job is None or job.status != "printing" or pid is None:
            return                                    # delivered late (the print already ended): the weight now is not the start weight
        previous = context["value"].get("previous_attempt_id") if context["status"] == "ok" else None
        if previous and await _awaiting_settlement(session, job_id, previous):
            # A reprint snapshots from the previous attempt's pending write: that write must exist first (events of different
            # subscribers are not ordered). The hub retries this delivery with backoff.
            raise PreviousAttemptPending(f"job {job_id}: attempt {previous} is not settled yet")
        spool_refs = [r for r in spool_refs if not await outbox.exists_for_job(session, pid, r, job_id, attempt_id or None)]
    for spool_ref in spool_refs:
        await snapshots.take(factory, job_id, printer_id, spool_ref, attempt_id)    # provider I/O, with no session held


async def _awaiting_settlement(session, job_id: int, attempt_id: str) -> bool:
    """Is `attempt_id` waiting on its `job.attempt_ended` (published, but its deductions not planned yet)?"""
    saved = (await job_details.get_details_in(session, job_id, ["filament_accounting"], origin="core"))["filament_accounting"]["value"]
    entry = _attempts(saved).get(attempt_id)
    if entry is not None and (entry.get("planned") or entry.get("legacy")):
        return False
    return (await session.execute(select(EventOutbox.id).where(
        EventOutbox.dedup_key == f"job.attempt_ended:{attempt_id}").limit(1))).first() is not None


def _attempts(accounting) -> dict:
    """The job's settled attempts ({attempt_id: entry}); a single-entry record from PR #173 (before v2) counts as one `legacy`
    attempt: its writes were keyed by job, not attempt, so a redelivery must check them that way."""
    if not isinstance(accounting, dict):
        return {}
    if isinstance(accounting.get("attempts"), dict):
        return dict(accounting["attempts"])
    if accounting.get("attempt_id"):
        return {accounting["attempt_id"]: {**accounting, "grams": sum((accounting.get("by_slot") or {}).values()), "legacy": True}}
    return {}


async def _mark_planned(session, job_id: int, attempt_id: str) -> None:
    saved = (await job_details.get_details_in(session, job_id, ["filament_accounting"], origin="core"))["filament_accounting"]["value"]
    if not isinstance(saved, dict) or not isinstance(saved.get("attempts"), dict) or attempt_id not in saved["attempts"]:
        return
    saved["attempts"][attempt_id] = {**saved["attempts"][attempt_id], "planned": True}
    await job_details.set_details(session, job_id, "core", {"filament_accounting": saved})
    await session.commit()


async def _settle(session, job: Job, printer_id: int | None, context: dict, *, ended: str, end_loaded: list | None,
                  actual_grams) -> dict:
    """Allocate and freeze one attempt's usage (once). Returns its entry."""
    saved = (await job_details.get_details_in(session, job.id, ["filament_accounting"], origin="core"))["filament_accounting"]["value"]
    attempts = _attempts(saved)
    entry = attempts.get(context["attempt_id"])
    if entry is not None:
        return entry                                  # redelivered: the allocation was frozen when the attempt was first settled
    details = await job_details.get_details_in(session, job.id, [REPORT_FIELD, "filament_usage"])
    # This attempt's report: core's per-attempt copy (the plugin's own field holds only its latest one, which a reprint replaces).
    reports = (await job_details.get_details_in(session, job.id, [REPORTS_FIELD], origin="core"))[REPORTS_FIELD]["value"]
    report = reports.get(context["attempt_id"]) if isinstance(reports, dict) else None
    if report is None and details[REPORT_FIELD]["status"] == "ok":
        report = details[REPORT_FIELD]["value"]           # a report stored directly as the plugin's field (no copy)
    usage = details["filament_usage"]["value"] if details["filament_usage"]["status"] == "ok" else None
    grams, breakdown = actual_grams, job.actual_filament_breakdown
    if isinstance(usage, dict) and usage.get("grams") is not None:
        grams, breakdown = usage["grams"], usage.get("breakdown") if usage.get("breakdown") is not None else breakdown
    alloc = filament_usage.allocate(context, ended=ended, report=report if isinstance(report, dict) else None,
                                    end_loaded=end_loaded, estimate_grams=grams, estimate_breakdown=breakdown)
    merged: dict[str, float] = {}
    unbound: list[str] = []
    for slot, used in alloc.slots:
        ref = refs.slot_spool_ref(slot)
        if ref is None:
            unbound.append(f"T{slot.get('slot')}")
        elif used > 0:
            merged[str(ref)] = merged.get(str(ref), 0.0) + used
    notes = [alloc.reason] if alloc.reason else []
    if unbound:
        notes.append(f"No spool is bound to {', '.join(unbound)}, so nothing was deducted for it — bind a spool and correct its weight")
    entry = {"result": ended, "source": alloc.source, "grams": alloc.grams, "printer_id": printer_id,
             "slots": [{"slot": s.get("slot"), "grams": g} for s, g in alloc.slots],
             "uses": [{"spool_ref": r, "grams": g} for r, g in merged.items()], "notes": notes, "provider": provider.provider_id()}
    attempts[context["attempt_id"]] = entry
    # A reprint adds to what the earlier attempts used: the job's consumption is `total_grams`. `jobs.actual_filament_grams` stays the
    # sliced estimate (the per-attempt fallback reads it, and its breakdown must match it).
    total = sum(float(e.get("grams") or 0.0) for e in attempts.values())
    await job_details.set_details(session, job.id, "core", {"filament_accounting": {"attempts": attempts, "total_grams": total}})
    await session.commit()
    return entry


async def _deduct(factory, job_id: int, printer_id: int | None, uses: list, notes: list, *, source: str,
                  attempt_id: str | None) -> None:
    for use in uses:
        async with factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            plan = await deduction.plan_completion(session, job=job, printer_id=printer_id, spool_ref=str(use["spool_ref"]),
                                                   grams=float(use["grams"]), source=source, attempt_id=attempt_id)
            await session.commit()
        if plan.kind == "deferred":
            # No start snapshot: read the weight and write the outbox row HERE, so the event is acknowledged only once the deduction is
            # durable (a crash or a provider error before that redelivers it; the outbox-row check makes the retry safe).
            await deduction.complete_deferred(factory, plan)
        else:
            deduction.after_commit(plan, factory)
    if notes:
        await deduction.warn_untracked(factory, job_id, printer_id, notes)


async def _settle_and_deduct(envelope: EventEnvelope, *, ended: str, context: dict | None) -> bool:
    """Settle `context`'s attempt; False when there is no attempt context (the caller then takes the legacy path)."""
    from ...eventing.hub import hub
    job_id, printer_id = envelope.entities["job_id"], envelope.entities.get("printer_id")
    factory = hub.session_factory
    async with _lock(job_id):
        async with factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return True
            if context is None:
                saved = (await job_details.get_details_in(session, job_id, ["filament_usage_context"], origin="core"))["filament_usage_context"]
                context = saved["value"] if saved["status"] == "ok" else None
            if not isinstance(context, dict) or not context.get("attempt_id"):
                return False
            entry = await _settle(session, job, printer_id, context, ended=ended,
                                  end_loaded=envelope.payload.get("loaded_filaments"), actual_grams=envelope.payload.get("actual_grams"))
            enabled = deduction.can_deduct() and await config.deduct_enabled(session)
        if enabled:
            if entry.get("provider") != provider.provider_id():
                uses, notes = [], ["Inventory provider changed after usage was allocated; correct the spool weights by hand"]
            else:
                uses, notes = entry.get("uses") or [], entry.get("notes") or []
            if uses or notes:
                source = "manual_complete" if envelope.payload.get("source") == "manual" else "queue"
                # A PR #173-era record was written per job (outbox rows with no attempt, its snapshot keyed ''): check it that way.
                await _deduct(factory, job_id, printer_id, uses, notes, source=source,
                              attempt_id=None if entry.get("legacy") else context["attempt_id"])
        if not entry.get("planned") and not entry.get("legacy"):
            async with factory() as session:
                await _mark_planned(session, job_id, context["attempt_id"])
    return True


async def deduct_on_attempt_ended(envelope: EventEnvelope) -> None:
    # The event carries the attempt's start context: by the time it is delivered the job may already have started its next attempt.
    context = envelope.payload.get("context") or None
    if context is not None and context.get("attempt_id") != envelope.payload.get("attempt_id"):
        context = None
    if context is None:                           # an event without its context: only the saved one can be this attempt's
        from ...eventing.hub import hub
        async with hub.session_factory() as session:
            saved = (await job_details.get_details_in(session, envelope.entities["job_id"], ["filament_usage_context"], origin="core"))
            value = saved["filament_usage_context"]["value"]
            context = value if isinstance(value, dict) and value.get("attempt_id") == envelope.payload.get("attempt_id") else None
        if context is None:
            return
    await _settle_and_deduct(envelope, ended=envelope.payload["result"], context=context)


async def deduct_on_complete(envelope: EventEnvelope) -> None:
    from ...eventing.hub import hub
    payload = envelope.payload
    legacy = "inventory" in payload or "deduction_notes" in payload            # written before inventory subscribed: spools already resolved
    if not legacy and await _settle_and_deduct(envelope, ended="complete", context=None):
        return
    job_id, printer_id = envelope.entities["job_id"], envelope.entities.get("printer_id")
    factory = hub.session_factory
    raw = payload.get("inventory")
    uses = [raw] if isinstance(raw, dict) else list(raw or [])
    notes = list(payload.get("deduction_notes") or [])
    if not legacy:
        # A job that started before attempt contexts existed: the completion-time slots and the sliced (or reported) total.
        async with factory() as session:
            job = await session.get(Job, job_id)
            grams, breakdown = payload.get("actual_grams"), None
            usage = (await job_details.get_details_in(session, job_id, ["filament_usage"]))["filament_usage"]
            if usage["status"] == "ok" and isinstance(usage["value"], dict) and usage["value"].get("grams") is not None:
                grams, breakdown = usage["value"]["grams"], usage["value"].get("breakdown")
            if grams is None:
                return
            cfg = await _config_for(session, job_id, printer_id) if job is not None else None
            if cfg is None or not (deduction.can_deduct() and await config.deduct_enabled(session)):
                return
            uses, notes = deduction.completion_uses(cfg, payload.get("loaded_filaments") or [], float(grams),
                                                    breakdown if breakdown is not None else job.actual_filament_breakdown)
    if not uses and not notes:
        return
    source = "queue" if payload.get("source") == "queue" else "manual_complete"
    await _deduct(factory, job_id, printer_id, uses, notes, source=source, attempt_id=None)


def register(hub, *, timeout: float) -> None:
    for event, name, handler in (("job.started", "job_started.inventory", snapshot_on_start),
                                 ("job.complete", "job_complete.inventory", deduct_on_complete),
                                 ("job.attempt_ended", "job_attempt_ended.inventory", deduct_on_attempt_ended)):
        if not hub.has_subscriber(name):
            hub.subscribe(event, handler, name=name, timeout=timeout)
