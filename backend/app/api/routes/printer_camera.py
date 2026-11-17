"""A printer's camera: the MJPEG stream and single snapshots.

Part of the `printers` routes: registered on the same `router` and imported at the bottom of printers.py.
What tests replace on that module (_LIST_TIMEOUT_S, _MAX_DIRECT_UPLOAD_BYTES, _discovery_lock, _discovery_network, _fetch_sidecar_catalog, _get_connected_client, _set_plate_gate, create_client, printer_manager, queue_engine) is read as `_printers.<name>` at call time, so one patch
point covers every printers route.
"""
from __future__ import annotations

import asyncio
from fastapi import Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from ...auth import require_scope
from ...database import get_session
from ...services import camera_hub

from . import printers as _printers
from .printers import (  # noqa: I001
    _get_or_404,
    router,
)


async def _activate_camera(client) -> None:
    """Enable the camera stream; runs the blocking call off the event loop."""
    if hasattr(client, "start_video_stream"):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, client.start_video_stream)


@router.get(
    "/{printer_id}/camera",
    summary="Stream camera (MJPEG)",
    responses={
        404: {"description": "Printer not found or has no camera"},
        503: {"description": "Printer not connected or its camera is unavailable (e.g. a transcoder the vendor needs is missing)"},
    },
    dependencies=[Depends(require_scope("printers:read"))],
)
async def stream_camera(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """MJPEG multipart stream from the printer camera. The printer's client produces the feed (native
    MJPEG, or a vendor transcode such as Bambu's RTSP). A keepalive ping prevents stream drops on
    printers that time out after 60 s of inactivity."""
    await _get_or_404(printer_id, session)
    client = _printers.printer_manager._clients.get(printer_id)
    if client is None or not client.connected:
        raise HTTPException(503, "Printer not connected")
    caps = client.get_capabilities()
    if not caps.camera:
        raise HTTPException(404, "This printer has no camera")

    if not client.camera_configured:
        raise HTTPException(404, "No camera URL configured")
    reason = client.camera_unavailable_reason()          # e.g. a transcoder the vendor needs is missing
    if reason:
        raise HTTPException(503, reason)

    if not camera_hub.hub.has_stream(printer_id):
        await _activate_camera(client)                 # before the response starts, so a failure is a real 5xx

    async def upstream():
        async for chunk in client.camera_stream():       # the client (its plugin) produces the feed; core fans it out
            yield chunk

    # One upstream connection / transcoder per printer however many viewers (camera wall, several browsers);
    # Elegoo drops the MJPEG stream after 60 s of silence, so the hub pings it every 45 s.
    ping = client.ping_video_stream if hasattr(client, "ping_video_stream") else None
    if camera_hub.hub.is_full_for(printer_id):
        raise HTTPException(429, "Too many camera streams open")

    async def _stream():
        frames = camera_hub.hub.subscribe(printer_id, upstream, ping)
        try:
            async for part in frames:
                yield part
        except camera_hub.HubFull:
            return
        finally:
            await frames.aclose()

    return StreamingResponse(
        _stream(),
        media_type=f"multipart/x-mixed-replace; boundary={camera_hub.BOUNDARY}",
    )


@router.get(
    "/{printer_id}/snapshot",
    summary="Camera snapshot",
    responses={
        404: {"description": "Printer not found, has no camera, or no camera source"},
        503: {"description": "Printer not connected or camera unavailable"},
    },
    dependencies=[Depends(require_scope("printers:read"))],
)
async def snapshot_camera(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Return a single JPEG frame from any camera source (MJPEG or RTSP)."""
    await _get_or_404(printer_id, session)
    client = _printers.printer_manager._clients.get(printer_id)
    if client is None or not client.connected:
        raise HTTPException(503, "Printer not connected")
    caps = client.get_capabilities()
    if not caps.camera:
        raise HTTPException(404, "This printer has no camera")

    async def grab():
        await _activate_camera(client)           # only when a real grab happens, not on a cache/live-frame hit
        return await client.camera_snapshot()

    try:
        jpeg = await camera_hub.hub.snapshot(printer_id, grab)
    except Exception as exc:
        raise HTTPException(503, f"Camera unavailable: {exc}")

    if jpeg is None:
        raise HTTPException(404, "No camera source available")

    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )
