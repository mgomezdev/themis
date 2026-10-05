"""Deprecated `/api/v1/spoolman/*` aliases (BIZ-202 §3.9): same paths and response shapes as ever, now relayed through
the inventory provider. They only apply while Spoolman is the active provider (409 when another one is; 503 when
Spoolman itself is not configured/enabled). They move into the Spoolman plugin's own router in a later phase."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import InventoryConfig
from ...plugins.kinds.filament_inventory import PROFILE_LINKS_WRITE, InventoryProviderError
from ...services.inventory import config as inventory_config, provider as inventory_provider, read as inventory_read
from ...services.inventory import sync as inventory_sync

router = APIRouter(prefix="/api/v1/spoolman", tags=["spoolman"])

PLUGIN_ID = "spoolman"


def _require_spoolman() -> None:
    """503 until Spoolman is configured, enabled and active; 409 when a different provider is active."""
    pid = inventory_provider.provider_id()
    if pid is None:
        raise HTTPException(status_code=503, detail="Spoolman not configured or disabled")
    if pid != PLUGIN_ID:
        raise HTTPException(status_code=409, detail="Spoolman is not the active inventory provider; use /api/v1/inventory")


def _unavailable(result) -> HTTPException:
    return HTTPException(status_code=503, detail=inventory_provider.describe_failure(result)[1])


@router.get(
    "/filaments",
    summary="List Spoolman filaments",
    responses={503: {"description": "Spoolman not configured, disabled, or unreachable"}},
    dependencies=[Depends(require_scope("spoolman:read"))],
)
async def get_filaments():
    """Fetch all filament definitions from the configured Spoolman instance."""
    _require_spoolman()
    result = await inventory_provider.call("list_materials")
    if not result.ok:
        raise _unavailable(result)
    return [m.raw for m in result.value]


@router.get(
    "/spools",
    summary="List Spoolman spools",
    responses={503: {"description": "Spoolman not configured, disabled, or unreachable"}},
    dependencies=[Depends(require_scope("spoolman:read"))],
)
async def get_spools():
    """Fetch all spool inventory from the configured Spoolman instance."""
    _require_spoolman()
    result = await inventory_provider.call("list_spools")
    if not result.ok:
        raise _unavailable(result)
    return [s.raw for s in result.value]


class SyncNowResponse(BaseModel):
    filament_count: int
    spool_count: int


@router.post(
    "/sync-now",
    summary="Manually sync filaments and spools from Spoolman",
    responses={503: {"description": "Spoolman not configured, disabled, or unreachable"}},
    dependencies=[Depends(require_scope("spoolman:read"))],
)
async def sync_now(session: AsyncSession = Depends(get_session)):
    """Re-fetch filaments and spools from Spoolman, confirming the connection.
    Records the outcome (success clears any previously shown error) so the
    status indicator and Spoolman settings page reflect this attempt too."""
    _require_spoolman()
    try:
        result = await inventory_sync.record_sync(session)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    return SyncNowResponse(filament_count=result["material_count"], spool_count=result["spool_count"])


class SyncStatusResponse(BaseModel):
    enabled: bool
    interval_minutes: int
    last_sync_at: str | None
    last_attempt_at: str | None
    last_error: str | None
    last_error_code: str | None


@router.get(
    "/sync-status",
    summary="Spoolman sync health for the status indicator and settings page",
    response_model=SyncStatusResponse,
    dependencies=[Depends(require_scope("spoolman:read"))],
)
async def get_sync_status():
    """Never 503s, even when Spoolman is disabled/unconfigured — callers use
    `enabled` to decide whether to show anything at all."""
    return SyncStatusResponse(**inventory_sync.status(PLUGIN_ID))


class FilamentPatchBody(BaseModel):
    orca_profiles: dict[str, list[str]]


@router.patch(
    "/filaments/{filament_id}",
    summary="Update filament OrcaSlicer profiles",
    responses={503: {"description": "Spoolman not configured, disabled, or unreachable"}},
    dependencies=[Depends(require_scope("spoolman:write"))],
)
async def patch_filament(filament_id: int, body: FilamentPatchBody):
    """Write OrcaSlicer profile assignments back to a Spoolman filament's extra fields."""
    _require_spoolman()
    if not inventory_provider.has(PROFILE_LINKS_WRITE):
        raise HTTPException(status_code=501, detail="The inventory provider does not support profile bindings")
    result = await inventory_provider.call("set_profile_links", str(filament_id), body.orca_profiles)
    if not result.ok:
        exc = result.exception
        if isinstance(exc, InventoryProviderError):
            raise HTTPException(status_code=exc.status or 503, detail=inventory_provider.describe_failure(result)[1])
        raise _unavailable(result)
    return result.value.raw


class LowStockConfig(BaseModel):
    """Grams below which a spool raises a `spool.low` event. `overrides` maps a Spoolman filament id (as a
    string) to its own threshold and wins over `default_g`; with neither set nothing alerts."""
    default_g: float | None = Field(default=None, ge=0, le=100_000)
    overrides: dict[str, float] = Field(default_factory=dict)

    @field_validator("overrides")
    @classmethod
    def _valid_overrides(cls, v: dict[str, float]) -> dict[str, float]:
        for key, grams in v.items():
            if not key.isdigit():
                raise ValueError(f"override key {key!r} must be a Spoolman filament id")
            if not 0 <= grams <= 100_000:
                raise ValueError("override thresholds must be between 0 and 100000 grams")
        return v


def _low_stock_out(row: InventoryConfig | None) -> LowStockConfig:
    return LowStockConfig(default_g=row.low_stock_default_g if row else None,
                          overrides=inventory_config.overrides_for(row, PLUGIN_ID))


@router.get("/low-stock", summary="Low-inventory alert thresholds", response_model=LowStockConfig,
            dependencies=[Depends(require_scope("spoolman:read"))])
async def get_low_stock(session: AsyncSession = Depends(get_session)):
    return _low_stock_out(await session.get(InventoryConfig, 1))


@router.put("/low-stock", summary="Set low-inventory alert thresholds", response_model=LowStockConfig,
            dependencies=[Depends(require_scope("spoolman:write"))])
async def put_low_stock(body: LowStockConfig, session: AsyncSession = Depends(get_session)):
    """Takes effect at the next Spoolman sync. Raising a threshold alerts spools now below it; lowering one
    re-arms spools that are no longer below it."""
    row = await inventory_config.get_config(session)
    row.low_stock_default_g = body.default_g
    row.low_stock_overrides = inventory_config.merge_overrides(row, PLUGIN_ID, body.overrides)
    await session.commit()
    return _low_stock_out(row)
