from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from ...config import get_orca_sidecar_url
from ...services.orca_sidecar_client import OrcaSidecarClient, SidecarError

router = APIRouter(prefix="/api/v1/orca", tags=["orca"])


@router.get("/catalog")
async def get_orca_catalog() -> dict:
    """Proxy the Orca sidecar profile catalog for frontend consumption.

    Returns the full machine / process / filament catalog. The frontend uses
    this to populate machine, process, and filament pickers without needing
    to know the sidecar URL or deal with CORS.
    """
    url = get_orca_sidecar_url()
    if not url:
        raise HTTPException(503, "Orca sidecar not configured (ORCA_SIDECAR_URL not set)")
    client = OrcaSidecarClient(url)
    try:
        catalog = await asyncio.to_thread(client.get_catalog)
    except SidecarError as exc:
        raise HTTPException(502, f"Orca sidecar unreachable: {exc}") from exc
    return catalog
