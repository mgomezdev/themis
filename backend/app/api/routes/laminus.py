from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...services import catalog_service
from ...services.catalog_service import CatalogUnavailable
from ...plugins.capabilities.filament_inventory import PROFILE_LINKS_READ, PROFILE_LINKS_WRITE
from ...services.inventory import provider as inventory_provider
from ...services.providers.slicing import Catalog


class RemapResolutionEntry(BaseModel):
    field: str
    stale_value: str
    new_value: str | None = None


class InventoryResolutionEntry(BaseModel):
    printer_preset: str
    stale_name: str
    new_name: str | None = None


class RemapResolutions(BaseModel):
    printers: list[RemapResolutionEntry] = []
    jobs: list[RemapResolutionEntry] = []
    inventory_filaments: list[InventoryResolutionEntry] = []


class ConfirmRemapBody(BaseModel):
    sync_id: str
    resolutions: RemapResolutions

logger = logging.getLogger("app.laminus")

router = APIRouter(prefix="/api/v1/laminus", tags=["laminus"])


def _http(exc: CatalogUnavailable) -> HTTPException:
    return HTTPException(exc.status, exc.detail)


# ---------------------------------------------------------------------------
# Routes — a thin HTTP layer over services/catalog_service
# ---------------------------------------------------------------------------

@router.get(
    "/catalog",
    summary="Get profile catalog",
    responses={
        502: {"description": "Laminus sidecar unreachable"},
        503: {"description": "Laminus sidecar not configured"},
    },
    dependencies=[Depends(require_scope("laminus:read"))],
)
async def get_laminus_catalog():
    """Return the full machine/process/filament catalog as JSON (served from the
    Themis-side cache; the sidecar is only contacted if the cache is cold)."""
    cached = catalog_service.cached_bytes()
    if cached is not None:
        return Response(content=cached, media_type="application/json")
    try:
        data = await catalog_service.fetch_and_cache()
    except CatalogUnavailable as exc:
        raise _http(exc) from exc
    return Response(content=data, media_type="application/json")


@router.get("/catalog/status", summary="Catalog cache status",
           dependencies=[Depends(require_scope("laminus:read"))])
async def get_catalog_status() -> dict:
    """Whether the Themis catalog cache is populated and Laminus's build state.
    Includes `laminus` sub-object with `catalog_loaded`, `catalog_building`, and
    `profile_count` if the sidecar is reachable. Health result is memoized for 30 s."""
    return await catalog_service.status()


@router.post(
    "/catalog/refresh",
    summary="Refresh catalog from Laminus",
    responses={
        502: {"description": "Laminus sidecar unreachable"},
        503: {"description": "Laminus sidecar not configured"},
    },
    dependencies=[Depends(require_scope("laminus:write"))],
)
async def refresh_catalog(session: AsyncSession = Depends(get_session)) -> dict:
    """Re-fetch the catalog from Laminus. If removed profiles are referenced by live data,
    returns pending_remaps instead of committing. Old catalog remains active until confirmed."""
    try:
        return await catalog_service.refresh(session)
    except CatalogUnavailable as exc:
        raise _http(exc) from exc


@router.post(
    "/catalog/rescan",
    summary="Rescan profiles and refresh catalog",
    responses={
        502: {"description": "Laminus sidecar unreachable"},
        503: {"description": "Laminus sidecar not configured"},
        504: {"description": "Laminus catalog rebuild did not complete within 120 s"},
    },
    dependencies=[Depends(require_scope("laminus:write"))],
)
async def rescan_and_refresh_catalog(session: AsyncSession = Depends(get_session)) -> dict:
    """Tell Laminus to rebuild its catalog from disk, then update the Themis cache.
    If removed profiles are referenced by live data, returns pending_remaps."""
    try:
        return await catalog_service.rescan(session)
    except CatalogUnavailable as exc:
        raise _http(exc) from exc


@router.post("/catalog/confirm-remap", summary="Confirm pending profile remap and commit catalog",
            dependencies=[Depends(require_scope("laminus:write"))])
