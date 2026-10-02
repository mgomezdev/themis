from fastapi import APIRouter, Depends

from ...auth import require_scope
from ...services import camera_hub

router = APIRouter(prefix="/api/v1/cameras", tags=["cameras"])


@router.get(
    "/stats",
    summary="Camera proxy load",
    dependencies=[Depends(require_scope("printers:read"))],
)
async def camera_stats() -> dict:
    """Open shared upstream streams (viewers, frames, bytes in/out), the stream cap, and how many snapshot requests
    were answered from a live frame / cache / coalesced grab versus a real grab. Use it to size a camera wall."""
    return camera_hub.hub.stats()
