from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Printer, QueueConfig, SpoolmanConfig, WebhookConfig
from ...services import spoolman_service
from ...services.printer_client_factory import REGISTRY, create_client
from ...services.printer_manager import printer_manager

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

_BACKUP_VERSION = 1


class QueueConfigOut(BaseModel):
    check_interval_minutes: int
    operator_name: str | None
    snapshot_interval_seconds: int
    estimates_enabled: bool


class QueueConfigIn(BaseModel):
    check_interval_minutes: int | None = None
    operator_name: str | None = None
    snapshot_interval_seconds: int | None = None
    estimates_enabled: bool | None = None


async def _get_or_create_queue(session: AsyncSession) -> QueueConfig:
    row = await session.get(QueueConfig, 1)
    if row is None:
        row = QueueConfig(id=1, check_interval_minutes=5, snapshot_interval_seconds=2, estimates_enabled=False)
        session.add(row)
        await session.flush()
    return row


@router.get("/queue", response_model=QueueConfigOut, summary="Get queue config")
async def get_queue_config(session: AsyncSession = Depends(get_session)):
    """Queue engine settings: poll interval, operator name, and snapshot interval."""
    return await _get_or_create_queue(session)


@router.put("/queue", response_model=QueueConfigOut, summary="Update queue config")
async def update_queue_config(
    body: QueueConfigIn,
    session: AsyncSession = Depends(get_session),
):
    """Update one or more queue engine settings. Omitted fields are left unchanged."""
    row = await _get_or_create_queue(session)
    if body.check_interval_minutes is not None:
        row.check_interval_minutes = max(1, body.check_interval_minutes)
    if body.operator_name is not None:
        row.operator_name = body.operator_name or None
    if body.snapshot_interval_seconds is not None:
        row.snapshot_interval_seconds = max(1, body.snapshot_interval_seconds)
    if body.estimates_enabled is not None:
        row.estimates_enabled = body.estimates_enabled
        if not body.estimates_enabled:
            from sqlalchemy import text as _text
            await session.execute(
                _text("UPDATE jobs SET estimate_status=NULL WHERE estimate_status='pending'")
            )
    await session.commit()
    await session.refresh(row)
    return row


class SpoolmanConfigOut(BaseModel):
    enabled: bool
    url: str | None
    api_key: str | None


class SpoolmanConfigIn(BaseModel):
    enabled: bool | None = None
    url: str | None = None
    api_key: str | None = None


async def _get_or_create(session: AsyncSession) -> SpoolmanConfig:
    row = await session.get(SpoolmanConfig, 1)
    if row is None:
        row = SpoolmanConfig(id=1, enabled=False)
        session.add(row)
        await session.flush()
    return row


@router.get("/spoolman", response_model=SpoolmanConfigOut, summary="Get Spoolman config")
async def get_spoolman_config(session: AsyncSession = Depends(get_session)):
    """Spoolman integration settings: enabled flag, base URL, and API key."""
    return await _get_or_create(session)