async def confirm_remap(
    body: ConfirmRemapBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    pending_sync = catalog_service.pending_sync()

    if pending_sync is None or pending_sync["sync_id"] != body.sync_id:
        raise HTTPException(409, "Sync superseded or expired — re-run the catalog sync")

    pending = pending_sync["pending"]
    resolutions = body.resolutions
    incoming_catalog = pending_sync.get("catalog") or Catalog()

    from ...services.catalog_utils import catalog_name_sets
    new_machines, new_processes, new_filaments, _ = catalog_name_sets(incoming_catalog)

    # Build resolution lookup maps keyed by (field, stale_value)
    printer_res_map: dict[tuple[str, str], str | None] = {
        (r.field, r.stale_value): r.new_value for r in resolutions.printers
    }
    job_res_map: dict[tuple[str, str], str | None] = {
        (r.field, r.stale_value): r.new_value for r in resolutions.jobs
    }
    # Inventory resolutions keyed by (printer_preset, stale_name) → new_name | None
    inventory_res_map: dict[tuple[str, str], str | None] = {
        (r.printer_preset, r.stale_name): r.new_name for r in resolutions.inventory_filaments
    }

    # Validate all required printer entries have valid resolutions
    unresolved = []
    for entry in pending.get("printers", []):
        key = (entry["field"], entry["stale_value"])
        new_val = printer_res_map.get(key)
        if entry.get("required") and not new_val:
            unresolved.append(f"Printer {entry['field']}={entry['stale_value']}")
        elif new_val:
            valid_set = new_machines if entry.get("options_kind") == "machine" else new_filaments
            if new_val not in valid_set:
                unresolved.append(f"Invalid value '{new_val}' for {entry['field']}")

    for entry in pending.get("jobs", []):
        key = (entry["field"], entry["stale_value"])
        new_val = job_res_map.get(key)
        if new_val:
            valid_set = new_processes if entry.get("options_kind") == "process" else new_filaments
            if new_val not in valid_set:
                unresolved.append(f"Invalid value '{new_val}' for job {entry['field']}")

    if unresolved:
        raise HTTPException(422, {"detail": "Unresolved required remaps", "unresolved": unresolved})

    # Apply Printer updates
    from ...models import JobModelTarget, Printer as PrinterModel, JobPrinterConfig as JPC
    applied_printers = 0
    for entry in pending.get("printers", []):
        key = (entry["field"], entry["stale_value"])
        new_val = printer_res_map.get(key)
        for printer_id, slot in zip(entry["affected_printer_ids"], entry["affected_slots"]):
            printer = await session.get(PrinterModel, printer_id)
            if printer is None:
                continue
            if slot is None:
                printer.current_orca_printer_profile = new_val
                applied_printers += 1
            else:
                loaded = list(printer.loaded_filaments or [])
                if slot < len(loaded):
                    loaded[slot] = {**loaded[slot], "filament_profile": new_val}
                    printer.loaded_filaments = loaded
                    applied_printers += 1

    # Apply JobPrinterConfig updates
    applied_jobs = 0
    for entry in pending.get("jobs", []):
        key = (entry["field"], entry["stale_value"])
        new_val = job_res_map.get(key)
        for cfg_id in entry["affected_config_ids"]:
            cfg = await session.get(JPC, cfg_id)
            if cfg is None:
                continue
            if entry["field"] == "print_profile":
                cfg.print_profile = new_val or ""
            else:
                cfg.filament_profile = new_val
            # Keep the make/model target in step so printers materialized later don't get the stale preset.
            target = await session.get(JobModelTarget, cfg.model_target_id) if cfg.model_target_id else None
            if target is not None:
                if entry["field"] == "print_profile":
                    target.print_profile = new_val or ""
                else:
                    target.filament_profile = new_val
            applied_jobs += 1

    await session.commit()

    # Inventory binding rewrites — best-effort after DB commit
    # For each stale entry: remove the stale name from the printer_preset list in the filament's profile
    # bindings; optionally insert the new_name in its place. A provider without PROFILE_BINDINGS is skipped.
    inventory_failures: list[str] = []
    applied_inventory = 0
    if pending.get("inventory_filaments"):
        if inventory_provider.has(PROFILE_LINKS_READ) and inventory_provider.has(PROFILE_LINKS_WRITE):
            # One read for the whole remap (not one per filament); the local copy follows our own writes, so several
            # entries touching the same material see each other's changes.
            fetched = await inventory_provider.call("list_materials")
            materials = {m.ref: m for m in fetched.value} if fetched.ok else {}
            load_error = None if fetched.ok else inventory_provider.describe_failure(fetched)[1]
            for entry in pending["inventory_filaments"]:
                printer_preset = entry["printer_preset"]
                stale_name = entry["stale_name"]
                new_name = inventory_res_map.get((printer_preset, stale_name))
                for fil_id in entry["affected_filament_ids"]:
                    try:
                        if load_error:
                            raise RuntimeError(load_error)
                        material = materials.get(str(fil_id))
                        if material is None:
                            raise RuntimeError("material not found")
                        bindings = {k: list(v) for k, v in (material.profile_links or {}).items()}
                        names = [n for n in bindings.get(printer_preset, []) if n != stale_name]
                        if new_name:
                            names.append(new_name)
                        if names:
                            bindings[printer_preset] = names
                        else:
                            bindings.pop(printer_preset, None)
                        written = await inventory_provider.call("set_profile_links", str(fil_id), bindings)
                        if not written.ok:
                            raise RuntimeError(inventory_provider.describe_failure(written)[1])
                        materials[str(fil_id)] = written.value
                        applied_inventory += 1
                    except Exception as exc:
                        inventory_failures.append(f"filament {fil_id}: {exc}")
                        logger.warning("Inventory profile-link rewrite failed for material %s: %s", fil_id, exc)

    # Commit catalog only when raw is not None (inventory-only pending skips this)
    if pending_sync.get("raw") is not None:
        catalog_service.commit_catalog(pending_sync["raw"], pending_sync["catalog"])

    catalog_service.set_pending_sync(None)
    return {
        "status": "ok",
        "applied": {
            "printers": applied_printers,
            "jobs": applied_jobs,
            "inventory_filaments": applied_inventory,
        },
        "inventory_failures": inventory_failures,
    }
