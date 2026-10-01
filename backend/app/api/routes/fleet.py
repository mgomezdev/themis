from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import Printer
from ...services import fleet_analytics
from ...services.printer_manager import printer_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"], dependencies=[Depends(require_scope("fleet:read"))])

_OFFLINE_STATE: dict[str, object] = {
    "connected": False,
    "state": "unknown",
    "progress": 0,
    "remaining_time": 0,
    "layer_num": None,
    "total_layers": None,
    "temperatures": {},
    "capabilities": {},
    "current_print": None,
}


def _fleet_dict(p: Printer) -> dict:
    base = {
        "id": p.id,
        "name": p.name,
        "printer_type": p.printer_type,
        "enabled": p.enabled,
        "queue_on": p.queue_on,
        "awaiting_plate_clear": p.awaiting_plate_clear,
        "no_snapshots_while_idle": p.no_snapshots_while_idle,
        "loaded_filaments": p.loaded_filaments or [],
    }
    client = printer_manager._clients.get(p.id)
    if client and client.connected:
        try:
            live = printer_manager.get_normalized_state(p.id)
        except Exception:
            logger.exception("Failed to get normalized state for printer %s", p.id)
            live = dict(_OFFLINE_STATE)
    else:
        live = dict(_OFFLINE_STATE)
    live.pop("awaiting_plate_clear", None)
    return {**base, **live}


@router.get("", summary="Fleet live state")
async def list_fleet(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """All printers with live telemetry (temperatures, progress, print state) merged in.
    Offline or disconnected printers return a zeroed-out state block."""
    result = await session.execute(select(Printer))
    return [_fleet_dict(p) for p in result.scalars().all()]


@router.get(
    "/analytics",
    summary="Fleet analytics over a date range",
    responses={422: {"description": "Inverted or over-long date range"}},
)
async def fleet_analytics_summary(
    start: Optional[date] = Query(None, description="First day (UTC), inclusive. Default: 29 days before `end`."),
    end: Optional[date] = Query(None, description="Last day (UTC), inclusive. Default: today (UTC)."),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Success rate, print time, filament use and cost — fleet-wide, per printer and per material — for jobs
    that reached a terminal state in the range. Definitions: `app/services/fleet_analytics.py`."""
    end = end or datetime.now(timezone.utc).date()
    start = start or end - timedelta(days=29)
    try:
        return await fleet_analytics.compute(session, start, end)
    except ValueError as e:
        raise HTTPException(422, str(e))
