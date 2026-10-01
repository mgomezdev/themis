"""Low-inventory alerts: after each Spoolman sync, any spool whose remaining grams fell below its threshold
raises a `spool.low` event (webhook + notification channels) once, until it is refilled/replaced.

Threshold = the filament's override if set, else the default; with neither, nothing alerts."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import NotificationConfig, SpoolmanConfig, WebhookConfig
from . import notification_service, webhook_service

logger = logging.getLogger("app")

EVENT = "spool.low"


@dataclass(frozen=True)
class LowSpool:
    spool_id: int
    filament_id: int | None
    name: str
    remaining_g: float
    threshold_g: float
    location: str | None


def threshold_for(filament_id: int | None, default_g: float | None, overrides: dict | None) -> float | None:
    if filament_id is not None and overrides:
        override = overrides.get(str(filament_id))
        if override is not None:
            return float(override)
    return float(default_g) if default_g is not None else None


def spool_name(spool: dict) -> str:
    fil = spool.get("filament") or {}
    vendor = (fil.get("vendor") or {}).get("name")
    name = fil.get("name") or f"spool {spool.get('id')}"
    return f"{vendor} {name}" if vendor else name


def find_low(spools: list[dict], default_g: float | None, overrides: dict | None) -> list[LowSpool]:
    out = []
    for s in spools:
        remaining = s.get("remaining_weight")
        if remaining is None or s.get("archived"):
            continue
        fil = s.get("filament") or {}
        limit = threshold_for(fil.get("id"), default_g, overrides)
        if limit is None or remaining >= limit:
            continue
        out.append(LowSpool(int(s["id"]), fil.get("id"), spool_name(s), float(remaining), limit,
                            (s.get("location") or "").strip() or None))
    return out


def message_for(low: LowSpool) -> tuple[str, str]:
    where = f" ({low.location})" if low.location else ""
    return ("Themis: spool running low",
            f"{low.name}{where} has {low.remaining_g:.0f} g left (alert below {low.threshold_g:.0f} g).")


async def process(session: AsyncSession, row: SpoolmanConfig, spools: list[dict]) -> list[LowSpool]:
    """Alert for spools newly below threshold; remember them so they alert once. Never raises."""
    try:
        low = find_low(spools, row.low_stock_default_g, row.low_stock_overrides)
        already = set(row.low_stock_alerted or [])
        fresh = [l for l in low if l.spool_id not in already]
        now_low = sorted(l.spool_id for l in low)
        if now_low != sorted(already):
            row.low_stock_alerted = now_low   # also drops spools that were refilled, re-arming them
        if fresh:
            await _deliver(session, fresh)
        return fresh
    except Exception:
        logger.exception("Low-stock evaluation failed")
        return []


async def _deliver(session: AsyncSession, fresh: list[LowSpool]) -> None:
    webhook = await session.get(WebhookConfig, 1)
    notif = await session.get(NotificationConfig, 1)
    for low in fresh:
        if webhook and webhook.url and (not webhook.events or EVENT in webhook.events):
            webhook_service.schedule(webhook.url, webhook.secret, EVENT, None, {
                "spool_id": low.spool_id, "filament_id": low.filament_id, "name": low.name,
                "remaining_g": low.remaining_g, "threshold_g": low.threshold_g, "location": low.location})
        if notif and (notif.ntfy_enabled or notif.discord_enabled or notif.email_enabled):
            title, message = message_for(low)
            asyncio.create_task(notification_service.dispatch(notif, EVENT, None, title, message))
