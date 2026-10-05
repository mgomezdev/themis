"""Themis-side preset catalog: the cache, warm-up, refresh and rescan over the SlicingProvider.

All internal Themis code reads the catalog from here (`get_cached_catalog`) instead of contacting the
slicing provider, so the provider is only hit on boot and on explicit refresh/rescan requests.
Module-level state is intentionally process-wide (single-process app), like the printer manager."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid as _uuid

from sqlalchemy.ext.asyncio import AsyncSession

from .providers.slicing import Catalog, SlicingProviderError, get_slicing_provider

logger = logging.getLogger("app.laminus")

_catalog: Catalog | None = None        # parsed catalog for internal callers (printers, jobs, projects)
_catalog_bytes: bytes | None = None    # pre-serialised legacy JSON for the HTTP response
_catalog_fetched_at: float | None = None
# Holds {sync_id, raw, catalog, pending, created_at}. raw=None signals a Spoolman-only pending
# (no catalog swap on confirm).
_pending_sync: dict | None = None

# 30-second health memo to avoid hammering the provider on every catalog/status call.
_health_memo: dict | None = None
_health_memo_at: float = 0.0
_HEALTH_MEMO_TTL = 30.0


class CatalogUnavailable(Exception):
    """The catalog can't be fetched. `status` is the HTTP status a route should surface:
    503 = no slicing provider configured, 502 = provider unreachable / bad response, 504 = rescan timed out."""

    def __init__(self, detail: str, status: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


# ---------------------------------------------------------------------------
# Cache access
# ---------------------------------------------------------------------------

def cached_catalog() -> Catalog | None:
    """The cached catalog if loaded; never fetches."""
    return _catalog


def cached_raw() -> dict | None:
    """The cached catalog in its legacy JSON-dict shape; never fetches."""
    return _catalog.raw if _catalog is not None else None


def cached_bytes() -> bytes | None:
    return _catalog_bytes


def commit_catalog(raw: bytes, catalog: Catalog) -> None:
    """Write a fetched catalog to the cache."""
    global _catalog, _catalog_bytes, _catalog_fetched_at
    _catalog = catalog
    _catalog_bytes = raw
    _catalog_fetched_at = time.time()
    logger.info("Catalog cached: %d bytes", len(raw))


def remember_catalog(catalog: Catalog) -> None:
    """Cache a catalog fetched outside the HTTP flow (sync callers in the slice thread pool) without
    touching the serialized bytes / fetch timestamp."""
    global _catalog
    _catalog = catalog


def pending_sync() -> dict | None:
    return _pending_sync


def set_pending_sync(value: dict | None) -> None:
    global _pending_sync
    _pending_sync = value


async def fetch_catalog() -> tuple[bytes, Catalog]:
    """Pull the catalog from the slicing provider. No cache side effects."""
    provider = get_slicing_provider()
    if provider is None:
        raise CatalogUnavailable("Laminus sidecar not configured (LAMINUS_SIDECAR_URL not set)", 503)
    try:
        catalog = await asyncio.to_thread(provider.get_catalog)
    except SlicingProviderError as exc:
        raise CatalogUnavailable(f"Laminus sidecar unreachable: {exc}", 502) from exc
    return json.dumps(catalog.raw).encode(), catalog


async def fetch_and_cache() -> bytes:
    raw, catalog = await fetch_catalog()
    commit_catalog(raw, catalog)
    return raw


async def get_cached_catalog() -> Catalog:
    """The cached catalog, fetching from the provider only if it isn't loaded yet."""
    if _catalog is not None:
        return _catalog
    await fetch_and_cache()
    assert _catalog is not None
    return _catalog


