"""Printer alarm feed: reconcile what each printer currently reports with the `printer_alarms` history, and raise
a `printer.alarm` webhook/notification for each *new* alarm at or above the configured severity.

Reconcile rules (per printer, on every state update — a cheap in-memory signature skips unchanged reports):
* a reported code with no active row → a new row (and an event if severity ≥ the filter);
* a reported code with an active row → `last_seen` refreshed, severity/message updated (no new event);
* an active row whose code is no longer reported → resolved (`resolved_at`), kept as history.
A code that comes back after resolving is a new occurrence (a new row, a new event)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import NotificationConfig, Printer, PrinterAlarm, QueueConfig, WebhookConfig
from . import notification_service, webhook_service
from .abstract_printer_client import SEVERITIES, Alarm

logger = logging.getLogger("app")

EVENT = "printer.alarm"
DEFAULT_MIN_SEVERITY = "warning"
KEEP_RESOLVED_DAYS = 90


def rank(severity: str) -> int:
    return SEVERITIES.index(severity) if severity in SEVERITIES else 0


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def signature(alarms: list[Alarm]) -> tuple:
    return tuple(sorted((a.code, a.severity, a.message) for a in alarms))


async def min_severity(session: AsyncSession) -> str:
    cfg = await session.get(QueueConfig, 1)
    value = getattr(cfg, "alarm_min_severity", None) if cfg else None
    return value if value in SEVERITIES else DEFAULT_MIN_SEVERITY


async def reconcile(session: AsyncSession, printer_id: int, alarms: list[Alarm]) -> list[PrinterAlarm]:
    """Apply `alarms` (the printer's current report) to the history; returns the rows newly raised. Commits."""
    active = (await session.execute(
        select(PrinterAlarm).where(PrinterAlarm.printer_id == printer_id, PrinterAlarm.resolved_at.is_(None))
    )).scalars().all()
    by_code = {a.code: a for a in active}
    stamp = now()
    reported = {a.code: a for a in alarms}
    fresh: list[PrinterAlarm] = []

    for code, a in reported.items():
        row = by_code.get(code)
        if row is None:
            row = PrinterAlarm(printer_id=printer_id, code=code, severity=a.severity, message=a.message,
                               source=a.source, help_url=a.help_url, first_seen=stamp, last_seen=stamp)
            session.add(row)
            fresh.append(row)
        else:
            row.last_seen, row.severity, row.message, row.help_url = stamp, a.severity, a.message, a.help_url
    for code, row in by_code.items():
        if code not in reported:
            row.resolved_at = stamp
    await session.commit()
    for row in fresh:
        await session.refresh(row)
    return fresh


async def deliver(session: AsyncSession, printer: Printer | None, row: PrinterAlarm) -> bool:
    """Webhook + notification channels for one new alarm, honouring the severity filter. Never raises."""
    try:
        if rank(row.severity) < rank(await min_severity(session)):
            return False
        webhook = await session.get(WebhookConfig, 1)
        notif = await session.get(NotificationConfig, 1)
        name = printer.name if printer else f"printer {row.printer_id}"
        if webhook and webhook.url and (not webhook.events or EVENT in webhook.events):
            webhook_service.schedule(webhook.url, webhook.secret, EVENT, None, {
                "printer_id": row.printer_id, "printer_name": name, "alarm_id": row.id, "code": row.code,
                "severity": row.severity, "message": row.message, "help_url": row.help_url})
        if notif and (notif.ntfy_enabled or notif.discord_enabled or notif.email_enabled):
            title = f"Themis: {row.severity} on {name}"
            asyncio.create_task(notification_service.dispatch(notif, EVENT, None, title, row.message))
        return True
    except Exception:
        logger.exception("Could not deliver alarm %s for printer %s", row.code, row.printer_id)
        return False


async def purge_old(session: AsyncSession, days: int = KEEP_RESOLVED_DAYS) -> int:
    """Delete resolved alarms older than `days` (history is bounded)."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = (await session.execute(
        select(PrinterAlarm).where(PrinterAlarm.resolved_at.is_not(None), PrinterAlarm.resolved_at < cutoff)
    )).scalars().all()
    for r in rows:
        await session.delete(r)
    await session.commit()
    return len(rows)


class AlarmTracker:
    """Hooked into PrinterManager.on_state_change: remembers the last report per printer and only touches the DB
    when it changes, so a telemetry stream of identical frames costs nothing."""

    def __init__(self) -> None:
        self._last: dict[int, tuple] = {}

    def forget(self, printer_id: int) -> None:
        self._last.pop(printer_id, None)

    async def observe(self, session_factory, printer_id: int, alarms: list[Alarm], broadcast=None) -> None:
        sig = signature(alarms)
        if self._last.get(printer_id) == sig:
            return
        async with session_factory() as session:
            printer = await session.get(Printer, printer_id)
            if printer is None:
                return
            fresh = await reconcile(session, printer_id, alarms)
            for row in fresh:
                await deliver(session, printer, row)
        self._last[printer_id] = sig               # only after a successful reconcile, so a DB failure retries
        if broadcast is not None:
            await broadcast("alarms_changed", {"printer_id": printer_id})


tracker = AlarmTracker()
