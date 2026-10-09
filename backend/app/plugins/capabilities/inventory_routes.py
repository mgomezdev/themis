"""Routes every `inventory.filament` provider serves at `/api/v1/capabilities/inventory.filament/…` (BIZ-245).

Functionality paths, not provider paths: a client written against these keeps working when the provider is swapped, so a
provider lists `shared_router` in its `Provide.routers` and gets them. Scopes are the neutral `inventory:*` ones. What a provider
adds beyond this (e.g. Local inventory's `weight-log`) is optional and answers 404 from a provider without it."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...services.inventory import config as inventory_config, provider as inventory_provider
from .filament_inventory import TRACKS_WEIGHT

shared_router = APIRouter(tags=["inventory"])


class LowStock(BaseModel):
    """Grams below which a spool raises `spool.low`. `overrides` maps a material ref (of the active provider) to its own
    threshold and wins over `default_g`; with neither set nothing alerts."""
    default_g: float | None = Field(default=None, ge=0, le=100_000)
    overrides: dict[str, float] = Field(default_factory=dict)

    @field_validator("overrides")
    @classmethod
    def _valid(cls, v: dict[str, float]) -> dict[str, float]:
        for key, grams in v.items():
            if not key.strip() or ":" in key:
                raise ValueError(f"override key {key!r} must be a material ref")
            if not 0 <= grams <= 100_000:
                raise ValueError("override thresholds must be between 0 and 100000 grams")
        return v


def low_stock_out(row, provider_id: str | None) -> dict:
    return {"default_g": row.low_stock_default_g if row else None,
            "overrides": inventory_config.overrides_for(row, provider_id) if provider_id else {}}


@shared_router.get("/low-stock", summary="Low-inventory alert thresholds of the active provider", response_model=LowStock,
                   dependencies=[Depends(require_scope("inventory:read"))])
async def get_low_stock(session: AsyncSession = Depends(get_session)):
    return low_stock_out(await inventory_config.get_config(session), inventory_provider.provider_id())


@shared_router.put("/low-stock", summary="Set the low-inventory alert thresholds of the active provider", response_model=LowStock,
                   dependencies=[Depends(require_scope("inventory:write"))])
async def put_low_stock(body: LowStock, session: AsyncSession = Depends(get_session)):
    """Takes effect at the next sync. Raising a threshold alerts spools now below it; lowering one re-arms spools that are no
    longer below it."""
    pid = inventory_provider.require(TRACKS_WEIGHT)
    row = await inventory_config.get_config(session)
    row.low_stock_default_g = body.default_g
    row.low_stock_overrides = inventory_config.merge_overrides(row, pid, body.overrides)
    await session.commit()
    return low_stock_out(row, pid)
