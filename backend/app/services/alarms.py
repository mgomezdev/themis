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

from ..models import Printer, PrinterAlarm, QueueConfig
from . import webhook_service
from .notify import notify
from .abstract_printer_client import SEVERITIES, Alarm

logger = logging.getLogger("app")

EVENT = "printer.alarm"
DEFAULT_MIN_SEVERITY = "warning"
KEEP_RESOLVED_DAYS = 90
_delivery_tasks: set[asyncio.Task] = set()      # strong refs: a bare create_task can be garbage-collected mid-flight


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


async def reconcile(session: AsyncSession, printer_id: int, alarms: list[Alarm], grace_s: float = 0) -> list[PrinterAlarm]:
    """Apply `alarms` (the printer's current report) to the history; returns the rows to ANNOUNCE: newly raised ones
    and ones whose severity escalated. Commits.

    `grace_s`: an active alarm that isn't reported is only resolved once it has been absent that long (measured from
    `last_seen`, which only advances while it is reported). That absorbs the empty state a client has right after a
    Themis restart or reconnect and frames that momentarily lack the error field, so neither resolves a standing
    alarm and re-raises it as a "new" one. `has_pending` on the returned list's owner tells callers to look again."""
    active = (await session.execute(
        select(PrinterAlarm).where(PrinterAlarm.printer_id == printer_id, PrinterAlarm.resolved_at.is_(None))
    )).scalars().all()
    by_code = {a.code: a for a in active}
    stamp = now()
    reported = {a.code: a for a in alarms}
    announce: list[PrinterAlarm] = []

    for code, a in reported.items():
        row = by_code.get(code)
        if row is None:
            row = PrinterAlarm(printer_id=printer_id, code=code, severity=a.severity, message=a.message,
                               source=a.source, help_url=a.help_url, first_seen=stamp, last_seen=stamp)
            session.add(row)
            announce.append(row)
        else:
            if rank(a.severity) > rank(row.severity):
                announce.append(row)                          # escalated (e.g. warning → fatal): worth telling again
                row.acknowledged_at = None                    # and it needs attention again
            row.last_seen, row.severity, row.message, row.help_url = stamp, a.severity, a.message, a.help_url
    pending = False
    for code, row in by_code.items():
        if code in reported:
            continue
        absent_for = (datetime.fromisoformat(stamp) - datetime.fromisoformat(row.last_seen)).total_seconds()
        if absent_for >= grace_s:
            row.resolved_at = stamp
        else:
            pending = True
    await session.commit()
    for row in announce:
        await session.refresh(row)
    reconcile.last_pending = pending                           # type: ignore[attr-defined]
    return announce


async def deliver(session: AsyncSession, printer: Printer | None, row: PrinterAlarm) -> bool:
    """Webhook + notification channels for one new alarm, honouring the severity filter. Never raises."""
    try:
        if rank(row.severity) < rank(await min_severity(session)):
            return False
        name = printer.name if printer else f"printer {row.printer_id}"
        await webhook_service.dispatch(session, EVENT, None, {
            "printer_id": row.printer_id, "printer_name": name, "alarm_id": row.id, "code": row.code,
            "severity": row.severity, "message": row.message, "help_url": row.help_url})
        title = f"Themis: {row.severity} on {name}"
        task = asyncio.create_task(notify(EVENT, None, title, row.message))
        _delivery_tasks.add(task)
        task.add_done_callback(_delivery_tasks.discard)
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


RESOLVE_GRACE_S = 30.0
_PURGE_EVERY_S = 24 * 3600


class AlarmTracker:
    """Hooked into PrinterManager.on_state_change. Remembers the last settled report per printer and only touches the
    DB when it changes (a telemetry stream of identical frames costs nothing); serialises work per printer (vendor
    callbacks arrive concurrently from different threads); and re-checks after the resolve grace so an alarm that
    really cleared is resolved even if no further frame arrives."""

    def __init__(self) -> None:
        self._last: dict[int, tuple] = {}
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._timers: dict[int, asyncio.TimerHandle] = {}
        self._tasks: set[asyncio.Task] = set()
        self._last_purge = 0.0

    def forget(self, printer_id: int) -> None:
        self._last.pop(printer_id, None)
        timer = self._timers.pop(printer_id, None)
        if timer:
            timer.cancel()

    async def observe(self, session_factory, printer_id: int, alarms: list[Alarm], broadcast=None, refresh=None) -> None:
        """`refresh` re-reads the printer's current alarms for the grace re-check (defaults to repeating `alarms`)."""
        loop = asyncio.get_running_loop()
        async with self._locks.setdefault((id(loop), printer_id), asyncio.Lock()):
            sig = signature(alarms)
            if self._last.get(printer_id) == sig:
                return
            pending = False
            async with session_factory() as session:
                printer = await session.get(Printer, printer_id)
                if printer is None:
                    return
                announce = await reconcile(session, printer_id, alarms, RESOLVE_GRACE_S)
                pending = reconcile.last_pending                      # type: ignore[attr-defined]
                for row in announce:
                    await deliver(session, printer, row)
                await self._maybe_purge(session)
            if pending:
                self._last.pop(printer_id, None)                      # not settled: the next frame must look again
                self._schedule_recheck(loop, session_factory, printer_id, broadcast, refresh or (lambda: alarms))
            else:
                self._last[printer_id] = sig                          # only after a successful reconcile → DB failure retries
        if broadcast is not None:
            await broadcast("alarms_changed", {"printer_id": printer_id})

    def _schedule_recheck(self, loop, session_factory, printer_id, broadcast, refresh) -> None:
        old = self._timers.pop(printer_id, None)
        if old:
            old.cancel()

        def fire() -> None:
            self._timers.pop(printer_id, None)
            task = loop.create_task(self._recheck(session_factory, printer_id, broadcast, refresh))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        self._timers[printer_id] = loop.call_later(RESOLVE_GRACE_S + 1, fire)

    async def _recheck(self, session_factory, printer_id, broadcast, refresh) -> None:
        try:
            await self.observe(session_factory, printer_id, list(refresh()), broadcast, refresh)
        except Exception:
            logger.exception("Alarm re-check failed for printer %s", printer_id)

    async def _maybe_purge(self, session: AsyncSession) -> None:
        import time
        if time.monotonic() - self._last_purge < _PURGE_EVERY_S:
            return
        self._last_purge = time.monotonic()
        await purge_old(session)


tracker = AlarmTracker()