async def warm() -> None:
    """Called at startup — polls until the provider's catalog is ready, then caches it."""
    if get_slicing_provider() is None:
        return
    deadline = time.time() + 300  # give up after 5 minutes
    while time.time() < deadline:
        try:
            await fetch_and_cache()
            return
        except Exception as exc:
            msg = str(exc)
            # An unreachable / still-building provider is transient (CatalogUnavailable 502/503, as the old
            # HTTPException("502: ...") text matched); anything else gives up.
            transient = (isinstance(exc, CatalogUnavailable) and exc.status in (502, 503)) or any(
                t in msg for t in ("503", "building_catalog", "502"))
            if transient:
                await asyncio.sleep(5)
                continue
            logger.warning("Startup catalog warm-up failed: %s", exc)
            return
    logger.warning("Startup catalog warm-up: laminus catalog not ready after 5 minutes")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

async def status() -> dict:
    """Cache + provider build state. The provider's health is memoized for 30 s."""
    global _health_memo, _health_memo_at
    provider = get_slicing_provider()
    laminus_status: dict | None = None
    state = "unconfigured"

    if provider is not None:
        now = time.time()
        if _health_memo is not None and now - _health_memo_at < _HEALTH_MEMO_TTL:
            h = _health_memo
        else:
            try:
                h = await asyncio.to_thread(provider.catalog_health, 5.0)
            except Exception:
                h = None
            _health_memo = h
            _health_memo_at = now

        if h is None:
            state = "offline"
        elif h.get("catalog_building"):
            state = "building"
        elif h.get("catalog_loaded"):
            state = "online"
        else:
            state = "offline"

        if h:
            laminus_status = {
                "catalog_loaded": h.get("catalog_loaded", False),
                "catalog_building": h.get("catalog_building", False),
                "profile_count": h.get("catalog_profile_count"),
            }

    catalog_counts = {
        "machine": len(_catalog.machines),
        "process": len(_catalog.processes),
        "filament": len(_catalog.filaments),
    } if _catalog else None

    return {
        "cached": _catalog_bytes is not None,
        "cached_bytes": len(_catalog_bytes) if _catalog_bytes else 0,
        "fetched_at": _catalog_fetched_at,
        "laminus_configured": provider is not None,
        "laminus": laminus_status,
        "catalog_counts": catalog_counts,
        "status": state,
    }


# ---------------------------------------------------------------------------
# Refresh / rescan (drift-gated)
# ---------------------------------------------------------------------------

async def _apply_drift_gate(raw: bytes, new_catalog: Catalog, session: AsyncSession) -> dict:
    """Check for drift, commit immediately or park pending. Returns the HTTP response dict."""
    global _pending_sync
    old_catalog = _catalog

    if old_catalog is None:
        # Cold cache — first sync ever, commit directly.
        commit_catalog(raw, new_catalog)
        return {"status": "ok", "bytes": len(raw)}

    from .catalog_utils import compute_drift
    drift = await compute_drift(old_catalog, new_catalog, session)

    if drift is None:
        commit_catalog(raw, new_catalog)
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


async def refresh(session: AsyncSession) -> dict:
    """Re-fetch the catalog. If removed presets are referenced by live data, returns pending_remaps
    instead of committing; the old catalog stays active until confirmed."""
    raw, new_catalog = await fetch_catalog()
    return await _apply_drift_gate(raw, new_catalog, session)


async def rescan(session: AsyncSession) -> dict:
    """Tell the provider to rebuild its catalog from disk, wait for it, then refresh like `refresh`."""
    provider = get_slicing_provider()
    if provider is None:
        raise CatalogUnavailable("Laminus sidecar not configured (LAMINUS_SIDECAR_URL not set)", 503)

    try:
        await asyncio.to_thread(provider.request_catalog_rebuild, 10.0)
    except SlicingProviderError as exc:
        raise CatalogUnavailable(str(exc), 502) from exc

    # Poll until the provider signals the rebuild is complete.
    deadline = time.time() + 120
    while time.time() < deadline:
        await asyncio.sleep(3)
        try:
            h = await asyncio.to_thread(provider.catalog_health, 5.0)
            if not h.get("catalog_building", True) and h.get("catalog_loaded"):
                break
        except Exception:
            pass
    else:
        raise CatalogUnavailable("Laminus catalog rebuild did not complete within 120 s", 504)

    raw, new_catalog = await fetch_catalog()
    return await _apply_drift_gate(raw, new_catalog, session)
