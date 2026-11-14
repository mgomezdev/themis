"""Print-completion effects as event subscribers (BIZ-269; docs/events.md).

A job's completion transaction (state change, wear-counter claim, gcode cleanup) stays authoritative and in one place; it also
writes a durable `job.complete` event in the SAME transaction (`enqueue_job_complete`). The effects that follow run from that
event, at least once, each idempotent on the job id:

* `job_complete.maintenance` — bumps the printer's lifetime wear counters, guarded by `jobs.maintenance_accrued` (the flag and the
  counters change in one transaction, so a crash or redelivery can neither lose nor repeat the accrual);
* inventory is an observer too (`services/inventory/subscribers.py`): `job_started.inventory` snapshots the spools in use and
  `job_complete.inventory` deducts from them. This module (the publisher) knows nothing about spools; it only says what the
  printer has loaded;
* `job_complete.notices` — WebSocket broadcast, webhook and notification channels for a real print (not a manual completion).
  Each is attempted on its own and none can fail the completion; the senders swallow their own errors, so a send failure is logged
  and not retried. Delivery of the event itself is still at-least-once: a timeout, crash or shutdown mid-handler redelivers it and
  can repeat a notice (webhook receivers should deduplicate; the notices have no idempotency key).

Importing this module registers the subscribers on the process-wide hub."""
from __future__ import annotations

import logging
from copy import deepcopy

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..eventing import EventEnvelope
from ..eventing.registry import CORE_EVENTS
from ..eventing.hub import hub
from ..models import Job, JobPrinterConfig, Printer
from . import filament_matching, job_details
from .inventory import subscribers as inventory_subscribers

logger = logging.getLogger("app")

_engine = None            # the QueueEngine whose broadcast/webhook/notification senders `job_complete.notices` uses


def bind_engine(engine) -> None:
    global _engine
    _engine = engine

EVENT = "job.complete"
STARTED = "job.started"
ATTEMPT_ENDED = "job.attempt_ended"
# A cancelled print is settled this long after the cancel: the printer's plugin can still send its final usage report, which only
# arrives once the printer has actually stopped (BIZ-366 v2). Failed and requeued prints are settled at once.
CANCEL_SETTLE_GRACE_S = 120.0
HANDLER_TIMEOUT_S = 30.0


async def enqueue_job_started(session: AsyncSession, job: Job, printer_id: int, *, loaded_filaments: list[dict] | None,
                              origin: str | None = "core") -> bool:
    """Add the durable `job.started` event to the caller's start transaction (no commit; call `hub.wake()` after it). One per
    physical start: the key carries the start time, so a job restarted after a failed attempt is a new event."""
    entities: dict[str, int | str] = {"job_id": job.id, "printer_id": printer_id}
    envelope = EventEnvelope(name=STARTED, schema_version=CORE_EVENTS[STARTED].version, origin=origin, entities=entities,
                             dedup_key=f"{STARTED}:{job.id}:{printer_id}:{job.updated_at}",
                             payload={"loaded_filaments": list(loaded_filaments or [])})
    previous = (await job_details.get_details_in(session, job.id, ["filament_usage_context"], origin="core"))["filament_usage_context"]["value"]
    accepted = await hub.enqueue_durable(session, envelope)
    if accepted:
        cfg = (await session.execute(select(JobPrinterConfig).where(
            JobPrinterConfig.job_id == job.id, JobPrinterConfig.printer_id == printer_id))).scalar_one_or_none()
        # A new physical attempt gets a new attempt id. Earlier attempts' reports and accounting stay: a report names its attempt, so
        # settlement ignores one from another attempt, and `filament_accounting` keeps every attempt so a reprint adds to it (BIZ-366 v2).
        # Generic printer facts, persisted in the physical-start transaction. Inventory interprets bindings itself.
        await job_details.set_details(session, job.id, "core", {"filament_usage_context": {
            "attempt_id": envelope.dedup_key, "printer_id": printer_id,
            # A reprint's start snapshot waits for this attempt's writes (inventory/subscribers.py).
            "previous_attempt_id": previous.get("attempt_id") if isinstance(previous, dict) else None,
            "slot_plan": filament_matching.plan_to_json(filament_matching.plan_for(cfg, loaded_filaments)),
            "loaded_filaments": deepcopy(loaded_filaments or []),
        }})
    return accepted


