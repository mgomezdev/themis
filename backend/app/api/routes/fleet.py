from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Printer
from ...services.printer_manager import printer_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"])

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


@router.get("")
async def list_fleet(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(select(Printer))
    return [_fleet_dict(p) for p in result.scalars().all()]
