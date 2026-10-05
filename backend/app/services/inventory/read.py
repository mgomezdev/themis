"""Reads of the active provider's data, contained and capability-gated.

For a REMOTE provider a successful list is persisted (`cache.py`) and an unreachable provider is answered from that
last-known cache, flagged `stale` with `as_of`. "Effective remaining" overlays the newest pending outbox target for a spool
(a completed print the provider has not seen yet) on whatever weight was read."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from sqlalchemy import select

from ...models import InventoryPendingWrite
from ...plugins.host import plugin_host
from ...plugins.kinds.filament_inventory import TRACKS_WEIGHT, InvMaterial, InvSpool
from . import cache, provider

logger = logging.getLogger("app")


@dataclass
class Reading:
    """A list read: `items`, whether it came from the cache (`stale`) and when it was fetched (`as_of`)."""
    items: list
    stale: bool = False
    as_of: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error: str | None = None            # why the live read failed (set whenever `stale`, or when there is no data)
    ok: bool = True


async def _list(method: str, kind: str) -> Reading | None:
    """Live list, else cache (REMOTE only). None when there is no provider at all."""
    pid = provider.provider_id()
    if pid is None:
        return None
    result = await provider.call(method)
    if result.ok:
        if cache.cacheable():
            await cache.store(pid, kind, result.value)
        return Reading(result.value)
    message = provider.describe_failure(result)[1]
    if cache.cacheable():
        cached = await cache.load(pid, kind)
        if cached is not None:
            return Reading(cached[0], stale=True, as_of=cached[1], error=message)
    return Reading([], ok=False, error=message)


async def spools_reading() -> Reading | None:
    return await _list("list_spools", cache.SPOOLS)


async def materials_reading() -> Reading | None:
    return await _list("list_materials", cache.MATERIALS)


async def spools() -> list[InvSpool] | None:
    """All spools (live, else last-known), or None when there is no provider or no data at all."""
    r = await spools_reading()
    return r.items if r is not None and r.ok else None


async def materials() -> list[InvMaterial] | None:
    r = await materials_reading()
    return r.items if r is not None and r.ok else None


async def pending_targets(provider_id: str) -> dict[str, float]:
    """`{spool_ref: newest pending target_g}` for the provider (the weight the provider will have once the outbox lands)."""
    factory = plugin_host.session_factory
    if factory is None:
        return {}
    try:
        async with factory() as session:
            rows = (await session.execute(select(InventoryPendingWrite.spool_ref, InventoryPendingWrite.target_g).where(
                InventoryPendingWrite.provider == provider_id, InventoryPendingWrite.status == "pending")
                .order_by(InventoryPendingWrite.id))).all()
    except Exception:
        logger.warning("Could not read pending inventory writes")
        return {}
    return {ref: float(g) for ref, g in rows}              # ascending id: the last one per spool wins


def effective(spools_: list[InvSpool], pending: dict[str, float]) -> list[InvSpool]:
    """Spools with `remaining_g` replaced by the pending target where there is one."""
    return [replace(s, remaining_g=pending[s.ref]) if s.ref in pending else s for s in spools_]


async def spools_by_ref(context: str = "") -> dict[str, InvSpool]:
    """`{ref: spool}` (effective remaining) for preflight. Empty without a TRACKS_WEIGHT provider (nothing to compare
    against) or when there is no data: preflight is advisory and must never break the request."""
    if not provider.has(TRACKS_WEIGHT):
        return {}
    reading = await spools_reading()
    if reading is None or not reading.ok:
        logger.warning("Inventory unreachable while checking spool sufficiency%s: %s",
                       f" ({context})" if context else "", reading.error if reading else "no provider")
        return {}
    pid = provider.provider_id()
    items = effective(reading.items, await pending_targets(pid)) if pid else reading.items
    return {s.ref: s for s in items}
