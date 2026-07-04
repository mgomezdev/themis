from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Printer, QueueConfig, SpoolmanConfig
from ...services import spoolman_service
from ...services.printer_client_factory import REGISTRY, create_client
from ...services.printer_manager import printer_manager

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

_BACKUP_VERSION = 1


class QueueConfigOut(BaseModel):
    check_interval_minutes: int
    operator_name: str | None
    snapshot_interval_seconds: int


class QueueConfigIn(BaseModel):
    check_interval_minutes: int | None = None
    operator_name: str | None = None
    snapshot_interval_seconds: int | None = None


async def _get_or_create_queue(session: AsyncSession) -> QueueConfig:
    row = await session.get(QueueConfig, 1)
    if row is None:
        row = QueueConfig(id=1, check_interval_minutes=5, snapshot_interval_seconds=2)
        session.add(row)
        await session.flush()
    return row


@router.get("/queue", response_model=QueueConfigOut)
async def get_queue_config(session: AsyncSession = Depends(get_session)):
    return await _get_or_create_queue(session)


@router.put("/queue", response_model=QueueConfigOut)
async def update_queue_config(
    body: QueueConfigIn,
    session: AsyncSession = Depends(get_session),
):
    row = await _get_or_create_queue(session)
    if body.check_interval_minutes is not None:
        row.check_interval_minutes = max(1, body.check_interval_minutes)
    if body.operator_name is not None:
        row.operator_name = body.operator_name or None
    if body.snapshot_interval_seconds is not None:
        row.snapshot_interval_seconds = max(1, body.snapshot_interval_seconds)
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


@router.get("/spoolman", response_model=SpoolmanConfigOut)
async def get_spoolman_config(session: AsyncSession = Depends(get_session)):
    return await _get_or_create(session)


@router.put("/spoolman", response_model=SpoolmanConfigOut)
async def update_spoolman_config(
    body: SpoolmanConfigIn,
    session: AsyncSession = Depends(get_session),
):
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


@router.post("/spoolman/test")
async def test_spoolman_connection(
    body: SpoolmanConfigIn,
    session: AsyncSession = Depends(get_session),
):
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
        return {"ok": True, "version": info.get("version", "unknown")}
    except Exception as e:
        return {"ok": False, "message": str(e)}


# ---------------------------------------------------------------------------
# Fleet backup / restore
# ---------------------------------------------------------------------------

@router.get("/fleet-backup")
async def fleet_backup(session: AsyncSession = Depends(get_session)) -> Response:
    """Export all printer configs as a downloadable JSON file."""
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


@router.post("/fleet-import", response_model=FleetImportReport)
async def fleet_import(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> FleetImportReport:
    """Import printer configs from a backup file. Profile resolution failures are non-fatal."""
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
    from .orca import get_cached_catalog
    cat: dict | None = None
    try:
        cat = await get_cached_catalog()
    except Exception:
        pass

    machine_names: set[str] = set()
    filament_names: set[str] = set()
    if cat:
        machine_names = {m["name"] for m in cat.get("machine", []) if m.get("name")}
        filament_names = {f["name"] for f in cat.get("filament", []) if f.get("name")}

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