async def enqueue_attempt_ended(session: AsyncSession, job_id: int, printer_id: int | None, *, result: str,
                                loaded_filaments: list[dict] | None = None, origin: str | None = "core") -> bool:
    """Add the durable `job.attempt_ended` event to the caller's transaction (no commit): a STARTED print ended without completing
    (`failed`, `cancelled` while printing, `requeued` after its printer died). Inventory settles that attempt's usage from it. One per
    attempt (keyed on the start context's attempt id); a job that never started an attempt publishes nothing."""
    context = (await job_details.get_details_in(session, job_id, ["filament_usage_context"], origin="core"))["filament_usage_context"]["value"]
    if not isinstance(context, dict) or not context.get("attempt_id"):
        return False
    entities: dict[str, int | str] = {"job_id": job_id}
    if printer_id is not None:
        entities["printer_id"] = printer_id
    envelope = EventEnvelope(name=ATTEMPT_ENDED, schema_version=CORE_EVENTS[ATTEMPT_ENDED].version, origin=origin, entities=entities,
                             dedup_key=f"{ATTEMPT_ENDED}:{context['attempt_id']}",
                             payload={"result": result, "attempt_id": context["attempt_id"], "context": deepcopy(context),
                                      "loaded_filaments": list(loaded_filaments or [])})
    from datetime import datetime, timedelta, timezone
    deliver_at = datetime.now(timezone.utc) + timedelta(seconds=CANCEL_SETTLE_GRACE_S) if result == "cancelled" else None
    return await hub.enqueue_durable(session, envelope, deliver_at=deliver_at)


async def enqueue_job_complete(session: AsyncSession, job: Job, printer_id: int | None, *, source: str,
                               loaded_filaments: list[dict] | None = None, origin: str | None = "core") -> bool:
    """Add the durable `job.complete` event to the caller's completion transaction (no commit; call `hub.wake()` after it).
    One logical event per job: `dedup_key = job.complete:<id>`. `loaded_filaments` = the printer's slots now (they may change after the print)."""
    entities: dict[str, int | str] = {"job_id": job.id}
    if printer_id is not None:
        entities["printer_id"] = printer_id
    if job.project_id is not None:
        entities["project_id"] = job.project_id
    if job.order_id is not None:
        entities["order_id"] = job.order_id
    envelope = EventEnvelope(name=EVENT, schema_version=CORE_EVENTS[EVENT].version, origin=origin, entities=entities,
                             dedup_key=f"{EVENT}:{job.id}", payload={
        "source": source, "actual_seconds": job.actual_seconds, "actual_grams": job.actual_filament_grams,
        "loaded_filaments": list(loaded_filaments or [])})
    return await hub.enqueue_durable(session, envelope)


async def accrue_maintenance(envelope: EventEnvelope) -> None:
    job_id, printer_id = envelope.entities["job_id"], envelope.entities.get("printer_id")
    async with hub.session_factory() as session:
        claim = await session.execute(update(Job).where(Job.id == job_id, Job.status == "complete", Job.maintenance_accrued == 0)
                                      .values(maintenance_accrued=True))
        if claim.rowcount == 0:                                  # redelivery (already counted) or the job no longer exists
            return
        job = await session.get(Job, job_id)
        printer = await session.get(Printer, printer_id) if printer_id is not None else None
        if printer is not None and job is not None:
            printer.lifetime_job_count += 1
            printer.lifetime_print_seconds += job.actual_seconds or 0
        await session.commit()


async def notify_consumers(envelope: EventEnvelope) -> None:
    if envelope.payload.get("source") != "queue":
        return                                                   # a manual completion is an admin action: no integrations
    queue_engine = _engine
    if queue_engine is None:
        logger.warning("Completion notices for job %s dropped: no queue engine is bound", envelope.entities.get("job_id"))
        return
    job_id, printer_id = envelope.entities["job_id"], envelope.entities.get("printer_id")
    for name, send in (("broadcast", lambda: queue_engine._broadcast_job(job_id)),
                       ("webhooks", lambda: queue_engine._fire_webhooks(job_id, EVENT, event_id=envelope.id)),
                       ("notifications", lambda: queue_engine._fire_notifications(job_id, EVENT, printer_id=printer_id,
                                                                                  event_id=envelope.id, origin=envelope.origin))):
        try:
            await send()
        except Exception:                                        # each is independent; none may fail the others or the completion
            logger.exception("Completion %s failed for job %s", name, job_id)


def register() -> None:
    for event, name, handler in ((EVENT, "job_complete.maintenance", accrue_maintenance), (EVENT, "job_complete.notices", notify_consumers)):
        if not hub.has_subscriber(name):
            hub.subscribe(event, handler, name=name, timeout=HANDLER_TIMEOUT_S)
    inventory_subscribers.register(hub, timeout=HANDLER_TIMEOUT_S)


register()
