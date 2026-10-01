from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import Printer, PrinterAlarm, QueueConfig
from ...services import alarms as alarm_service
from ...services.abstract_printer_client import SEVERITIES

router = APIRouter(prefix="/api/v1/alarms", tags=["alarms"])


def _dict(a: PrinterAlarm, printer_name: str | None) -> dict:
    return {
        "id": a.id, "printer_id": a.printer_id, "printer_name": printer_name, "code": a.code, "severity": a.severity,
        "message": a.message, "source": a.source, "help_url": a.help_url, "first_seen": a.first_seen,
        "last_seen": a.last_seen, "resolved_at": a.resolved_at, "acknowledged_at": a.acknowledged_at,
        "active": a.resolved_at is None,
    }


@router.get("", summary="List printer alarms", dependencies=[Depends(require_scope("printers:read"))])
async def list_alarms(
    status: str = Query("active", pattern="^(active|unacknowledged|all)$"),
    printer_id: int | None = None,
    min_severity: str | None = Query(None, pattern="^(info|warning|error|fatal)$"),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Alarms newest first. `active` = still reported by the printer; `unacknowledged` = active and not yet
    acknowledged; `all` includes resolved history."""
    q = select(PrinterAlarm, Printer.name).join(Printer, Printer.id == PrinterAlarm.printer_id, isouter=True)
    if status in ("active", "unacknowledged"):
        q = q.where(PrinterAlarm.resolved_at.is_(None))
    if status == "unacknowledged":
        q = q.where(PrinterAlarm.acknowledged_at.is_(None))
    if printer_id is not None:
        q = q.where(PrinterAlarm.printer_id == printer_id)
    if min_severity:
        q = q.where(PrinterAlarm.severity.in_([s for s in SEVERITIES if alarm_service.rank(s) >= alarm_service.rank(min_severity)]))
    rows = (await session.execute(q.order_by(PrinterAlarm.first_seen.desc(), PrinterAlarm.id.desc()).limit(limit))).all()
    return [_dict(a, name) for a, name in rows]


@router.get("/summary", summary="Active alarm counts", dependencies=[Depends(require_scope("printers:read"))])
async def alarm_summary(session: AsyncSession = Depends(get_session)) -> dict:
    """Unacknowledged active alarms: total, worst severity, and per printer — what badges show."""
    rows = (await session.execute(
        select(PrinterAlarm.printer_id, PrinterAlarm.severity, func.count())
        .where(PrinterAlarm.resolved_at.is_(None), PrinterAlarm.acknowledged_at.is_(None))
        .group_by(PrinterAlarm.printer_id, PrinterAlarm.severity)
    )).all()
    per: dict[int, dict] = {}
    for pid, sev, n in rows:
        e = per.setdefault(pid, {"printer_id": pid, "count": 0, "worst": "info"})
        e["count"] += n
        if alarm_service.rank(sev) > alarm_service.rank(e["worst"]):
            e["worst"] = sev
    worst = max((e["worst"] for e in per.values()), key=alarm_service.rank, default=None)
    return {"count": sum(e["count"] for e in per.values()), "worst": worst,
            "printers": sorted(per.values(), key=lambda e: e["printer_id"])}


class AlarmSettings(BaseModel):
    min_severity: str


@router.get("/settings", summary="Alarm notification settings", dependencies=[Depends(require_scope("settings:read"))])
async def get_alarm_settings(session: AsyncSession = Depends(get_session)) -> dict:
    return {"min_severity": await alarm_service.min_severity(session), "severities": list(SEVERITIES)}


@router.put("/settings", summary="Update alarm notification settings", responses={422: {"description": "Unknown severity"}},
            dependencies=[Depends(require_scope("settings:write"))])
async def update_alarm_settings(body: AlarmSettings, session: AsyncSession = Depends(get_session)) -> dict:
    """Lowest severity that raises a `printer.alarm` webhook/notification. The feed itself always shows everything."""
    if body.min_severity not in SEVERITIES:
        raise HTTPException(422, f"min_severity must be one of {', '.join(SEVERITIES)}")
    cfg = await session.get(QueueConfig, 1)
    if cfg is None:
        cfg = QueueConfig(id=1)
        session.add(cfg)
    cfg.alarm_min_severity = body.min_severity
    await session.commit()
    return {"min_severity": body.min_severity, "severities": list(SEVERITIES)}


async def _get_or_404(alarm_id: int, session: AsyncSession) -> PrinterAlarm:
    a = await session.get(PrinterAlarm, alarm_id)
    if a is None:
        raise HTTPException(404, f"Alarm {alarm_id} not found")
    return a


@router.post("/{alarm_id}/acknowledge", summary="Acknowledge an alarm", responses={404: {"description": "Alarm not found"}},
             dependencies=[Depends(require_scope("printers:control"))])
async def acknowledge(alarm_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Silence an alarm in badges and the unacknowledged list. It stays active until the printer stops reporting it."""
    a = await _get_or_404(alarm_id, session)
    if a.acknowledged_at is None:
        a.acknowledged_at = datetime.now(timezone.utc).isoformat()
        await session.commit()
    printer = await session.get(Printer, a.printer_id)
    return _dict(a, printer.name if printer else None)


@router.post("/acknowledge-all", summary="Acknowledge every active alarm",
             dependencies=[Depends(require_scope("printers:control"))])
async def acknowledge_all(printer_id: int | None = None, session: AsyncSession = Depends(get_session)) -> dict:
    q = select(PrinterAlarm).where(PrinterAlarm.resolved_at.is_(None), PrinterAlarm.acknowledged_at.is_(None))
    if printer_id is not None:
        q = q.where(PrinterAlarm.printer_id == printer_id)
    rows = (await session.execute(q)).scalars().all()
    stamp = datetime.now(timezone.utc).isoformat()
    for a in rows:
        a.acknowledged_at = stamp
    await session.commit()
    return {"acknowledged": len(rows)}
