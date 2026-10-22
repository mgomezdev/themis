"""Operator view of event delivery (BIZ-249; docs/events.md): the catalog, per-subscriber counters, and the durable outbox's
deliveries (with retry for a dead one). Scopes `settings:read` / `settings:write`."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...eventing.envelope import ENTITY_KEYS
from ...eventing.hub import hub
from ...eventing.redaction import redact_error
from ...eventing.registry import CORE_EVENTS, definer_of_event, event_catalog
from ...models import EventDelivery, EventOutbox

router = APIRouter(prefix="/api/v1/events", tags=["events"])

STATUSES = ("pending", "delivered", "dead")


@router.get("/catalog", dependencies=[Depends(require_scope("settings:read"))])
async def catalog() -> list[dict]:
    """Every event class: name, schema version, durability, and who defines it ("core" or a plugin id)."""
    return [{"name": d.name, "version": d.version, "durability": d.durability, "description": d.description,
             "source": "core" if name in CORE_EVENTS else definer_of_event(name)}
            for name, d in sorted(event_catalog().items())]


@router.get("/subscribers", dependencies=[Depends(require_scope("settings:read"))])
async def subscribers(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Per-subscriber counters since startup (delivered/failed/timed out/dropped/skipped), lane depth and the last redacted error,
    plus the durable backlog (`pending`, `dead`) from the outbox."""
    counts: dict[str, dict[str, int]] = {}
    for subscriber, status, n in (await session.execute(
            select(EventDelivery.subscriber, EventDelivery.status, func.count()).group_by(EventDelivery.subscriber,
                                                                                         EventDelivery.status))).all():
        counts.setdefault(subscriber, {})[status] = n
    out = hub.snapshot()
    seen = {row["subscriber"] for row in out}
    for row in out:
        c = counts.get(row["subscriber"], {})
        row["durable_pending"], row["durable_dead"] = c.get("pending", 0), c.get("dead", 0)
    for subscriber, c in counts.items():                        # durable rows of a subscriber that is not registered right now
        if subscriber not in seen:
            out.append({"subscriber": subscriber, "event": None, "kind": "plugin" if subscriber.startswith("plugin:") else "core",
                        "plugin_id": subscriber.split(":")[1] if subscriber.startswith("plugin:") else None, "delivered": 0,
                        "failed": 0, "timed_out": 0, "dropped": 0, "skipped_inactive": 0, "queue_depth": 0, "last_error": None,
                        "last_error_at": None, "last_ok_at": None, "last_event_id": None,
                        "durable_pending": c.get("pending", 0), "durable_dead": c.get("dead", 0)})
    return out


@router.get("/deliveries", dependencies=[Depends(require_scope("settings:read"))])
async def deliveries(status: str = Query("dead"), subscriber: str | None = None, name: str | None = None,
                     event_id: str | None = None, dedup_key: str | None = None,
                     entity: str | None = Query(None, description="`<key>:<id>`, e.g. `job_id:42`"),
                     limit: int = Query(100, ge=1, le=500), session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Durable deliveries, newest first, with the event's entity references. `status` is pending | delivered | dead (default dead:
    what needs attention). Filter by `subscriber`, event `name`, `event_id`, `dedup_key` or `entity` (one `<key>:<id>`), e.g.
    `?status=delivered&entity=job_id:42` answers "did job 42's completion reach everyone?"."""
    if status not in STATUSES:
        raise HTTPException(422, f"status must be one of {', '.join(STATUSES)}")
    q = (select(EventDelivery, EventOutbox).join(EventOutbox, EventOutbox.id == EventDelivery.outbox_id)
         .where(EventDelivery.status == status).order_by(EventDelivery.id.desc()).limit(limit))
    if subscriber:
        q = q.where(EventDelivery.subscriber == subscriber)
    if name:
        q = q.where(EventOutbox.name == name)
    if event_id:
        q = q.where(EventOutbox.event_id == event_id)
    if dedup_key:
        q = q.where(EventOutbox.dedup_key == dedup_key)
    if entity:
        key, _, value = entity.partition(":")
        if key not in ENTITY_KEYS or not value:
            raise HTTPException(422, f"entity must be <key>:<id> with key one of {', '.join(sorted(ENTITY_KEYS))}")
        q = q.where(func.json_extract(EventOutbox.envelope, f"$.entities.{key}") == (int(value) if value.isdigit() else value))
    return [{"id": d.id, "event_id": o.event_id, "name": o.name, "dedup_key": o.dedup_key, "occurred_at": o.occurred_at,
             "entities": (o.envelope or {}).get("entities", {}), "correlation_id": (o.envelope or {}).get("correlation_id"),
             "subscriber": d.subscriber, "status": d.status, "attempts": d.attempts, "next_attempt_at": d.next_attempt_at,
             "last_attempt_at": d.last_attempt_at, "delivered_at": d.delivered_at,
             "last_error": redact_error(d.last_error) if d.last_error else None}
            for d, o in (await session.execute(q)).all()]


@router.post("/deliveries/{delivery_id}/retry", dependencies=[Depends(require_scope("settings:write"))])
async def retry_delivery(delivery_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    if not await hub.retry(session, delivery_id):
        raise HTTPException(404, "No such undelivered delivery")
    return {"id": delivery_id, "status": "pending"}
