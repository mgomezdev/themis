# backend/app/services/spool_check.py
"""Pure logic for the low-stock preflight warning. No DB/HTTP access here — callers resolve the physical
spool (a `Spool` from the inventory provider) and the needed-grams figure, and hand both to
check_spool_sufficiency."""
from __future__ import annotations

from .providers.filament_inventory import Spool


def _spool_label(spool: Spool) -> str:
    """Human-readable label for a spool: prefer the filament name, fall back to a generic
    'spool {id}' when filament info is unavailable."""
    return spool.filament_name or f"spool {spool.ref}"


def _spool_id(spool: Spool) -> int | str:
    """Response `spool_id` stays the integer Spoolman id the frontend always got."""
    return int(spool.ref) if spool.ref.isdigit() else spool.ref


def check_spool_sufficiency(needed_g: float | None, spool: Spool) -> dict | None:
    """Returns None if there's nothing to warn about (needed_g unknown, spool has no
    remaining_weight, or remaining_weight >= needed_g). Otherwise returns
    {spool_id, spool_label, remaining_g, needed_g, message}."""
    if needed_g is None:
        return None
    remaining_g = spool.remaining_weight
    if remaining_g is None:
        return None
    if remaining_g >= needed_g:
        return None

    spool_label = _spool_label(spool)
    filament_type = spool.filament_material
    needed_g = round(needed_g, 2)
    remaining_g = round(remaining_g, 2)

    if filament_type:
        message = (
            f"project needs ~{needed_g:.0f}g {filament_type}, "
            f"spool {spool_label} has ~{remaining_g:.0f}g remaining"
        )
    else:
        message = (
            f"project needs ~{needed_g:.0f}g, "
            f"spool {spool_label} has ~{remaining_g:.0f}g remaining"
        )

    return {
        "spool_id": _spool_id(spool),
        "spool_label": spool_label,
        "remaining_g": remaining_g,
        "needed_g": needed_g,
        "message": message,
    }
