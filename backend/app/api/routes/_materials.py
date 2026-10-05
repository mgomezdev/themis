"""HTTP glue for provider-namespaced material asks (see services/inventory/refs.py): a bad ref is a 422."""
from __future__ import annotations

from fastapi import HTTPException

from ...services.inventory import refs as inventory_refs


def material_columns(filament_id: int | None, material_provider: str | None = None, material_ref: str | None = None) -> dict:
    """`{filament_id, material_provider, material_ref}` for a writer: accepts the legacy `filament_id` or the pair."""
    try:
        f, p, r = inventory_refs.material(filament_id, material_provider, material_ref)
    except inventory_refs.MaterialRefError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"filament_id": f, "material_provider": p, "material_ref": r}
