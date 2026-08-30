# backend/app/services/spool_check.py
"""Pure logic for the Spoolman low-stock preflight warning. No DB/HTTP access
here — callers resolve the physical spool dict (from Spoolman's /api/v1/spool)
and the needed-grams figure, and hand both to check_spool_sufficiency."""
from __future__ import annotations


def _spool_label(spool: dict) -> str:
    """Human-readable label for a Spoolman spool: prefer the filament name,
    fall back to a generic 'spool {id}' when filament info is unavailable."""
    filament = spool.get("filament") or {}
    name = filament.get("name")
    if name:
        return name
    return f"spool {spool.get('id')}"


def _filament_type(spool: dict) -> str | None:
    filament = spool.get("filament") or {}
    return filament.get("material")


def check_spool_sufficiency(needed_g: float | None, spool: dict) -> dict | None:
    """spool is a raw Spoolman spool dict (has id, remaining_weight, filament.name/material).
    Returns None if there's nothing to warn about (needed_g unknown, spool has no
    remaining_weight, or remaining_weight >= needed_g). Otherwise returns
    {spool_id, spool_label, remaining_g, needed_g, message}."""
    if needed_g is None:
        return None
    remaining_g = spool.get("remaining_weight")
    if remaining_g is None:
        return None
    if remaining_g >= needed_g:
        return None

    spool_label = _spool_label(spool)
    filament_type = _filament_type(spool)
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
        "spool_id": spool.get("id"),
        "spool_label": spool_label,
        "remaining_g": remaining_g,
        "needed_g": needed_g,
        "message": message,
    }
