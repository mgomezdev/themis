from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ...config import get_laminus_sidecar_url
from ...services.laminus_sidecar_client import LaminusSidecarClient, SidecarError

logger = logging.getLogger("app.laminus")

router = APIRouter(prefix="/api/v1/laminus", tags=["laminus"])

# ---------------------------------------------------------------------------
# Module-level catalog store — all Themis code reads from here.
# Synced from Laminus on boot and on demand; never re-fetched per-request.
# ---------------------------------------------------------------------------
_catalog_dict: dict | None = None   # for internal callers (printers, jobs, projects)
_catalog_bytes: bytes | None = None  # pre-serialised for the HTTP response
_catalog_fetched_at: float | None = None


def _sidecar_client() -> tuple[str, LaminusSidecarClient]:
    url = get_laminus_sidecar_url()
    if not url:
        raise HTTPException(503, "Laminus sidecar not configured (LAMINUS_SIDECAR_URL not set)")
    return url, LaminusSidecarClient(url)


async def _fetch_and_cache() -> bytes:
    """Pull catalog from Laminus, populate module-level cache, return JSON bytes."""
    global _catalog_dict, _catalog_bytes, _catalog_fetched_at
    _, client = _sidecar_client()
    try:
        catalog = await asyncio.to_thread(client.get_catalog)
    except SidecarError as exc:
        raise HTTPException(502, f"Laminus sidecar unreachable: {exc}") from exc
    _catalog_dict = catalog
    _catalog_bytes = json.dumps(catalog).encode()
    _catalog_fetched_at = time.time()
    logger.info("Catalog cached: %d bytes", len(_catalog_bytes))
    return _catalog_bytes


async def get_cached_catalog() -> dict:
    """Return the Themis-side catalog dict, fetching from Laminus if not yet loaded.

    All internal Themis routes (printers, jobs, projects) call this instead of
    going directly to the Laminus sidecar, so Laminus is only contacted on boot and
    on explicit refresh/rescan requests.
    """
    global _catalog_dict
    if _catalog_dict is not None:
        return _catalog_dict
    await _fetch_and_cache()
    assert _catalog_dict is not None
    return _catalog_dict


async def warm_catalog_cache() -> None:
    """Called at startup — polls until Laminus's catalog is ready, then caches it."""
    url = get_laminus_sidecar_url()
    if not url:
        return
    deadline = time.time() + 300  # give up after 5 minutes
    while time.time() < deadline:
        try:
            await _fetch_and_cache()
            return
        except Exception as exc:
            msg = str(exc)
            if "503" in msg or "building_catalog" in msg or "502" in msg:
                await asyncio.sleep(5)
                continue
            logger.warning("Startup catalog warm-up failed: %s", exc)
            return
    logger.warning("Startup catalog warm-up: laminus catalog not ready after 5 minutes")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get(
    "/catalog",
    summary="Get profile catalog",
    responses={
        502: {"description": "Laminus sidecar unreachable"},
        503: {"description": "Laminus sidecar not configured"},
    },
)
async def get_laminus_catalog():
    """Return the full machine/process/filament catalog as JSON (served from the
    Themis-side cache; the sidecar is only contacted if the cache is cold)."""
    global _catalog_bytes
    if _catalog_bytes is not None:
        return Response(content=_catalog_bytes, media_type="application/json")
    data = await _fetch_and_cache()
    return Response(content=data, media_type="application/json")


@router.get("/catalog/status", summary="Catalog cache status")
async def get_catalog_status() -> dict:
    """Whether the Themis catalog cache is populated and Laminus's build state.
    Includes `laminus` sub-object with `catalog_loaded`, `catalog_building`, and
    `profile_count` if the sidecar is reachable."""
    url = get_laminus_sidecar_url()
    laminus_status: dict | None = None
    if url:
        try:
            r = await asyncio.to_thread(
                lambda: httpx.get(f"{url}/api/health", timeout=5)
            )
            if r.status_code == 200:
                h = r.json()
                laminus_status = {
                    "catalog_loaded": h.get("catalog_loaded", False),
                    "catalog_building": h.get("catalog_building", False),
                    "profile_count": h.get("catalog_profile_count"),
                }
        except Exception:
            pass
    return {
        "cached": _catalog_bytes is not None,
        "cached_bytes": len(_catalog_bytes) if _catalog_bytes else 0,
        "fetched_at": _catalog_fetched_at,
        "laminus_configured": url is not None,
        "laminus": laminus_status,
    }


@router.post(
    "/catalog/refresh",
    summary="Refresh catalog from Laminus",
    responses={
        502: {"description": "Laminus sidecar unreachable"},
        503: {"description": "Laminus sidecar not configured"},
    },
)
async def refresh_catalog() -> dict:
    """Re-fetch the catalog from Laminus and update the Themis cache.

    Use when Laminus already has fresh profiles (e.g., user uploaded a new preset
    via the Laminus API) and you want Themis to pick them up without a full rescan.
    """
    global _catalog_dict, _catalog_bytes
    _catalog_dict = None
    _catalog_bytes = None
    data = await _fetch_and_cache()
    return {"ok": True, "bytes": len(data)}


@router.post(
    "/catalog/rescan",
    summary="Rescan profiles and refresh catalog",
    responses={
        502: {"description": "Laminus sidecar unreachable"},
        503: {"description": "Laminus sidecar not configured"},
        504: {"description": "Laminus catalog rebuild did not complete within 120 s"},
    },
)
async def rescan_and_refresh_catalog() -> dict:
    """Tell Laminus to rebuild its catalog from disk, then update the Themis cache.

    Use after installing new OrcaSlicer profiles or adding user presets to disk.
    Laminus re-walks all profile directories and rebuilds inheritance; this can
    take up to 60 s on Windows WSL2 volumes. Themis polls until the rebuild
    completes before re-fetching.
    """
    url, _ = _sidecar_client()

    # Trigger Laminus rebuild (returns immediately; rebuild runs in background).
    try:
        r = await asyncio.to_thread(
            lambda: httpx.get(f"{url}/api/profiles?refresh=true", timeout=10)
        )
        if r.status_code not in (200, 503):
            raise HTTPException(502, f"Laminus rescan trigger returned {r.status_code}")
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not reach Laminus sidecar: {exc}") from exc

    # Poll until Laminus signals the rebuild is complete.
    deadline = time.time() + 120
    while time.time() < deadline:
        await asyncio.sleep(3)
        try:
            h = await asyncio.to_thread(
                lambda: httpx.get(f"{url}/api/health", timeout=5).json()
            )
            if not h.get("catalog_building", True) and h.get("catalog_loaded"):
                break
        except Exception:
            pass
    else:
        raise HTTPException(504, "Laminus catalog rebuild did not complete within 120 s")

    # Invalidate Themis cache and re-fetch.
    global _catalog_dict, _catalog_bytes
    _catalog_dict = None
    _catalog_bytes = None
    data = await _fetch_and_cache()
    return {"ok": True, "bytes": len(data)}
