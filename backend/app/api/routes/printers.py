from __future__ import annotations

import asyncio
import socket
import time as _time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import shutil

from ...database import get_session
from ...models import Printer
from ...services.camera_proxy import grab_jpeg_frame, grab_snapshot_from_client, stream_mjpeg, stream_rtsp_ffmpeg
from ...services.printer_client_factory import REGISTRY, get_printer_types_for_ui, create_client_from_config, create_client
from ...services.printer_manager import printer_manager
from ...services.queue_engine import queue_engine

async def _fetch_sidecar_catalog() -> dict | None:
    """Return the Themis-side catalog cache (never calls Orca directly)."""
    from .orca import get_cached_catalog
    try:
        return await get_cached_catalog()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Could not get catalog: %s", e)
        return None

router = APIRouter(prefix="/api/v1/printers", tags=["printers"])


class PrinterCreate(BaseModel):
    name: str
    printer_type: str
    connection_config: dict
    orca_printer_profiles: list[str] = []
    current_orca_printer_profile: str | None = None
    loaded_filaments: list[dict] = []
    build_plate_type: str | None = None
    no_snapshots_while_idle: bool = False


class PrinterUpdate(BaseModel):
    name: str | None = None
    connection_config: dict | None = None
    orca_printer_profiles: list[str] | None = None
    current_orca_printer_profile: str | None = None
    enabled: bool | None = None
    queue_on: bool | None = None
    loaded_filaments: list[dict] | None = None
    build_plate_type: str | None = None
    no_snapshots_while_idle: bool | None = None


class ActivePresetUpdate(BaseModel):
    preset: str


class LightBody(BaseModel):
    on: bool


class JogZBody(BaseModel):
    distance_mm: float


class FanBody(BaseModel):
    fan: str  # "model" | "auxiliary" | "box"
    speed_pct: int


class BedTempBody(BaseModel):
    celsius: int


def _to_dict(p: Printer) -> dict:
    live_client = printer_manager._clients.get(p.id)
    return {
        "id": p.id,
        "name": p.name,
        "printer_type": p.printer_type,
        "connection_config": p.connection_config,
        "awaiting_plate_clear": p.awaiting_plate_clear,
        "orca_printer_profiles": p.orca_printer_profiles,
        "current_orca_printer_profile": p.current_orca_printer_profile,
        "enabled": p.enabled,
        "queue_on": p.queue_on,
        "loaded_filaments": p.loaded_filaments or [],
        "build_plate_type": p.build_plate_type,
        "no_snapshots_while_idle": p.no_snapshots_while_idle,
        "connected": live_client.connected if live_client else False,
    }


async def _get_or_404(printer_id: int, session: AsyncSession) -> Printer:
    printer = await session.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(404, f"Printer {printer_id} not found")
    return printer


def _get_connected_client(printer_id: int):
    client = printer_manager._clients.get(printer_id)
    if client is None or not client.connected:
        raise HTTPException(503, "Printer not connected")
    return client


@router.get("/types")
async def list_printer_types() -> list[dict]:
    return get_printer_types_for_ui()


