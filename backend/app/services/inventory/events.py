"""Inventory events (`inventory.*`) through the same plumbing as `spool.low`: a webhook (when configured and subscribed) and
the notification channels. Never raises — an undeliverable event must not break the path that raised it."""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from .. import webhook_service
from ..notify import notify
from . import tasks

logger = logging.getLogger("app")

TRACKING_UNAVAILABLE = "inventory.tracking_unavailable"
TRACKING_RESTORED = "inventory.tracking_restored"
DISCONNECTED = "inventory.disconnected"
RECONNECTED = "inventory.reconnected"
WEIGHT_CONFLICT = "inventory.weight_conflict"


async def emit(session: AsyncSession, event: str, payload: dict, title: str, message: str, job_id: int | None = None) -> bool:
    """Schedule delivery. Returns False when it could not even be scheduled (a DB error loading the configs)."""
    try:
        await webhook_service.dispatch(session, event, job_id, payload)
        tasks.spawn(notify(event, job_id, title, message))
        return True
    except Exception:
        logger.exception("Could not send %s", event)
        return False
