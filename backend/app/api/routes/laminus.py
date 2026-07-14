from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid as _uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_laminus_sidecar_url
from ...database import get_session
from ...models import SpoolmanConfig
from ...services.laminus_sidecar_client import LaminusSidecarClient, SidecarError


class ConfirmRemapBody(BaseModel):
    sync_id: str
    resolutions: dict  # {printers: [...], jobs: [...], spoolman_filaments: [...]}

logger = logging.getLogger("app.laminus")

router = APIRouter(prefix="/api/v1/laminus", tags=["laminus"])

# ---------------------------------------------------------------------------
# Module-level catalog store — all Themis code reads from here.
# Synced from Laminus on boot and on demand; never re-fetched per-request.
# ---------------------------------------------------------------------------
_catalog_dict: dict | None = None   # for internal callers (printers, jobs, projects)
_catalog_bytes: bytes | None = None  # pre-serialised for the HTTP response
_catalog_fetched_at: float | None = None
# Module-level pending-sync slot. Holds {sync_id, raw, catalog, pending, created_at}.
# raw=None signals a Spoolman-only pending (no catalog swap on confirm).
_pending_sync: dict | None = None

# 30-second health memo to avoid hammering Laminus on every catalog/status call.
_health_memo: dict | None = None
_health_memo_at: float = 0.0
_HEALTH_MEMO_TTL = 30.0


def _sidecar_client() -> tuple[str, LaminusSidecarClient]:
    url = get_laminus_sidecar_url()
    if not url:
        raise HTTPException(503, "Laminus sidecar not configured (LAMINUS_SIDECAR_URL not set)")
    return url, LaminusSidecarClient(url)


async def _fetch_catalog() -> tuple[bytes, dict]:
    """Pull catalog from Laminus sidecar. No module-level side effects."""
    _, client = _sidecar_client()
    try:
        catalog = await asyncio.to_thread(client.get_catalog)
    except SidecarError as exc:
        raise HTTPException(502, f"Laminus sidecar unreachable: {exc}") from exc
    raw = json.dumps(catalog).encode()
    return raw, catalog


def _commit_catalog(raw: bytes, parsed: dict) -> None:
    """Write the fetched catalog to the module-level cache."""
    global _catalog_dict, _catalog_bytes, _catalog_fetched_at
    _catalog_dict = parsed
    _catalog_bytes = raw
    _catalog_fetched_at = time.time()
    logger.info("Catalog cached: %d bytes", len(raw))


