from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import shutil

from ...database import get_session
from ...models import Printer
from ...services.camera_proxy import grab_jpeg_frame, stream_mjpeg, stream_rtsp_ffmpeg
from ...services.printer_client_factory import REGISTRY, get_printer_types_for_ui, create_client_from_config, create_client
from ...services.printer_manager import printer_manager
from ...config import get_bambu_config_dir, get_orca_config_dir
from ...services.preset_resolver import PresetResolver
from ...services.profile_index import ProfileIndex
from ...services.profile_service import ProfileService
from ...services.queue_engine import queue_engine

# Shared, cached compatibility indexes per slicer (rebuild when presets change on disk).
_profile_indexes: dict[str, ProfileIndex] = {
    "orca": ProfileIndex(),
    "bambu": ProfileIndex(PresetResolver(str(get_bambu_config_dir()))),
}


def _get_profile_index(slicer: str) -> ProfileIndex:
    return _profile_indexes.get(slicer) or _profile_indexes["orca"]

router = APIRouter(prefix="/api/v1/printers", tags=["printers"])


class PrinterCreate(BaseModel):
    name: str
    printer_type: str
    connection_config: dict
    slicer: str = "orca"
    orca_printer_profiles: list[str] = []
    current_orca_printer_profile: str | None = None
    loaded_filaments: list[dict] = []


class PrinterUpdate(BaseModel):
    name: str | None = None
    connection_config: dict | None = None
    slicer: str | None = None
    orca_printer_profiles: list[str] | None = None
    current_orca_printer_profile: str | None = None
    enabled: bool | None = None
    queue_on: bool | None = None
    loaded_filaments: list[dict] | None = None


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
        "slicer": p.slicer or "orca",
        "orca_printer_profiles": p.orca_printer_profiles,
        "current_orca_printer_profile": p.current_orca_printer_profile,
        "enabled": p.enabled,
        "queue_on": p.queue_on,
        "loaded_filaments": p.loaded_filaments or [],
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
        slicer=body.slicer,
        orca_printer_profiles=body.orca_printer_profiles,
        current_orca_printer_profile=body.current_orca_printer_profile,
        loaded_filaments=body.loaded_filaments,
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
    svc = ProfileService()
    return svc.get_printer_preset_names()


@router.get("/orca-machine-catalog")
async def orca_machine_catalog() -> list[dict]:
    """Real selectable OrcaSlicer machine presets [{name, vendor, printer_model,
    nozzle, source}] for the printer-settings make/model/nozzle picker."""
    return _profile_indexes["orca"].machine_catalog()


@router.get("/bambu-machine-catalog")
async def bambu_machine_catalog() -> list[dict]:
    """Real selectable BambuStudio machine presets [{name, vendor, printer_model,
    nozzle, source}] for the printer-settings make/model/nozzle picker."""
    return _profile_indexes["bambu"].machine_catalog()


@router.post("/rescan-profiles")
async def rescan_profiles() -> dict:
    """Drop both cached profile indexes and rebuild from disk — use after adding
    or editing slicer presets so new options appear."""
    for idx in _profile_indexes.values():
        idx.refresh()
    orca_count = len(_profile_indexes["orca"].machine_catalog())
    bambu_count = len(_profile_indexes["bambu"].machine_catalog())
    return {"orca_machine_presets": orca_count, "bambu_machine_presets": bambu_count}


class TestConnectionRequest(BaseModel):
    printer_type: str
    connection_config: dict


@router.post("/test-connection")
async def test_connection(body: TestConnectionRequest) -> dict:
    if body.printer_type not in REGISTRY:
        raise HTTPException(422, f"Unknown printer_type: {body.printer_type!r}")
    client = None
    try:
        client = create_client_from_config(body.printer_type, body.connection_config)
        client.connect()
        await asyncio.sleep(5)
        ok = client.connected
        if ok:
            return {"ok": True}
        return {"ok": False, "error": "Could not connect"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass


_SAMPLE_PROFILES = {
    "print_profiles": [
        "0.20mm Standard @ECC",
        "0.16mm Fine @ECC",
        "0.28mm Draft @ECC",
    ],
    "filament_profiles": [
        "Elegoo PLA Basic @ECC",
        "Elegoo PLA+ @ECC",
        "Elegoo PETG @ECC",
        "Elegoo ABS @ECC",
        "Generic PLA @ECC",
    ],
}


@router.get("/{printer_id}/profiles")
async def get_profiles(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    printer = await _get_or_404(printer_id, session)
    if not printer.current_orca_printer_profile:
        return {"print_profiles": [], "filament_profiles": []}
    slicer = printer.slicer or "orca"
    idx = _get_profile_index(slicer)
    result = idx.compatible_profiles(printer.current_orca_printer_profile)
    if not result["print_profiles"] and not result["filament_profiles"]:
        config_dir = get_bambu_config_dir() if slicer == "bambu" else get_orca_config_dir()
        if not config_dir.exists():
            return _SAMPLE_PROFILES
    return result


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
    if body.slicer is not None:
        printer.slicer = body.slicer
    if body.orca_printer_profiles is not None:
        printer.orca_printer_profiles = body.orca_printer_profiles
    if body.current_orca_printer_profile is not None:
        printer.current_orca_printer_profile = body.current_orca_printer_profile
    if body.enabled is not None:
        printer.enabled = body.enabled
    if body.queue_on is not None:
        printer.queue_on = body.queue_on
    if body.loaded_filaments is not None:
        printer.loaded_filaments = body.loaded_filaments
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
    """Return a single JPEG frame — for browsers that don't support MJPEG streaming."""
    await _get_or_404(printer_id, session)
    client = printer_manager._clients.get(printer_id)
    if client is None or not client.connected:
        raise HTTPException(503, "Printer not connected")
    caps = client.get_capabilities()
    if not caps.camera:
        raise HTTPException(404, "This printer has no camera")

    await _activate_camera(client)

    url = client.camera_mjpeg_url
    if not url:
        raise HTTPException(404, "No camera URL configured")

    try:
        jpeg = await grab_jpeg_frame(url)
    except Exception as exc:
        raise HTTPException(503, f"Camera unavailable: {exc}")

    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )
