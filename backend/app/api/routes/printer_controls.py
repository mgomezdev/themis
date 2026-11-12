"""Live printer controls: reconnect, pause / resume / stop, light, fans, temperatures, jog and home.

Part of the `printers` routes: registered on the same `router` and imported at the bottom of printers.py.
What tests replace on that module (_LIST_TIMEOUT_S, _MAX_DIRECT_UPLOAD_BYTES, _discovery_lock, _discovery_network, _fetch_sidecar_catalog, _get_connected_client, _set_plate_gate, create_client, printer_manager, queue_engine) is read as `_printers.<name>` at call time, so one patch
point covers every printers route.
"""
from __future__ import annotations

import asyncio
from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ...auth import require_scope
from ...database import get_session
from ...models import Job, Printer
from ...services import printer_setup

from . import printers as _printers
from .printers import (  # noqa: I001
    BedTempBody,
    ChamberTempBody,
    FanBody,
    HomeBody,
    JogBody,
    JogZBody,
    LightBody,
    NozzleTempBody,
    _get_or_404,
    router,
)


@router.post(
    "/{printer_id}/reconnect",
    summary="Reconnect printer",
    responses={
        404: {"description": "Printer not found"},
        503: {"description": "Connection attempt failed"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def reconnect_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Disconnect the current session and open a fresh connection. 409 while a required connection field is missing (nothing is
    sent with a blank credential)."""
    printer = await _get_or_404(printer_id, session)
    missing = printer_setup.missing_required(printer_setup.fields_for(printer), printer.connection_config or {})
    if missing:
        raise HTTPException(409, printer_setup.missing_reason(missing))
    _printers.printer_manager.disconnect_printer(printer_id)
    try:
        client = _printers.create_client(printer)
        _printers.printer_manager.connect_printer(printer_id, client)
    except Exception as exc:
        raise HTTPException(503, f"Failed to connect: {exc}")
    return {"ok": True}


@router.post(
    "/{printer_id}/pause",
    summary="Pause print",
    responses={
        404: {"description": "Printer not found"},
        503: {"description": "Printer not connected"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def pause_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    client.pause_print()
    return {"ok": True}


@router.post(
    "/{printer_id}/resume",
    summary="Resume print",
    responses={
        404: {"description": "Printer not found"},
        503: {"description": "Printer not connected"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def resume_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    client.resume_print()
    return {"ok": True}


@router.post(
    "/{printer_id}/stop",
    summary="Stop print",
    responses={
        404: {"description": "Printer not found"},
        503: {"description": "Printer not connected"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def stop_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Stop the active print and cancel any Themis job the printer was physically running."""
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    client.stop_print()
    # Reconcile any Themis job this printer was physically running so it doesn't
    # stay stuck "printing" after the machine is stopped.
    from datetime import datetime, timezone
    result = await session.execute(
        select(Job).where(
            Job.assigned_printer_id == printer_id,
            Job.status.in_(["slicing", "printing", "paused", "uploading"]),
        )
    )
    now = datetime.now(timezone.utc).isoformat()
    printer = await session.get(Printer, printer_id)
    for job in result.scalars().all():
        if job.status in ("printing", "paused"):     # a started print: settle the attempt's usage (BIZ-366 v2)
            from ...services import completion_events
            await completion_events.enqueue_attempt_ended(session, job.id, printer_id, result="cancelled",
                                                          loaded_filaments=printer.loaded_filaments if printer else None)
        job.status = "cancelled"
        job.assigned_printer_id = None
        job.queue_position = None
        job.updated_at = now
    await session.commit()
    from ...eventing.hub import hub as _hub
    _hub.wake()                                  # deliver a `job.attempt_ended` written above
    _printers.queue_engine.cancel_slice(printer_id=printer_id)    # and a slice being made for it (cancel <-> stop, BIZ-295)
    return {"ok": True}


@router.post(
    "/{printer_id}/light",
    summary="Set chamber light",
    responses={
        404: {"description": "Printer not found"},
        503: {"description": "Printer not connected"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def set_light(
    printer_id: int,
    body: LightBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    client.set_chamber_light(body.on)
    return {"ok": True}


@router.post(
    "/{printer_id}/jog-z",
    summary="Jog Z axis",
    responses={
        404: {"description": "Printer not found"},
        503: {"description": "Printer not connected"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def jog_z(
    printer_id: int,
    body: JogZBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    client.jog_z(body.distance_mm)
    return {"ok": True}


@router.post(
    "/{printer_id}/fan",
    summary="Set fan speed",
    responses={
        404: {"description": "Printer not found"},
        422: {"description": "Invalid fan name"},
        503: {"description": "Printer not connected"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def set_fan(
    printer_id: int,
    body: FanBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Set fan speed (0–100 %) for `model`, `auxiliary`, or `box` fan."""
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    state = _printers.printer_manager.get_normalized_state(printer_id)
    model = int(state.get("fan_model", 0))
    aux = int(state.get("fan_aux", 0))
    box = int(state.get("fan_box", 0))
    if body.fan == "model":
        model = body.speed_pct
    elif body.fan == "auxiliary":
        aux = body.speed_pct
    elif body.fan == "box":
        box = body.speed_pct
    else:
        raise HTTPException(422, f"Invalid fan name: {body.fan!r}. Valid: model, auxiliary, box")
    client.set_fan_speeds(model, aux, box)
    return {"ok": True}


@router.post(
    "/{printer_id}/bed-temp",
    summary="Set bed temperature",
    responses={
        404: {"description": "Printer not found"},
        503: {"description": "Printer not connected"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def set_bed_temp(
    printer_id: int,
    body: BedTempBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    client.set_bed_temp(body.celsius)
    return {"ok": True}


def _require_capability(client, name: str, what: str) -> None:
    if not getattr(client.get_capabilities(), name, False):
        raise HTTPException(409, f"This printer does not support {what}")


def _require_not_printing(client) -> None:
    """Motion and nozzle changes need a printer that is positively idle (the queue's own notion), not merely
    "not printing": states like Bambu's PREPARE (heating/levelling) are neither."""
    if client.is_printing or not client.is_idle:
        raise HTTPException(409, "Printer is busy; wait for it to be idle (or stop the print) first")


def _ok_or_502(result) -> dict:
    if not result:
        raise HTTPException(502, "Printer rejected the command")
    return {"ok": True}


_CONSOLE_RESPONSES = {
    404: {"description": "Printer not found"},
    409: {"description": "Not supported by this printer, or the printer is printing"},
    502: {"description": "Printer rejected the command"},
    503: {"description": "Printer not connected"},
}


@router.post(
    "/{printer_id}/jog",
    summary="Jog an axis",
    responses=_CONSOLE_RESPONSES,
    dependencies=[Depends(require_scope("printers:control"))],
)
async def jog_axis(printer_id: int, body: JogBody, session: AsyncSession = Depends(get_session)) -> dict:
    """Relative move of X, Y or Z by `distance_mm` (negative = other direction). X/Y need the `axis_jog`
    capability; refused while a print is running."""
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    if body.axis != "Z":
        _require_capability(client, "axis_jog", "X/Y jog")
    _require_not_printing(client)
    if body.axis == "Z":      # vendors with a native Z command (Elegoo SDCP) override jog_z, not jog
        return _ok_or_502(await asyncio.to_thread(client.jog_z, body.distance_mm))
    return _ok_or_502(await asyncio.to_thread(client.jog, body.axis, body.distance_mm))


@router.post(
    "/{printer_id}/home",
    summary="Home axes",
    responses=_CONSOLE_RESPONSES,
    dependencies=[Depends(require_scope("printers:control"))],
)
async def home_printer(printer_id: int, body: HomeBody, session: AsyncSession = Depends(get_session)) -> dict:
    """Home all axes, or a single axis (needs the `home_axes` capability). Refused while printing."""
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    _require_not_printing(client)
    if body.axes == "all":
        return _ok_or_502(await asyncio.to_thread(client.home))
    _require_capability(client, "home_axes", "single-axis homing")
    return _ok_or_502(await asyncio.to_thread(client.home_axes, body.axes))


@router.post(
    "/{printer_id}/nozzle-temp",
    summary="Set nozzle temperature",
    responses=_CONSOLE_RESPONSES,
    dependencies=[Depends(require_scope("printers:control"))],
)
async def set_nozzle_temp(printer_id: int, body: NozzleTempBody, session: AsyncSession = Depends(get_session)) -> dict:
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    _require_capability(client, "nozzle_temp", "a nozzle setpoint")
    _require_not_printing(client)      # M104 mid-print would cool the nozzle and ruin the print
    return _ok_or_502(await asyncio.to_thread(client.set_nozzle_temp, body.celsius))


@router.post(
    "/{printer_id}/chamber-temp",
    summary="Set chamber temperature",
    responses=_CONSOLE_RESPONSES,
    dependencies=[Depends(require_scope("printers:control"))],
)
async def set_chamber_temp(printer_id: int, body: ChamberTempBody, session: AsyncSession = Depends(get_session)) -> dict:
    await _get_or_404(printer_id, session)
    client = _printers._get_connected_client(printer_id)
    _require_capability(client, "chamber_temp", "a chamber setpoint")
    return _ok_or_502(await asyncio.to_thread(client.set_chamber_temp, body.celsius))


_direct_start_locks: dict[tuple[int, int], asyncio.Lock] = {}     # keyed by (event loop, printer)
