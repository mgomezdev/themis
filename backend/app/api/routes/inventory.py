"""Provider-neutral inventory API (BIZ-202 §3.9). Talks only to the active `filament_inventory` provider through the
plugin host and the neutral DTOs; a route that needs a capability the provider lacks answers 409
`capability_unavailable`. `raw` provider payloads are never serialised here."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...plugins.kinds.filament_inventory import (
    LABEL_SCAN, PROFILE_LINKS_WRITE, REMOTE, TRACKS_WEIGHT, InventoryProviderError, InvMaterial, InvSpool,
)
from ...services.inventory import config as inventory_config, provider as inventory_provider, sync as inventory_sync

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])


def material_out(m: InvMaterial) -> dict:
    d = asdict(m)
    d.pop("raw", None)
    return d


def spool_out(s: InvSpool, url: str | None = None) -> dict:
    d = asdict(s)
    d.pop("raw", None)
    d["material"] = material_out(s.material) if s.material else None
    d["url"] = url
    return d


def _unavailable(result) -> HTTPException:
    return HTTPException(status_code=503, detail=inventory_provider.describe_failure(result)[1])


def _envelope(pid: str, items: list) -> dict:
    # `stale`/`as_of` become meaningful with the last-known cache (offline behaviour); live reads are never stale.
    return {"provider": pid, "stale": False, "as_of": datetime.now(timezone.utc).isoformat(), "items": items}


@router.get("/materials", summary="List materials of the active inventory provider",
            responses={409: {"description": "No inventory provider is active"}, 503: {"description": "Provider unreachable"}},
            dependencies=[Depends(require_scope("inventory:read"))])
async def list_materials():
    pid = inventory_provider.require()
    result = await inventory_provider.call("list_materials")
    if not result.ok:
        raise _unavailable(result)
    return _envelope(pid, [material_out(m) for m in result.value])


@router.get("/spools", summary="List spools of the active inventory provider",
            responses={409: {"description": "No inventory provider is active"}, 503: {"description": "Provider unreachable"}},
            dependencies=[Depends(require_scope("inventory:read"))])
async def list_spools():
    pid = inventory_provider.require()
    result = await inventory_provider.call("list_spools")
    if not result.ok:
        raise _unavailable(result)
    provider = inventory_provider.active_provider()
    return _envelope(pid, [spool_out(s, provider.spool_url(s.ref)) for s in result.value])


@router.post("/sync-now", summary="Refresh from the provider now",
             responses={409: {"description": "No provider, or it is not a remote one"}, 503: {"description": "Provider unreachable"}},
             dependencies=[Depends(require_scope("inventory:read"))])
async def sync_now(session: AsyncSession = Depends(get_session)):
    inventory_provider.require(REMOTE)
    try:
        return await inventory_sync.record_sync(session)
    except InventoryProviderError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/sync-status", summary="Provider health and sync status (never fails)",
            dependencies=[Depends(require_scope("inventory:read"))])
async def sync_status():
    pid = inventory_provider.provider_id()
    provider = inventory_provider.active_provider()
    caps = sorted(provider.capabilities) if provider else []
    return {"provider": pid, "capabilities": caps, **inventory_sync.status(pid)}


class LabelBody(BaseModel):
    text: str = Field(max_length=2000)


@router.post("/resolve-label", summary="Resolve scanned label text to a spool",
             responses={409: {"description": "The provider cannot scan labels"}},
             dependencies=[Depends(require_scope("inventory:read"))])
async def resolve_label(body: LabelBody):
    inventory_provider.require(LABEL_SCAN)
    provider = inventory_provider.active_provider()
    try:
        ref = provider.parse_label(body.text)
    except Exception:
        ref = None
    return {"spool_ref": ref}


class LinksBody(BaseModel):
    links: dict[str, list[str]]


@router.patch("/materials/{ref}/profile-links", summary="Set a material's Orca preset links",
              responses={409: {"description": "The provider cannot store profile links"}, 503: {"description": "Provider unreachable"}},
              dependencies=[Depends(require_scope("inventory:write"))])
async def set_profile_links(ref: str, body: LinksBody):
    inventory_provider.require(PROFILE_LINKS_WRITE)
    result = await inventory_provider.call("set_profile_links", ref, body.links)
    if not result.ok:
        exc = result.exception
        status = exc.status if isinstance(exc, InventoryProviderError) and exc.status else 503
        raise HTTPException(status_code=status, detail=inventory_provider.describe_failure(result)[1])
    return material_out(result.value)


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


class SettingsIn(BaseModel):
    deduct_on_complete: bool | None = None
    low_stock: LowStock | None = None


async def _settings_out(session: AsyncSession) -> dict:
    row = await inventory_config.get_config(session)
    pid = inventory_provider.provider_id()
    return {
        "provider": pid,
        "deduct_on_complete": bool(row.deduct_on_complete),
        "low_stock": {"default_g": row.low_stock_default_g,
                      "overrides": inventory_config.overrides_for(row, pid) if pid else {}},
    }


@router.get("/settings", summary="Inventory settings (deduct on completion, low-stock thresholds)",
            dependencies=[Depends(require_scope("inventory:read"))])
async def get_settings(session: AsyncSession = Depends(get_session)):
    out = await _settings_out(session)
    await session.commit()
    return out


@router.put("/settings", summary="Update inventory settings; omitted fields are unchanged",
            responses={409: {"description": "Low-stock thresholds need a provider that tracks weight"}},
            dependencies=[Depends(require_scope("inventory:write"))])
async def put_settings(body: SettingsIn, session: AsyncSession = Depends(get_session)):
    row = await inventory_config.get_config(session)
    if body.low_stock is not None:
        pid = inventory_provider.require(TRACKS_WEIGHT)
        row.low_stock_default_g = body.low_stock.default_g
        row.low_stock_overrides = inventory_config.merge_overrides(row, pid, body.low_stock.overrides)
    if body.deduct_on_complete is not None:
        row.deduct_on_complete = body.deduct_on_complete
    await session.commit()
    return await _settings_out(session)
