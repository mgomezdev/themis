"""Last-known cache of a REMOTE provider's spools/materials (`inventory_cache`). Written after every successful list, read
when the provider is unreachable (so the UI shows stale data and a restart during an outage still works). Per provider, so
switching providers never serves another's data. Non-REMOTE providers are never cached (their reads cannot fail)."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, fields
from datetime import datetime, timezone

from sqlalchemy import delete

from ...models import InventoryCache
from ...plugins.host import plugin_host
from ...plugins.capabilities.filament_inventory import REMOTE, InvMaterial, InvSpool
from . import provider

logger = logging.getLogger("app")

SPOOLS, MATERIALS = "spools", "materials"


def _dump(item) -> dict:
    d = asdict(item)
    d.pop("raw", None)
    return d


def _material(d: dict | None) -> InvMaterial | None:
    if d is None:
        return None
    known = {f.name for f in fields(InvMaterial)} - {"raw"}
    return InvMaterial(**{k: v for k, v in d.items() if k in known})


def _spool(d: dict) -> InvSpool:
    known = {f.name for f in fields(InvSpool)} - {"raw", "material"}
    return InvSpool(**{k: v for k, v in d.items() if k in known}, material=_material(d.get("material")))


def cacheable() -> bool:
    return provider.has(REMOTE)


async def store(provider_id: str, kind: str, items: list) -> None:
    """Persist a successful live list. Skips the write when nothing changed. Never raises."""
    factory = plugin_host.session_factory
    if factory is None:
        return
    payload = [_dump(i) for i in items]
    try:
        async with factory() as session:
            row = await session.get(InventoryCache, (provider_id, kind))
            if row is not None and json.dumps(row.payload, sort_keys=True) == json.dumps(payload, sort_keys=True):
                return
            now = datetime.now(timezone.utc).isoformat()
            if row is None:
                session.add(InventoryCache(provider=provider_id, kind=kind, payload=payload, fetched_at=now))
            else:
                row.payload, row.fetched_at = payload, now
            await session.commit()
    except Exception:
        logger.warning("Could not cache %s of %s", kind, provider_id)


async def load(provider_id: str, kind: str) -> tuple[list, str] | None:
    """(items, fetched_at) or None when nothing is cached. Never raises."""
    factory = plugin_host.session_factory
    if factory is None:
        return None
    try:
        async with factory() as session:
            row = await session.get(InventoryCache, (provider_id, kind))
            if row is None:
                return None
            build = _spool if kind == SPOOLS else _material
            return [build(d) for d in row.payload], row.fetched_at
    except Exception:
        logger.warning("Could not read the %s cache of %s", kind, provider_id)
        return None


async def patch_spool_weight(provider_id: str, spool_ref: str, remaining_g: float) -> None:
    """After a write was applied at the provider, reflect it in the cached spool (keeps `fetched_at`: the rest of the
    cached list is no fresher than before). Never raises."""
    factory = plugin_host.session_factory
    if factory is None:
        return
    try:
        async with factory() as session:
            row = await session.get(InventoryCache, (provider_id, SPOOLS))
            if row is None:
                return
            payload = [dict(d) for d in row.payload]
            hit = False
            for d in payload:
                if d.get("ref") == spool_ref:
                    d["remaining_g"], hit = remaining_g, True
            if hit:
                row.payload = payload
                await session.commit()
    except Exception:
        logger.warning("Could not update the cached weight of spool %s", spool_ref)


async def cached_weight(provider_id: str, spool_ref: str) -> float | None:
    got = await load(provider_id, SPOOLS)
    if got is None:
        return None
    for s in got[0]:
        if s.ref == spool_ref and s.remaining_g is not None:
            return float(s.remaining_g)
    return None


async def invalidate(provider_id: str) -> None:
    """Drop a provider's cache. Run when its settings change (a different server/credentials: its last-known data must
    never be served as if it were the new one's). Never raises."""
    factory = plugin_host.session_factory
    if factory is None:
        return
    try:
        async with factory() as session:
            await session.execute(delete(InventoryCache).where(InventoryCache.provider == provider_id))
            await session.commit()
    except Exception:
        logger.warning("Could not clear the inventory cache of %s", provider_id)


if invalidate not in plugin_host.config_changed_hooks:
    plugin_host.config_changed_hooks.append(invalidate)