@router.put("/spoolman", response_model=SpoolmanConfigOut, summary="Update Spoolman config")
async def update_spoolman_config(
    body: SpoolmanConfigIn,
    session: AsyncSession = Depends(get_session),
):
    """Update Spoolman integration settings. Omitted fields are left unchanged."""
    row = await _get_or_create(session)
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.url is not None:
        row.url = body.url or None
    if body.api_key is not None:
        row.api_key = body.api_key or None
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/spoolman/test", summary="Test Spoolman connection")
async def test_spoolman_connection(
    body: SpoolmanConfigIn,
    session: AsyncSession = Depends(get_session),
):
    """Verify connectivity to Spoolman. Uses the supplied URL/key if provided,
    falling back to the saved config. Returns `{ok, version}` or `{ok, message}`.
    If the catalog is warm and stale UUIDs are detected in Spoolman filaments,
    returns `{status: "pending_remaps", ...}` instead."""
    url = body.url
    api_key = body.api_key
    if not url:
        row = await _get_or_create(session)
        url = row.url
        if api_key is None:
            api_key = row.api_key
    if not url:
        return {"ok": False, "message": "No URL configured"}
    try:
        info = await spoolman_service.test_connection(url, api_key)
    except Exception as e:
        return {"ok": False, "message": str(e)}

    # --- Spoolman UUID sanity check (best-effort) ---
    import app.api.routes.laminus as _lam_mod
    from ...services.catalog_utils import catalog_name_sets

    _catalog = _lam_mod._catalog_dict
    if _catalog is not None:
        try:
            _, _, _, catalog_uuids = catalog_name_sets(_catalog)
            spool_filaments = await spoolman_service.fetch_filaments(url, api_key)
            spoolman_groups: dict[str, dict] = {}
            for fil in spool_filaments:
                raw_extra = (fil.get("extra") or {}).get("orca_profiles")
                if not raw_extra:
                    continue
                try:
                    profiles: dict = json.loads(json.loads(raw_extra))
                except Exception:
                    continue
                for uid in profiles:
                    if uid not in catalog_uuids:
                        stale_name = profiles[uid] if isinstance(profiles[uid], str) else str(uid)
                        g = spoolman_groups.setdefault(uid, {
                            "stale_uuid": uid,
                            "stale_name": stale_name,
                            "options_kind": "filament_uuid",
                            "required": False,
                            "affected_filament_ids": [],
                            "affected_filament_names": [],
                        })
                        g["affected_filament_ids"].append(fil["id"])
                        g["affected_filament_names"].append(fil.get("name", str(fil["id"])))

            if spoolman_groups:
                import uuid as _uuid
                sync_id = str(_uuid.uuid4())
                import time as _time
                _lam_mod._pending_sync = {
                    "sync_id": sync_id,
                    "raw": None,
                    "catalog": None,
                    "pending": {
                        "printers": [],
                        "jobs": [],
                        "spoolman_filaments": list(spoolman_groups.values()),
                    },
                    "created_at": _time.time(),
                }
                return {
                    "status": "pending_remaps",
                    "ok": True,
                    "sync_id": sync_id,
                    "pending": {
                        "printers": [],
                        "jobs": [],
                        "spoolman_filaments": list(spoolman_groups.values()),
                    },
                    "options": {
                        "machine": [],
                        "process": [],
                        "filament": [],
                        "filament_uuids": [
                            {"uuid": f["uuid"], "name": f["name"]}
                            for f in _catalog.get("filament", [])
                            if f.get("uuid") and f.get("name")
                        ],
                    },
                    "spoolman_error": None,
                }
        except Exception:
            # Best-effort: if fetch_filaments fails, fall through to normal success
            pass

    return {"ok": True, "status": "ok", "version": info.get("version", "unknown")}


# ---------------------------------------------------------------------------
# Webhook config
# ---------------------------------------------------------------------------

class WebhookConfigOut(BaseModel):
    url: str | None
    secret: str | None
    events: list[str]


class WebhookConfigIn(BaseModel):
    url: str | None = None
    secret: str | None = None
    events: list[str] | None = None


async def _get_or_create_webhook(session: AsyncSession) -> WebhookConfig:
    row = await session.get(WebhookConfig, 1)
    if row is None:
        row = WebhookConfig(id=1, events=[])
        session.add(row)
        await session.flush()
    return row


@router.get("/webhook", response_model=WebhookConfigOut, summary="Get webhook config")
async def get_webhook_config(session: AsyncSession = Depends(get_session)):
    """Outbound webhook settings: endpoint URL, HMAC secret, and subscribed event types."""
    return await _get_or_create_webhook(session)


@router.put("/webhook", response_model=WebhookConfigOut, summary="Update webhook config")
async def update_webhook_config(
    body: WebhookConfigIn,
    session: AsyncSession = Depends(get_session),
):
    """Update webhook settings. Omitted fields are left unchanged."""
    row = await _get_or_create_webhook(session)
    if body.url is not None:
        row.url = body.url or None
    if body.secret is not None:
        row.secret = body.secret or None
    if body.events is not None:
        row.events = body.events
    await session.commit()
    await session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Fleet backup / restore
# ---------------------------------------------------------------------------

