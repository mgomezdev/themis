"""Reads of the active provider's data, contained and capability-gated."""
from __future__ import annotations

import logging

from ...plugins.kinds.filament_inventory import TRACKS_WEIGHT, InvMaterial, InvSpool
from . import provider

logger = logging.getLogger("app")


async def spools() -> list[InvSpool] | None:
    """All spools, or None when there is no provider or the provider is unreachable."""
    result = await provider.call("list_spools")
    return result.value if result.ok else None


async def materials() -> list[InvMaterial] | None:
    result = await provider.call("list_materials")
    return result.value if result.ok else None


async def spools_by_ref(context: str = "") -> dict[str, InvSpool]:
    """`{ref: spool}` for preflight. Empty without a TRACKS_WEIGHT provider (nothing to compare against) or when the
    provider fails: preflight is advisory and must never break the request."""
    if not provider.has(TRACKS_WEIGHT):
        return {}
    result = await provider.call("list_spools")
    if not result.ok:
        logger.warning("Inventory unreachable while checking spool sufficiency%s: %s", f" ({context})" if context else "", result.error)
        return {}
    return {s.ref: s for s in result.value}
