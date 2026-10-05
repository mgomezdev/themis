"""Pure logic for the low-stock preflight warning. No DB/HTTP access — callers resolve the physical spool (an
`InvSpool` from the active provider) and the needed-grams figure."""
from __future__ import annotations

from ...plugins.kinds.filament_inventory import InvSpool


def _spool_label(spool: InvSpool) -> str:
    """Prefer the material name; fall back to a generic 'spool {ref}' when material info is unavailable."""
    return (spool.material.name if spool.material else "") or f"spool {spool.ref}"


def check_spool_sufficiency(needed_g: float | None, spool: InvSpool) -> dict | None:
    """None when there's nothing to warn about (needed_g unknown, spool weight untracked, or enough left). Otherwise
    `{spool_id, spool_label, remaining_g, needed_g, message}` (`spool_id` stays the integer id the frontend always got)."""
    if needed_g is None:
        return None
    remaining_g = spool.remaining_g
    if remaining_g is None or remaining_g >= needed_g:
        return None
    spool_label = _spool_label(spool)
    material = spool.material.material if spool.material else None
    needed_g, remaining_g = round(needed_g, 2), round(remaining_g, 2)
    ask = f"~{needed_g:.0f}g {material}" if material else f"~{needed_g:.0f}g"
    return {
        "spool_id": int(spool.ref) if spool.ref.isdigit() else spool.ref,
        "spool_label": spool_label,
        "remaining_g": remaining_g,
        "needed_g": needed_g,
        "message": f"project needs {ask}, spool {spool_label} has ~{remaining_g:.0f}g remaining",
    }