@router.get("")
async def list_printers(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(select(Printer))
    return [_to_dict(p) for p in result.scalars().all()]


@router.post("", status_code=201)
async def create_printer(
    body: PrinterCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if body.printer_type not in REGISTRY:
        raise HTTPException(422, f"Unknown printer_type: {body.printer_type!r}. Valid types: {list(REGISTRY.keys())}")
    printer = Printer(
        name=body.name,
        printer_type=body.printer_type,
        connection_config=body.connection_config,
        orca_printer_profiles=body.orca_printer_profiles,
        current_orca_printer_profile=body.current_orca_printer_profile,
        loaded_filaments=body.loaded_filaments,
        build_plate_type=body.build_plate_type,
        no_snapshots_while_idle=body.no_snapshots_while_idle,
    )
    session.add(printer)
    await session.commit()
    await session.refresh(printer)
    try:
        client = create_client(printer)
        printer_manager.connect_printer(printer.id, client)
    except Exception:
        pass  # non-fatal: printer saved, connection will retry on next restart
    return _to_dict(printer)


@router.get("/orca-presets")
async def list_orca_printer_presets() -> list[str]:
    loop = asyncio.get_running_loop()
    cat = await _fetch_sidecar_catalog()
    if cat is None:
        return []
    return sorted({m["name"] for m in cat.get("machine", []) if m.get("name")})


@router.get("/orca-machine-catalog")
async def orca_machine_catalog() -> list[dict]:
    """Selectable OrcaSlicer machine presets [{name, vendor, printer_model, nozzle,
    source, uuid}]. Sourced exclusively from the Orca sidecar."""
    loop = asyncio.get_running_loop()
    cat = await _fetch_sidecar_catalog()
    if cat is None:
        return []
    return sorted(
        [
            {
                "name": m["name"],
                "vendor": m.get("manufacturer") or "",
                "printer_model": m.get("model") or "",
                "nozzle": m.get("nozzle") or "",
                "source": "system",
                "uuid": m.get("uuid") or "",
            }
            for m in cat.get("machine", [])
            if m.get("name") and m.get("model") and m.get("nozzle")
        ],
        key=lambda m: (m["vendor"], m["printer_model"], m["nozzle"], m["name"]),
    )


@router.post("/rescan-profiles")
async def rescan_profiles() -> dict:
    """Trigger a catalog refresh from Orca and report the machine preset count."""
    from .orca import refresh_catalog as _orca_refresh
    await _orca_refresh()
    cat = await _fetch_sidecar_catalog()
    if cat is None:
        return {"machine_presets": 0}
    count = sum(1 for m in cat.get("machine", []) if m.get("model") and m.get("nozzle"))
    return {"machine_presets": count}


class TestConnectionRequest(BaseModel):
    printer_type: str
    connection_config: dict


_TEST_CONNECT_POLL_S = 15.0  # MQTT/TLS handshake + first report can take well over 5s


async def _connect_failure_hint(client) -> str:
    """Classify a failed test connection by probing the control port, so the UI
    can say *why* (unreachable vs reached-but-login-failed) instead of a bare
    'Could not connect'."""
    try:
        endpoint = client.control_endpoint()
    except Exception:
        endpoint = None
    if not endpoint:
        return "Could not connect."
    host, port = endpoint

    def _probe() -> bool:
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except Exception:
            return False

    reachable = await asyncio.get_running_loop().run_in_executor(None, _probe)
    if reachable:
        return (f"Reached {host}:{port} but the login didn't complete — check the access code / "
                f"serial number, or the printer's single LAN connection is busy.")
    return (f"Couldn't reach {host}:{port}. The printer may be off or asleep, the IP may be wrong, "
            f"or another app is holding the printer's single LAN connection (Bambu allows only one).")


@router.post("/test-connection")
async def test_connection(body: TestConnectionRequest) -> dict:
    if body.printer_type not in REGISTRY:
        raise HTTPException(422, f"Unknown printer_type: {body.printer_type!r}")
    client = None
    try:
        client = create_client_from_config(body.printer_type, body.connection_config)
        client.connect()
        deadline = asyncio.get_running_loop().time() + _TEST_CONNECT_POLL_S
        while asyncio.get_running_loop().time() < deadline and not client.connected:
            await asyncio.sleep(0.5)
        if client.connected:
            return {"ok": True}
        return {"ok": False, "error": await _connect_failure_hint(client)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass


@router.get("/{printer_id}/profiles")
async def get_profiles(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    printer = await _get_or_404(printer_id, session)
    if not printer.current_orca_printer_profile:
        return {"print_profiles": [], "filament_profiles": []}
    machine_name = printer.current_orca_printer_profile

    loop = asyncio.get_running_loop()
    cat = await _fetch_sidecar_catalog()
    if cat is None:
        return {"print_profiles": [], "filament_profiles": []}

    processes = sorted(
        p["name"] for p in cat.get("process", [])
        if machine_name in (p.get("compatible_printers") or [])
    )
    filaments = sorted(
        f["name"] for f in cat.get("filament", [])
        if machine_name in (f.get("compatible_printers") or [])
    )
    return {"print_profiles": processes, "filament_profiles": filaments}


@router.get("/{printer_id}")
async def get_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return _to_dict(await _get_or_404(printer_id, session))


@router.patch("/{printer_id}")
async def update_printer(
    printer_id: int,
    body: PrinterUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    printer = await _get_or_404(printer_id, session)
    if body.name is not None:
        printer.name = body.name
    if body.connection_config is not None:
        printer.connection_config = body.connection_config
    if body.orca_printer_profiles is not None:
        printer.orca_printer_profiles = body.orca_printer_profiles
    # Use model_fields_set so an explicit null clears the preset (EditForm sends
    # null to unset make/model); an omitted key leaves it unchanged.
    if "current_orca_printer_profile" in body.model_fields_set:
        printer.current_orca_printer_profile = body.current_orca_printer_profile
    if body.enabled is not None:
        printer.enabled = body.enabled
    if body.queue_on is not None:
        printer.queue_on = body.queue_on
    if body.loaded_filaments is not None:
        printer.loaded_filaments = body.loaded_filaments
    if "build_plate_type" in body.model_fields_set:
        printer.build_plate_type = body.build_plate_type
    if body.no_snapshots_while_idle is not None:
        printer.no_snapshots_while_idle = body.no_snapshots_while_idle
    await session.commit()
    await session.refresh(printer)
    return _to_dict(printer)


@router.delete("/{printer_id}", status_code=204)
async def delete_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    printer = await _get_or_404(printer_id, session)
    await session.delete(printer)
    await session.commit()


@router.post("/{printer_id}/plate-cleared")
async def plate_cleared(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    printer = await _get_or_404(printer_id, session)
    printer.awaiting_plate_clear = False
    await session.commit()
    printer_manager.set_awaiting_plate_clear(printer_id, False)
    queue_engine.wake()
    return {"ok": True}


@router.patch("/{printer_id}/active-preset")
async def switch_active_preset(
    printer_id: int,
    body: ActivePresetUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    printer = await _get_or_404(printer_id, session)
    if body.preset not in (printer.orca_printer_profiles or []):
        raise HTTPException(422, f"Preset {body.preset!r} not in this printer's configured profiles")
    printer.current_orca_printer_profile = body.preset
    await session.commit()
    await session.refresh(printer)
    return _to_dict(printer)


@router.post("/{printer_id}/reconnect")
async def reconnect_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    printer = await _get_or_404(printer_id, session)
    printer_manager.disconnect_printer(printer_id)
    try:
        client = create_client(printer)
        printer_manager.connect_printer(printer_id, client)
    except Exception as exc:
        raise HTTPException(503, f"Failed to connect: {exc}")
    return {"ok": True}


@router.post("/{printer_id}/pause")
async def pause_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.pause_print()
    return {"ok": True}


@router.post("/{printer_id}/resume")
async def resume_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.resume_print()
    return {"ok": True}


@router.post("/{printer_id}/stop")
async def stop_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.stop_print()
    # Reconcile any Themis job this printer was physically running so it doesn't
    # stay stuck "printing" after the machine is stopped.
    from datetime import datetime, timezone
    from ...models import Job
    result = await session.execute(
        select(Job).where(
            Job.assigned_printer_id == printer_id,
            Job.status.in_(["printing", "paused", "uploading"]),
        )
    )
    now = datetime.now(timezone.utc).isoformat()
    for job in result.scalars().all():
        job.status = "cancelled"
        job.assigned_printer_id = None
        job.queue_position = None
        job.updated_at = now
    await session.commit()
    return {"ok": True}


@router.post("/{printer_id}/light")
async def set_light(
    printer_id: int,
    body: LightBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.set_chamber_light(body.on)
    return {"ok": True}


@router.post("/{printer_id}/jog-z")
async def jog_z(
    printer_id: int,
    body: JogZBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.jog_z(body.distance_mm)
    return {"ok": True}


@router.post("/{printer_id}/fan")
async def set_fan(
    printer_id: int,
    body: FanBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    state = printer_manager.get_normalized_state(printer_id)
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


@router.post("/{printer_id}/bed-temp")
async def set_bed_temp(
    printer_id: int,
    body: BedTempBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.set_bed_temp(body.celsius)
    return {"ok": True}


async def _activate_camera(client) -> None:
    """Enable the camera stream; runs the blocking call off the event loop."""
    if hasattr(client, "start_video_stream"):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, client.start_video_stream)


@router.get("/{printer_id}/camera")
async def stream_camera(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    await _get_or_404(printer_id, session)
    client = printer_manager._clients.get(printer_id)
    if client is None or not client.connected:
        raise HTTPException(503, "Printer not connected")
    caps = client.get_capabilities()
    if not caps.camera:
        raise HTTPException(404, "This printer has no camera")

    await _activate_camera(client)

    if client.camera_mjpeg_url:
        raw = stream_mjpeg(client.camera_mjpeg_url)
    elif client.camera_rtsp_url:
        from ...config import get_ffmpeg_executable
        if not shutil.which(get_ffmpeg_executable()):
            raise HTTPException(503, "ffmpeg not available for RTSP streaming")
        raw = stream_rtsp_ffmpeg(client.camera_rtsp_url)
    else:
        raise HTTPException(404, "No camera URL configured")

    # Ping keepalive: Elegoo drops the MJPEG stream after 60 s of silence; ping every 45 s.
    stop = asyncio.Event()

    async def _ping_loop():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=45)
            except asyncio.TimeoutError:
                if hasattr(client, "ping_video_stream"):
                    client.ping_video_stream()

    ping_task = asyncio.create_task(_ping_loop())

    async def _stream():
        try:
            async for chunk in raw:
                yield chunk
        finally:
            stop.set()
            ping_task.cancel()

    return StreamingResponse(
        _stream(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/{printer_id}/snapshot")
async def snapshot_camera(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Return a single JPEG frame from any camera source (MJPEG or RTSP)."""
    await _get_or_404(printer_id, session)
    client = printer_manager._clients.get(printer_id)
    if client is None or not client.connected:
        raise HTTPException(503, "Printer not connected")
    caps = client.get_capabilities()
    if not caps.camera:
        raise HTTPException(404, "This printer has no camera")

    await _activate_camera(client)

    try:
        jpeg = await grab_snapshot_from_client(client)
    except Exception as exc:
        raise HTTPException(503, f"Camera unavailable: {exc}")

    if jpeg is None:
        raise HTTPException(404, "No camera source available")

    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )
