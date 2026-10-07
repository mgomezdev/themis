"""The Spoolman plugin's deprecated alias routes (BIZ-202 §3.9, D9): `/api/v1/spoolman/*` and `/api/v1/settings/spoolman*`.

Same paths, scopes and response shapes as before the extraction (byte-for-byte goldens in `tests/golden/`), relayed through the
inventory provider. They only apply while Spoolman is the active provider (503 until it is configured/enabled, 409 when another
provider is active). When to remove them is this plugin's own versioning decision, not a core compat window. Mounted by the host
from `PluginManifest.alias_routers` (absolute prefixes); the neutral API is `/api/v1/inventory`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import InventoryConfig
from ...services import catalog_service
from ...services.inventory import config as inventory_config, provider as inventory_provider
from ...services.inventory import sync as inventory_sync
from ..host import plugin_host
from ..manifest import PluginError
from ..capabilities.filament_inventory import KIND, PROFILE_LINKS_READ, PROFILE_LINKS_WRITE, InventoryProviderError

router = APIRouter(prefix="/api/v1/spoolman", tags=["spoolman"])
settings_router = APIRouter(prefix="/api/v1/settings/spoolman", tags=["settings"])

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


# --- /api/v1/settings/spoolman ----------------------------------------------------------------------------------------------

class SpoolmanConfigOut(BaseModel):
    enabled: bool
    url: str | None
    has_api_key: bool
    sync_interval_minutes: int


class SpoolmanConfigIn(BaseModel):
    enabled: bool | None = None
    url: str | None = None
    api_key: str | None = None
    sync_interval_minutes: int | None = None


def _spoolman_out() -> SpoolmanConfigOut:
    cfg = plugin_host.settings(PLUGIN_ID)
    return SpoolmanConfigOut(
        enabled=plugin_host.is_enabled(PLUGIN_ID), url=cfg.get("url") or None,
        has_api_key=plugin_host.has_secret(PLUGIN_ID, "api_key"),
        sync_interval_minutes=int(cfg.get("sync_interval_minutes") or 15),
    )


@settings_router.get("", response_model=SpoolmanConfigOut, summary="Get Spoolman config",
           dependencies=[Depends(require_scope("settings:read"))])
async def get_spoolman_config():
    """Spoolman integration settings: enabled flag, base URL, and whether an API key is set.
    The key itself never round-trips - omit `api_key` on PUT to leave it unchanged."""
    return _spoolman_out()


@settings_router.put("", response_model=SpoolmanConfigOut, summary="Update Spoolman config",
           dependencies=[Depends(require_scope("settings:write"))])
async def update_spoolman_config(body: SpoolmanConfigIn):
    """Update Spoolman integration settings. Omitted fields (including `api_key`) are left
    unchanged; an empty string for `api_key` clears it. Enabling Spoolman makes it the active inventory provider."""
    settings: dict = {}
    if body.url is not None:
        settings["url"] = body.url or None
    if body.sync_interval_minutes is not None:
        settings["sync_interval_minutes"] = max(1, body.sync_interval_minutes)
    try:
        await plugin_host.update_config(
            PLUGIN_ID, enabled=body.enabled, settings=settings or None,
            secrets={"api_key": body.api_key} if body.api_key is not None else None)
        if body.enabled and plugin_host.slot(KIND) != PLUGIN_ID:
            await plugin_host.set_slot(KIND, PLUGIN_ID)
    except PluginError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _spoolman_out()


@settings_router.post("/test", summary="Test Spoolman connection",
            dependencies=[Depends(require_scope("settings:write"))])
async def test_spoolman_connection(body: SpoolmanConfigIn):
    """Verify connectivity to Spoolman. Uses the supplied URL/key if provided,
    falling back to the saved config. Returns `{ok, version}` or `{ok, message}`.
    If the catalog is warm and stale UUIDs are detected in Spoolman filaments,
    returns `{status: "pending_remaps", ...}` instead."""
    saved_url = plugin_host.settings(PLUGIN_ID).get("url")
    if not (body.url or saved_url):
        return {"ok": False, "message": "No URL configured"}
    try:
        inventory = plugin_host.build_candidate(
            PLUGIN_ID, settings={"url": body.url} if body.url else None,
            secrets={"api_key": body.api_key} if body.api_key is not None else None)
        info = await inventory.test_connection()
    except Exception as e:
        return {"ok": False, "message": plugin_host.redact(PLUGIN_ID, str(e), (body.api_key or "",))}

    # --- Spoolman profile-name sanity check (best-effort) ---
    # Check that profile name strings bound to each filament exist in the catalog.
    from ...services.catalog_utils import catalog_name_sets, stale_binding_groups

    _catalog = catalog_service.cached_catalog()
    if _catalog is not None and PROFILE_LINKS_READ in inventory.capabilities:
        try:
            _, _, catalog_filaments, _ = catalog_name_sets(_catalog)
            spoolman_groups = stale_binding_groups(
                await inventory.list_materials(), lambda name: name not in catalog_filaments)

            if spoolman_groups:
                import uuid as _uuid
                import time as _time
                sync_id = str(_uuid.uuid4())
                pending_entries = list(spoolman_groups.values())
                catalog_service.set_pending_sync({
                    "sync_id": sync_id,
                    "raw": None,
                    "catalog": None,
                    "pending": {
                        "printers": [],
                        "jobs": [],
                        "spoolman_filaments": pending_entries,
                    },
                    "created_at": _time.time(),
                })
                return {
                    "status": "pending_remaps",
                    "ok": True,
                    "sync_id": sync_id,
                    "pending": {
                        "printers": [],
                        "jobs": [],
                        "spoolman_filaments": pending_entries,
                    },
                    "options": {
                        "machine": [],
                        "process": [],
                        "filament": sorted(catalog_filaments),
                    },
                    "spoolman_error": None,
                }
        except Exception:
            # Best-effort: if fetch_filaments fails, fall through to normal success
            pass

    return {"ok": True, "status": "ok", "version": info.get("version", "unknown")}