async def _fetch_and_cache() -> bytes:
    """Backward-compat: fetch + commit in one step (used by warm_catalog_cache)."""
    raw, catalog = await _fetch_catalog()
    _commit_catalog(raw, catalog)
    return raw


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
    `profile_count` if the sidecar is reachable. Health result is memoized for 30 s."""
    global _health_memo, _health_memo_at
    url = get_laminus_sidecar_url()
    laminus_status: dict | None = None
    status = "unconfigured"

    if url:
        now = time.time()
        if _health_memo is not None and now - _health_memo_at < _HEALTH_MEMO_TTL:
            h = _health_memo
        else:
            try:
                r = await asyncio.to_thread(
                    lambda: httpx.get(f"{url}/api/health", timeout=5)
                )
                if r.status_code == 200:
                    h = r.json()
                elif r.status_code == 503:
                    h = {"catalog_loaded": False, "catalog_building": True}
                else:
                    h = None
            except Exception:
                h = None
            _health_memo = h
            _health_memo_at = now

        if h is None:
            status = "offline"
        elif h.get("catalog_building"):
            status = "building"
        elif h.get("catalog_loaded"):
            status = "online"
        else:
            status = "offline"

        if h:
            laminus_status = {
                "catalog_loaded": h.get("catalog_loaded", False),
                "catalog_building": h.get("catalog_building", False),
                "profile_count": h.get("catalog_profile_count"),
            }

    catalog_counts = {
        "machine": len(_catalog_dict.get("machine", [])),
        "process": len(_catalog_dict.get("process", [])),
        "filament": len(_catalog_dict.get("filament", [])),
    } if _catalog_dict else None

    return {
        "cached": _catalog_bytes is not None,
        "cached_bytes": len(_catalog_bytes) if _catalog_bytes else 0,
        "fetched_at": _catalog_fetched_at,
        "laminus_configured": url is not None,
        "laminus": laminus_status,
        "catalog_counts": catalog_counts,
        "status": status,
    }


async def _apply_drift_gate(raw: bytes, new_catalog: dict, session: AsyncSession) -> dict:
    """Check for drift, commit immediately or park pending. Returns HTTP response dict."""
    global _pending_sync
    old_catalog = _catalog_dict

    if old_catalog is None:
        # Cold cache — first sync ever, commit directly.
        _commit_catalog(raw, new_catalog)
        return {"status": "ok", "bytes": len(raw)}

    from ...services.catalog_utils import compute_drift
    spoolman_cfg = await session.get(SpoolmanConfig, 1)
    drift = await compute_drift(old_catalog, new_catalog, session, spoolman_cfg)

    if drift is None:
        _commit_catalog(raw, new_catalog)
        return {"status": "ok", "bytes": len(raw)}

    sync_id = str(_uuid.uuid4())
    _pending_sync = {
        "sync_id": sync_id,
        "raw": raw,
        "catalog": new_catalog,
        "pending": drift["pending"],
        "created_at": time.time(),
    }
    return {
        "status": "pending_remaps",
        "sync_id": sync_id,
        **drift,
    }


@router.post(
    "/catalog/refresh",
    summary="Refresh catalog from Laminus",
    responses={
        502: {"description": "Laminus sidecar unreachable"},
        503: {"description": "Laminus sidecar not configured"},
    },
)
async def refresh_catalog(session: AsyncSession = Depends(get_session)) -> dict:
    """Re-fetch the catalog from Laminus. If removed profiles are referenced by live data,
    returns pending_remaps instead of committing. Old catalog remains active until confirmed."""
    raw, new_catalog = await _fetch_catalog()
    return await _apply_drift_gate(raw, new_catalog, session)


@router.post(
    "/catalog/rescan",
    summary="Rescan profiles and refresh catalog",
    responses={
        502: {"description": "Laminus sidecar unreachable"},
        503: {"description": "Laminus sidecar not configured"},
        504: {"description": "Laminus catalog rebuild did not complete within 120 s"},
    },
)
async def rescan_and_refresh_catalog(session: AsyncSession = Depends(get_session)) -> dict:
    """Tell Laminus to rebuild its catalog from disk, then update the Themis cache.
    If removed profiles are referenced by live data, returns pending_remaps."""
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

    raw, new_catalog = await _fetch_catalog()
    return await _apply_drift_gate(raw, new_catalog, session)


@router.post("/catalog/confirm-remap", summary="Confirm pending profile remap and commit catalog")
async def confirm_remap(
    body: ConfirmRemapBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    global _pending_sync

    if _pending_sync is None or _pending_sync["sync_id"] != body.sync_id:
        raise HTTPException(409, "Sync superseded or expired — re-run the catalog sync")

    pending = _pending_sync["pending"]
    resolutions = body.resolutions
    incoming_catalog = _pending_sync.get("catalog") or {}

    from ...services.catalog_utils import catalog_name_sets
    new_machines, new_processes, new_filaments, new_uuids = catalog_name_sets(incoming_catalog)

    # Build resolution lookup maps keyed by (field, stale_value)
    printer_res_map: dict[tuple[str, str], str | None] = {
        (r["field"], r["stale_value"]): r.get("new_value")
        for r in resolutions.get("printers", [])
    }
    job_res_map: dict[tuple[str, str], str | None] = {
        (r["field"], r["stale_value"]): r.get("new_value")
        for r in resolutions.get("jobs", [])
    }
    spoolman_res_map: dict[str, str | None] = {
        r["stale_uuid"]: r.get("new_uuid")
        for r in resolutions.get("spoolman_filaments", [])
    }

    # Validate all required printer entries have valid resolutions
    unresolved = []
    for entry in pending.get("printers", []):
        key = (entry["field"], entry["stale_value"])
        new_val = printer_res_map.get(key)
        if entry.get("required") and not new_val:
            unresolved.append(f"Printer {entry['field']}={entry['stale_value']}")
        elif new_val:
            valid_set = new_machines if entry.get("options_kind") == "machine" else new_filaments
            if new_val not in valid_set:
                unresolved.append(f"Invalid value '{new_val}' for {entry['field']}")

    if unresolved:
        raise HTTPException(422, {"detail": "Unresolved required remaps", "unresolved": unresolved})

    # Apply Printer updates
    from ...models import Printer as PrinterModel, JobPrinterConfig as JPC
    applied_printers = 0
    for entry in pending.get("printers", []):
        key = (entry["field"], entry["stale_value"])
        new_val = printer_res_map.get(key)
        for printer_id, slot in zip(entry["affected_printer_ids"], entry["affected_slots"]):
            printer = await session.get(PrinterModel, printer_id)
            if printer is None:
                continue
            if slot is None:
                printer.current_orca_printer_profile = new_val
                applied_printers += 1
            else:
                loaded = list(printer.loaded_filaments or [])
                if slot < len(loaded):
                    loaded[slot] = {**loaded[slot], "filament_profile": new_val}
                    printer.loaded_filaments = loaded
                    applied_printers += 1

    # Apply JobPrinterConfig updates
    applied_jobs = 0
    for entry in pending.get("jobs", []):
        key = (entry["field"], entry["stale_value"])
        new_val = job_res_map.get(key)
        for cfg_id in entry["affected_config_ids"]:
            cfg = await session.get(JPC, cfg_id)
            if cfg is None:
                continue
            if entry["field"] == "print_profile":
                cfg.print_profile = new_val or ""
            else:
                cfg.filament_profile = new_val
            applied_jobs += 1

    await session.commit()

    # Spoolman patches — best-effort after DB commit
    from ...services import spoolman_service as _spoolman
    spoolman_failures: list[str] = []
    applied_spoolman = 0
    if pending.get("spoolman_filaments"):
        spoolman_cfg = await session.get(SpoolmanConfig, 1)
        if spoolman_cfg and spoolman_cfg.url:
            for entry in pending["spoolman_filaments"]:
                new_uuid = spoolman_res_map.get(entry["stale_uuid"])
                stale_uuid = entry["stale_uuid"]
                for fil_id in entry["affected_filament_ids"]:
                    try:
                        headers = {}
                        if spoolman_cfg.api_key:
                            headers["X-API-Key"] = spoolman_cfg.api_key
                        r = await asyncio.to_thread(
                            lambda: httpx.get(
                                f"{spoolman_cfg.url.rstrip('/')}/api/v1/filament/{fil_id}",
                                headers=headers, timeout=10,
                            )
                        )
                        r.raise_for_status()
                        fil_data = r.json()
                        raw_extra = (fil_data.get("extra") or {}).get("orca_profiles", "null")
                        try:
                            profiles: dict = json.loads(json.loads(raw_extra))
                        except Exception:
                            profiles = {}
                        profiles.pop(stale_uuid, None)
                        if new_uuid:
                            cat_filaments = (incoming_catalog or {}).get("filament", [])
                            name = next(
                                (f["name"] for f in cat_filaments if f.get("uuid") == new_uuid),
                                new_uuid,
                            )
                            profiles[new_uuid] = name
                        await _spoolman.patch_filament(
                            spoolman_cfg.url, spoolman_cfg.api_key, fil_id, profiles
                        )
                        applied_spoolman += 1
                    except Exception as exc:
                        spoolman_failures.append(f"filament {fil_id}: {exc}")
                        logger.warning("Spoolman patch failed for filament %s: %s", fil_id, exc)

    # Commit catalog only when raw is not None (Spoolman-only pending skips this)
    if _pending_sync.get("raw") is not None:
        _commit_catalog(_pending_sync["raw"], _pending_sync["catalog"])

    _pending_sync = None
    return {
        "status": "ok",
        "applied": {
            "printers": applied_printers,
            "jobs": applied_jobs,
            "spoolman_filaments": applied_spoolman,
        },
        "spoolman_failures": spoolman_failures,
    }