@router.get(
    "/fleet-backup",
    summary="Download fleet backup",
    responses={},
)
async def fleet_backup(session: AsyncSession = Depends(get_session)) -> Response:
    """Export all printer configs as a downloadable JSON file.

    The response has `Content-Disposition: attachment; filename=themis-fleet-backup.json`
    so browsers will prompt to save it. Import with `POST /settings/fleet-import`."""
    result = await session.execute(select(Printer))
    printers = result.scalars().all()

    data = {
        "themis_backup_version": _BACKUP_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "printers": [
            {
                "name": p.name,
                "printer_type": p.printer_type,
                "connection_config": p.connection_config or {},
                "orca_printer_profiles": p.orca_printer_profiles or [],
                "current_orca_printer_profile": p.current_orca_printer_profile,
                "loaded_filaments": p.loaded_filaments or [],
                "build_plate_type": p.build_plate_type,
                "enabled": p.enabled,
                "queue_on": p.queue_on,
            }
            for p in printers
        ],
    }

    payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="themis-fleet-backup.json"'},
    )


class FleetImportReport(BaseModel):
    imported: int
    skipped: int
    warnings: list[str]


@router.post(
    "/fleet-import",
    response_model=FleetImportReport,
    summary="Import fleet backup",
    responses={
        400: {"description": "Invalid or unsupported backup file"},
    },
)
async def fleet_import(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> FleetImportReport:
    """Import printer configs from a backup file. Profile resolution failures are non-fatal.

    Returns a report with counts of imported and skipped printers plus any warnings
    about unrecognised OrcaSlicer profile names."""
    raw = await file.read()
    try:
        data = json.loads(raw)
    except Exception as exc:
        raise HTTPException(400, f"Invalid JSON: {exc}")

    if not isinstance(data, dict) or "printers" not in data:
        raise HTTPException(400, "Not a valid Themis fleet backup file")

    version = data.get("themis_backup_version", 0)
    if version < 1:
        raise HTTPException(400, f"Unsupported backup version: {version}")

    # Fetch catalog once for profile-name validation (best-effort)
    from .laminus import get_cached_catalog
    cat: dict | None = None
    try:
        cat = await get_cached_catalog()
    except Exception:
        pass

    machine_names: set[str] = set()
    filament_names: set[str] = set()
    if cat:
        from ...services.catalog_utils import catalog_name_sets
        machine_names, _, filament_names, _ = catalog_name_sets(cat)

    warnings: list[str] = []
    imported = 0
    skipped = 0

    for pr in data.get("printers", []):
        pname = pr.get("name") or "Unnamed Printer"
        ptype = pr.get("printer_type", "")

        if ptype not in REGISTRY:
            warnings.append(f"'{pname}': skipped — unknown printer type '{ptype}'")
            skipped += 1
            continue

        orca_profiles: list[str] = pr.get("orca_printer_profiles") or []
        current_profile: str | None = pr.get("current_orca_printer_profile")
        loaded: list[dict] = pr.get("loaded_filaments") or []

        if cat:
            for prof in orca_profiles:
                if prof not in machine_names:
                    warnings.append(f"'{pname}': Orca machine profile '{prof}' not found in catalog")
            if current_profile and current_profile not in machine_names:
                warnings.append(f"'{pname}': active Orca profile '{current_profile}' not found in catalog")
            for slot in loaded:
                fp = slot.get("filament_profile")
                if fp and fp not in filament_names:
                    warnings.append(
                        f"'{pname}' slot {slot.get('slot', '?')}: filament profile '{fp}' not found in catalog"
                    )

        printer = Printer(
            name=pname,
            printer_type=ptype,
            connection_config=pr.get("connection_config") or {},
            orca_printer_profiles=orca_profiles,
            current_orca_printer_profile=current_profile,
            loaded_filaments=loaded,
            build_plate_type=pr.get("build_plate_type"),
            enabled=pr.get("enabled", True),
            queue_on=pr.get("queue_on", True),
        )
        session.add(printer)
        await session.flush()

        try:
            client = create_client(printer)
            printer_manager.connect_printer(printer.id, client)
        except Exception:
            pass

        imported += 1

    await session.commit()

    return FleetImportReport(imported=imported, skipped=skipped, warnings=warnings)
