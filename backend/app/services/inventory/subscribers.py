"""Inventory as an observer of print events (BIZ-308). The printer and the queue do not drive spool tracking: they publish
`job.started` / `job.complete` and inventory reacts.

* `job_started.inventory` — snapshots the weight of each spool bound to a slot the print uses (`snapshots.take`);
* `job_complete.inventory` — works out the spool(s) the print used and plans the absolute `snapshot - spent` write
  (`deduction.plan_completion`).

Both do nothing unless the active provider can track and write weight and the operator left deduction on. `job_complete.inventory` reads what the print used from the job's `filament_usage` detail first, then the event's `actual_grams`, and the legacy
`inventory` payload still wins when an old event carries it. Both are idempotent per job
(at-least-once delivery): a snapshot is unique per job/provider/spool, and a deduction whose outbox row exists is not planned again."""
from __future__ import annotations

from sqlalchemy import select

from ...eventing import EventEnvelope
from ...models import Job, JobPrinterConfig
from .. import job_details
from . import config, deduction, outbox, provider, snapshots


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
        spool_refs = deduction.snapshot_refs(cfg, envelope.payload.get("loaded_filaments") or []) if cfg is not None else []
        pid = provider.provider_id()
        if job is None or job.status != "printing" or pid is None:
            return                                    # delivered late (the print already ended): the weight now is not the start weight
        spool_refs = [r for r in spool_refs if not await outbox.exists_for_job(session, pid, r, job_id)]
    for spool_ref in spool_refs:
        await snapshots.take(factory, job_id, printer_id, spool_ref)             # provider I/O, with no session held


async def deduct_on_complete(envelope: EventEnvelope) -> None:
    from ...eventing.hub import hub
    payload = envelope.payload
    job_id, printer_id = envelope.entities["job_id"], envelope.entities.get("printer_id")
    factory = hub.session_factory
    legacy = "inventory" in payload or "deduction_notes" in payload            # written before inventory subscribed: spools already resolved
    raw = payload.get("inventory")
    uses = [raw] if isinstance(raw, dict) else list(raw or [])
    notes = list(payload.get("deduction_notes") or [])
    if not legacy:
        async with factory() as session:
            job = await session.get(Job, job_id)
            # What the print used, from the most authoritative source first: the `filament_usage` detail the printer's plugin reported
            # (or core filled in for it), else the grams carried on the event (the shape before details existed).
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
    for use in uses:
        async with factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            plan = await deduction.plan_completion(session, job=job, printer_id=printer_id, spool_ref=str(use["spool_ref"]),
                                                   grams=float(use["grams"]), source=source)
            await session.commit()
        if plan.kind == "deferred":
            # No start snapshot: read the weight and write the outbox row HERE, so the event is acknowledged only once the deduction is
            # durable (a crash or a provider error before that redelivers it; the outbox-row check makes the retry safe).
            await deduction.complete_deferred(factory, plan)
        else:
            deduction.after_commit(plan, factory)
    if notes:
        await deduction.warn_untracked(factory, job_id, printer_id, notes)


def register(hub, *, timeout: float) -> None:
    for event, name, handler in (("job.started", "job_started.inventory", snapshot_on_start),
                                 ("job.complete", "job_complete.inventory", deduct_on_complete)):
        if not hub.has_subscriber(name):
            hub.subscribe(event, handler, name=name, timeout=timeout)
