"""Interim usage deduction (replaced by the snapshot + absolute-set outbox in BIZ-218): read the spool's current weight
and set it to `current - grams`, fire-and-forget off the queue loop. Needs TRACKS_WEIGHT + WRITE_WEIGHT."""
from __future__ import annotations

import logging

from ...plugins.kinds.filament_inventory import TRACKS_WEIGHT, WRITE_WEIGHT
from . import provider

logger = logging.getLogger("app")


def can_deduct() -> bool:
    return provider.has(TRACKS_WEIGHT) and provider.has(WRITE_WEIGHT)


async def deduct(spool_ref: str, grams: float) -> None:
    """Never raises. Logs a warning when the deduction could not be applied."""
    try:
        read = await provider.call("get_spool", spool_ref)
        spool = read.value if read.ok else None
        if spool is None or spool.remaining_g is None:
            logger.warning("Inventory deduction skipped: spool %s unreadable (%s)", spool_ref, read.error or "not found")
            return
        wrote = await provider.call("set_remaining", spool_ref, max(0.0, spool.remaining_g - grams))
        if not wrote.ok:
            logger.warning("Inventory deduction failed: spool=%s grams=%s (%s)", spool_ref, grams, wrote.error)
    except Exception:
        logger.warning("Inventory deduction failed: spool=%s grams=%s", spool_ref, grams)
