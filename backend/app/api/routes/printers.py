from __future__ import annotations

import asyncio
import os
import socket
import time as _time

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import shutil

from ...auth import require_scope
from ...database import get_session
from ...models import GcodeFile, Job, JobModelTarget, JobPrinterConfig, Printer
from ...services.library_scanner import is_presliced_name
from ...services import camera_hub, catalog_service
from ...services.inventory import refs as inventory_refs
from ...services.providers.slicing import Catalog, get_format_provider
from ...services.camera_proxy import grab_jpeg_frame, grab_snapshot_from_client, stream_mjpeg, stream_rtsp_ffmpeg
from ...services.printer_client_factory import client_class, create_client, create_client_from_config, enabled_client_classes, printer_type_names
from ...services.printer_identity import IdentityError, declared_model, printer_model_catalog, resolve_legacy
from ...services import scheduling
from ...services.printer_manager import printer_manager
from ...services.queue_engine import queue_engine

async def _fetch_sidecar_catalog() -> Catalog | None:
    """Return the Themis-side catalog cache (never calls the slicing provider directly)."""
    try:
        return await catalog_service.get_cached_catalog()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Could not get catalog: %s", e)
        return None

router = APIRouter(prefix="/api/v1/printers", tags=["printers"])


class PrinterCreate(BaseModel):
    name: str
    # Identity: either the plugin/manufacturer/model triple, or a legacy printer_type (mapped to the triple).
    plugin_id: str | None = None
    manufacturer_id: str | None = None
    model_id: str | None = None
    printer_type: str | None = None
    connection_config: dict
    orca_printer_profiles: list[str] = []
    current_orca_printer_profile: str | None = None
    loaded_filaments: list[dict] = []
    build_plate_type: str | None = None
    no_snapshots_while_idle: bool = False
    bed_x_mm: float | None = None         # default: the declared model's bed
    bed_y_mm: float | None = None
    machine_rate_per_hour: float | None = Field(default=None, ge=0, le=100_000)


class PrinterUpdate(BaseModel):
    name: str | None = None
    manufacturer_id: str | None = None
    model_id: str | None = None
    connection_config: dict | None = None
    orca_printer_profiles: list[str] | None = None
    current_orca_printer_profile: str | None = None
    enabled: bool | None = None
    queue_on: bool | None = None
    loaded_filaments: list[dict] | None = None
    build_plate_type: str | None = None
    no_snapshots_while_idle: bool | None = None
    bed_x_mm: float | None = None
    bed_y_mm: float | None = None
    machine_rate_per_hour: float | None = Field(default=None, ge=0, le=100_000)  # null clears (use the shop rate)
    quiet_start: str | None = None
    quiet_end: str | None = None

    @model_validator(mode="after")
    def _quiet_hours_pair(self):
        for v in (self.quiet_start, self.quiet_end):
            if v is not None:
                try:
                    scheduling.parse_hhmm(v)
                except ValueError as e:
                    raise ValueError(str(e))
        if (self.quiet_start is None) != (self.quiet_end is None):
            raise ValueError("quiet_start and quiet_end must be set together (or both null)")
        return self


class ActivePresetUpdate(BaseModel):
    preset: str


class LightBody(BaseModel):
    on: bool


class JogZBody(BaseModel):
    distance_mm: float


class FanBody(BaseModel):
    fan: str  # "model" | "auxiliary" | "box"
    speed_pct: int


class BedTempBody(BaseModel):
    celsius: int


class JogBody(BaseModel):
    axis: Literal["X", "Y", "Z"]
    distance_mm: float = Field(..., ge=-200, le=200)


class HomeBody(BaseModel):
    axes: Literal["all", "X", "Y", "Z"] = "all"


class NozzleTempBody(BaseModel):
    celsius: int = Field(..., ge=0, le=350)


class ChamberTempBody(BaseModel):
    celsius: int = Field(..., ge=0, le=100)


def _to_dict(p: Printer) -> dict:
    live_client = printer_manager._clients.get(p.id)
    return {
        "id": p.id,
        "name": p.name,
        "printer_type": p.printer_type,
        "plugin_id": p.plugin_id,
        "manufacturer_id": p.manufacturer_id,
        "model_id": p.model_id,
        "connection_config": p.connection_config,
        "awaiting_plate_clear": p.awaiting_plate_clear,
        "orca_printer_profiles": p.orca_printer_profiles,
        "current_orca_printer_profile": p.current_orca_printer_profile,
        "enabled": p.enabled,
        "queue_on": p.queue_on,
        "loaded_filaments": p.loaded_filaments or [],
        "build_plate_type": p.build_plate_type,
        "no_snapshots_while_idle": p.no_snapshots_while_idle,
        "bed_x_mm": p.bed_x_mm,
        "bed_y_mm": p.bed_y_mm,
        "quiet_start": p.quiet_start,
        "quiet_end": p.quiet_end,
        "machine_rate_per_hour": p.machine_rate_per_hour,
        "connected": live_client.connected if live_client else False,
    }


async def _get_or_404(printer_id: int, session: AsyncSession) -> Printer:
    printer = await session.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(404, f"Printer {printer_id} not found")
    return printer


def _get_connected_client(printer_id: int):
    client = printer_manager._clients.get(printer_id)
    if client is None or not client.connected:
        raise HTTPException(503, "Printer not connected")
    return client


def _identity_for_create(body: PrinterCreate):
    """(plugin_id, manufacturer_id, model_id, stored printer_type, declared model). A legacy printer_type is mapped; an
    explicit triple must be declared by its plugin. Raises IdentityError (-> 422) before anything is stored."""
    if body.plugin_id is None and body.printer_type is not None:
        plugin_id, manufacturer_id, model_id = resolve_legacy(body.printer_type)
        printer_type = body.printer_type
    elif body.plugin_id is not None and body.manufacturer_id and body.model_id:
        plugin_id, manufacturer_id, model_id = body.plugin_id, body.manufacturer_id, body.model_id
        printer_type = plugin_id
    else:
        raise IdentityError("Give plugin_id, manufacturer_id and model_id, or a legacy printer_type")
    _, model = declared_model(plugin_id, manufacturer_id, model_id)
    return plugin_id, manufacturer_id, model_id, printer_type, model


@router.get("/types", summary="List printer models every plugin declares", dependencies=[Depends(require_scope("printers:read"))])
async def list_printer_types() -> list[dict]:
    """Available printer driver types with display name and required connection config fields."""
    return printer_model_catalog()


def _stem(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/")).lower()
    for ext in (".gcode.3mf", ".3mf", ".gcode", ".bgcode"):
        if base.endswith(ext):
            return base[: -len(ext)]
    return base


def _file_dict(f) -> dict:
    return {
        "id": f.id, "name": f.name, "size": f.size, "modified_at": f.modified_at, "is_dir": f.is_dir,
        "metadata": f.metadata,
        "printable": (not f.is_dir) and f.name.lower().endswith((".gcode", ".3mf", ".bgcode")),
    }


_LIST_TIMEOUT_S = 25


async def _list_printer_files(client, directory: str) -> list[dict]:
    files = await asyncio.wait_for(asyncio.to_thread(client.list_files, directory), _LIST_TIMEOUT_S)
    return sorted((_file_dict(f) for f in files), key=lambda d: (not d["is_dir"], d["name"].lower()))


@router.get(
    "/files/all",
    summary="Files stored on every printer",
    dependencies=[Depends(require_scope("printers:read"))],
)
async def list_all_printer_files(session: AsyncSession = Depends(get_session)) -> dict:
    """Merged view: the top-level files of every printer whose driver can list its storage, fetched
    concurrently. A printer that is offline or times out reports an `error` instead of failing the request."""
    printers = (await session.execute(select(Printer).order_by(Printer.id))).scalars().all()

    async def one(p: Printer) -> dict | None:
        entry = {"printer_id": p.id, "printer_name": p.name, "files": [], "error": None,
                 "can_delete": False, "can_download": False}
        client = printer_manager._clients.get(p.id)
        if client is None or not client.connected:
            entry["error"] = "Printer not connected"
            return entry
        caps = client.get_capabilities()
        if not caps.file_browser:
            return None
        entry["can_delete"], entry["can_download"] = caps.file_delete, caps.file_download
        try:
            entry["files"] = await _list_printer_files(client, "/")
        except asyncio.TimeoutError:
            entry["error"] = "Timed out listing files"
        except Exception as e:
            entry["error"] = f"Could not list files: {e}"
        return entry

    return {"printers": [e for e in await asyncio.gather(*(one(p) for p in printers)) if e is not None]}


@router.get("", summary="List printers", dependencies=[Depends(require_scope("printers:read"))])
async def list_printers(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """All configured printers with their current connection status."""
    result = await session.execute(select(Printer))
    return [_to_dict(p) for p in result.scalars().all()]


@router.post(
    "",
    status_code=201,
    summary="Create printer",
    responses={
        422: {"description": "Unknown printer_type"},
    },
    dependencies=[Depends(require_scope("printers:write"))],
)
async def create_printer(
    body: PrinterCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Register a new printer and attempt an immediate connection. Connection failure
    is non-fatal — the printer is saved and will retry on next restart."""
    try:
        plugin_id, manufacturer_id, model_id, printer_type, model = _identity_for_create(body)
    except IdentityError as e:
        raise HTTPException(422, str(e))
    printer = Printer(
        name=body.name,
        printer_type=printer_type,
        plugin_id=plugin_id,
        manufacturer_id=manufacturer_id,
        model_id=model_id,
        connection_config=body.connection_config,
        orca_printer_profiles=body.orca_printer_profiles,
        current_orca_printer_profile=body.current_orca_printer_profile,
        loaded_filaments=inventory_refs.normalize_slots(body.loaded_filaments),
        build_plate_type=body.build_plate_type,
        no_snapshots_while_idle=body.no_snapshots_while_idle,
        bed_x_mm=body.bed_x_mm if body.bed_x_mm is not None else model.bed_mm[0],
        bed_y_mm=body.bed_y_mm if body.bed_y_mm is not None else model.bed_mm[1],
        machine_rate_per_hour=body.machine_rate_per_hour,
    )
    session.add(printer)
    await session.commit()
    await session.refresh(printer)
    printer_manager.set_printer_plugin(printer.id, plugin_id)
    try:
        client = create_client(printer)
        printer_manager.connect_printer(printer.id, client)
    except Exception:
        pass  # non-fatal: printer saved, connection will retry on next restart
    return _to_dict(printer)


@router.get("/orca-presets", summary="List OrcaSlicer machine preset names",
           dependencies=[Depends(require_scope("printers:read"))])
async def list_orca_printer_presets() -> list[str]:
    """Sorted list of all OrcaSlicer machine preset names available in the sidecar catalog."""
    cat = await _fetch_sidecar_catalog()
    if cat is None:
        return []
    return sorted(cat.names("machine"))


@router.get("/orca-machine-catalog", summary="OrcaSlicer machine catalog",
           dependencies=[Depends(require_scope("printers:read"))])
async def orca_machine_catalog() -> list[dict]:
    """Selectable OrcaSlicer machine presets [{name, vendor, printer_model, nozzle,
    source, uuid}]. Sourced exclusively from the Orca sidecar."""
    cat = await _fetch_sidecar_catalog()
    if cat is None:
        return []
    return sorted(
        [
            {
                "name": m.name,
                "vendor": m.raw.get("manufacturer") or "",
                "printer_model": m.raw.get("model") or "",
                "nozzle": m.raw.get("nozzle") or "",
                "source": "system",
                "uuid": m.ref,
            }
            for m in cat.machines
            if m.name and m.raw.get("model") and m.raw.get("nozzle")
        ],
        key=lambda m: (m["vendor"], m["printer_model"], m["nozzle"], m["name"]),
    )


@router.post(
    "/rescan-profiles",
    summary="Rescan OrcaSlicer profiles",
    responses={
        502: {"description": "Laminus sidecar unreachable"},
    },
    dependencies=[Depends(require_scope("printers:write"))],
)
async def rescan_profiles(session: AsyncSession = Depends(get_session)) -> dict:
    """Trigger a catalog refresh from Orca and report the machine preset count."""
    try:
        await catalog_service.refresh(session)
    except catalog_service.CatalogUnavailable as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    cat = await _fetch_sidecar_catalog()
    if cat is None:
        return {"machine_presets": 0}
    count = sum(1 for m in cat.machines if m.raw.get("model") and m.raw.get("nozzle"))
    return {"machine_presets": count}


class TestConnectionRequest(BaseModel):
    printer_type: str
    connection_config: dict


class DiscoverRequest(BaseModel):
    ranges: list[str] = Field(default_factory=list, max_length=8)   # CIDR / single IP; empty = this host's /24


_discovery_lock = asyncio.Lock()


def _discovery_network():
    """Seam for tests: the network discovery runs over."""
    from ...services.discovery_net import RealNetwork
    return RealNetwork()


@router.post(
    "/discover",
    summary="Scan the network for printers",
    responses={422: {"description": "Malformed, oversized or non-private range"}},
    dependencies=[Depends(require_scope("printers:write"))],
)
async def discover_printers(body: DiscoverRequest, session: AsyncSession = Depends(get_session)) -> dict:
    """Sweep `ranges` (e.g. `192.168.7.0/24` — printers on another VLAN need their range given explicitly) and list
    what answers to a vendor's documented discovery signature. Private ranges only, at most a /20 each. Printers
    already added (same IP) are flagged. Secrets (access codes, API keys) are never discovered."""
    from ...services import discovery
    ranges = [r for r in body.ranges if r.strip()] or discovery.local_ranges()
    if not ranges:
        raise HTTPException(422, "Could not work out this host's network; pass a range such as 192.168.1.0/24")
    if _discovery_lock.locked():
        raise HTTPException(409, "A network scan is already running")       # each scan holds hundreds of sockets
    net = _discovery_network()
    try:
        async with _discovery_lock:
            result = await discovery.scan(net, ranges, enabled_client_classes())
    except discovery.ScanRangeError as e:
        raise HTTPException(422, str(e))
    finally:
        close = getattr(net, "aclose", None)
        if close is not None:
            await close()
    existing: set[str] = set()
    for p in (await session.execute(select(Printer))).scalars().all():
        cfg = p.connection_config or {}
        for key in ("ip_address", "host"):
            if cfg.get(key):
                existing.add(str(cfg[key]).strip())
    names = printer_type_names()
    return {
        "ranges": ranges, "scanned": result.scanned, "truncated": result.truncated,
        "found": [{
            "printer_type": d.printer_type, "display_name": names.get(d.printer_type, d.printer_type),
            "ip": d.ip, "model": d.model, "name": d.name, "serial": d.serial,
            "connection_config": d.connection_config, "note": d.note, "already_added": d.ip in existing,
        } for d in result.found],
    }


_TEST_CONNECT_POLL_S = 15.0  # MQTT/TLS handshake + first report can take well over 5s


async def _connect_failure_hint(client) -> str:
    """Classify a failed test connection by probing the control port, so the UI
    can say *why* (unreachable vs reached-but-login-failed) instead of a bare
    'Could not connect'."""
    try:
        endpoint = client.control_endpoint()
    except Exception:
        endpoint = None
    if not endpoint:
        return "Could not connect."
    host, port = endpoint

    def _probe() -> bool:
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except Exception:
            return False

    reachable = await asyncio.get_running_loop().run_in_executor(None, _probe)
    if reachable:
        return (f"Reached {host}:{port} but the login didn't complete — check the access code / "
                f"serial number, or the printer's single LAN connection is busy.")
    return (f"Couldn't reach {host}:{port}. The printer may be off or asleep, the IP may be wrong, "
            f"or another app is holding the printer's single LAN connection (Bambu allows only one).")


@router.post(
    "/test-connection",
    summary="Test printer connection",
    responses={
        422: {"description": "Unknown printer_type"},
    },
    dependencies=[Depends(require_scope("printers:read"))],
)
async def test_connection(body: TestConnectionRequest) -> dict:
    """Attempt to connect with the given config and return `{ok: true}` or
    `{ok: false, error: "..."}` with a human-readable hint about why it failed."""
    if client_class(body.printer_type) is None:
        raise HTTPException(422, f"Unknown printer_type: {body.printer_type!r}")
    client = None
    try:
        client = create_client_from_config(body.printer_type, body.connection_config)
        client.connect()
        deadline = asyncio.get_running_loop().time() + _TEST_CONNECT_POLL_S
        while asyncio.get_running_loop().time() < deadline and not client.connected:
            await asyncio.sleep(0.5)
        if client.connected:
            return {"ok": True}
        return {"ok": False, "error": await _connect_failure_hint(client)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass


@router.get(
    "/{printer_id}/profiles",
    summary="Get printer profiles",
    responses={
        404: {"description": "Printer not found"},
    },
    dependencies=[Depends(require_scope("printers:read"))],
)
async def get_profiles(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """OrcaSlicer print and filament profiles compatible with this printer's active machine preset."""
    printer = await _get_or_404(printer_id, session)
    if not printer.current_orca_printer_profile:
        return {"print_profiles": [], "filament_profiles": []}
    machine_name = printer.current_orca_printer_profile

    cat = await _fetch_sidecar_catalog()
    if cat is None:
        return {"print_profiles": [], "filament_profiles": []}

    slicer = get_format_provider()
    processes = sorted(p.name for p in slicer.compatible_presets(cat, machine_name, "process"))
    filaments = sorted(f.name for f in slicer.compatible_presets(cat, machine_name, "filament"))
    return {"print_profiles": processes, "filament_profiles": filaments}


@router.get(
    "/{printer_id}",
    summary="Get printer",
    responses={
        404: {"description": "Printer not found"},
    },
    dependencies=[Depends(require_scope("printers:read"))],
)
async def get_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return _to_dict(await _get_or_404(printer_id, session))


@router.patch(
    "/{printer_id}",
    summary="Update printer",
    responses={
        404: {"description": "Printer not found"},
    },
    dependencies=[Depends(require_scope("printers:write"))],
)
async def update_printer(
    printer_id: int,
    body: PrinterUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Update one or more printer fields. Omitted fields are left unchanged.
    `current_orca_printer_profile` supports explicit null to clear the preset."""
    printer = await _get_or_404(printer_id, session)
    if body.manufacturer_id is not None or body.model_id is not None:
        # validated before anything is changed: a rejected identity leaves the printer exactly as it was
        manufacturer_id = body.manufacturer_id or printer.manufacturer_id or ""
        model_id = body.model_id or printer.model_id or ""
        try:
            declared_model(printer.plugin_id or "", manufacturer_id, model_id)
        except IdentityError as e:
            raise HTTPException(422, str(e))
        printer.manufacturer_id, printer.model_id = manufacturer_id, model_id
    if body.name is not None:
        printer.name = body.name
    if body.connection_config is not None:
        printer.connection_config = body.connection_config
    if body.orca_printer_profiles is not None:
        printer.orca_printer_profiles = body.orca_printer_profiles
    # Use model_fields_set so an explicit null clears the preset (EditForm sends
    # null to unset make/model); an omitted key leaves it unchanged.
    if "current_orca_printer_profile" in body.model_fields_set:
        printer.current_orca_printer_profile = body.current_orca_printer_profile
    if body.enabled is not None:
        printer.enabled = body.enabled
    if body.queue_on is not None:
        printer.queue_on = body.queue_on
    if body.loaded_filaments is not None:
        # whole-list replace from the client: resolve each slot against what is stored (dual-write, see inventory/refs.py)
        printer.loaded_filaments = inventory_refs.normalize_slots(body.loaded_filaments, printer.loaded_filaments)
    if "build_plate_type" in body.model_fields_set:
        printer.build_plate_type = body.build_plate_type
    if body.no_snapshots_while_idle is not None:
        printer.no_snapshots_while_idle = body.no_snapshots_while_idle
    if body.bed_x_mm is not None:
        printer.bed_x_mm = body.bed_x_mm
    if body.bed_y_mm is not None:
        printer.bed_y_mm = body.bed_y_mm
    if "quiet_start" in body.model_fields_set or "quiet_end" in body.model_fields_set:
        printer.quiet_start, printer.quiet_end = body.quiet_start, body.quiet_end
    if "machine_rate_per_hour" in body.model_fields_set:
        printer.machine_rate_per_hour = body.machine_rate_per_hour
    await session.commit()
    await session.refresh(printer)
    return _to_dict(printer)


@router.delete(
    "/{printer_id}",
    status_code=204,
    summary="Delete printer",
    responses={
        404: {"description": "Printer not found"},
        409: {"description": "Printer has an active job"},
    },
    dependencies=[Depends(require_scope("printers:write"))],
)
async def delete_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    from datetime import datetime, timezone

    printer = await _get_or_404(printer_id, session)

    # Refuse while the printer is physically working on a job — deleting the row
    # can't stop the machine, and it would strand the job with no way to resolve it.
    active = (await session.execute(
        select(Job.id).where(
            Job.assigned_printer_id == printer_id,
            Job.status.in_(["printing", "paused", "uploading"]),
        ).limit(1)
    )).first()
    if active is not None:
        raise HTTPException(409, "Printer has an active job — stop or cancel it first")

    # Jobs that use this printer's config, captured before we delete it, so we can
    # detect any left with zero remaining configs (otherwise invisible to the queue).
    affected_job_ids = (await session.execute(
        select(JobPrinterConfig.job_id).where(JobPrinterConfig.printer_id == printer_id).distinct()
    )).scalars().all()

    gcode_rows = (await session.execute(
        select(GcodeFile).where(GcodeFile.printer_id == printer_id)
    )).scalars().all()
    for gcode in gcode_rows:
        try:
            os.remove(gcode.path)
        except OSError:
            pass
    await session.execute(delete(GcodeFile).where(GcodeFile.printer_id == printer_id))
    await session.execute(delete(JobPrinterConfig).where(JobPrinterConfig.printer_id == printer_id))
    await session.execute(
        update(Job)
        .where(Job.assigned_printer_id == printer_id)
        .values(assigned_printer_id=None)
    )
    await session.execute(
        update(Job).where(Job.printed_on_printer_id == printer_id).values(printed_on_printer_id=None)
    )

    if affected_job_ids:
        remaining = set((await session.execute(
            select(JobPrinterConfig.job_id).where(JobPrinterConfig.job_id.in_(affected_job_ids)).distinct()
        )).scalars().all())
        # A job with a make/model target isn't orphaned: another (or a future) printer of that model can take it.
        targeted = set((await session.execute(
            select(JobModelTarget.job_id).where(JobModelTarget.job_id.in_(affected_job_ids)).distinct()
        )).scalars().all())
        orphaned_ids = [jid for jid in affected_job_ids if jid not in remaining and jid not in targeted]
        if orphaned_ids:
            await session.execute(
                update(Job)
                .where(Job.id.in_(orphaned_ids), Job.status.in_(["queued", "blocked"]))
                .values(
                    status="blocked",
                    block_reason="No printer configured for this job",
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            )

    await session.delete(printer)
    await session.commit()
    printer_manager.disconnect_printer(printer_id)
    if printer_manager._on_state_broadcast is not None:   # its alarms went with it (FK cascade)
        await printer_manager._on_state_broadcast("alarms_changed", {"printer_id": printer_id})


@router.post(
    "/{printer_id}/plate-cleared",
    summary="Confirm plate cleared",
    responses={
        404: {"description": "Printer not found"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def plate_cleared(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Mark the printer's bed as cleared so the queue engine can assign the next job."""
    printer = await _get_or_404(printer_id, session)
    printer.awaiting_plate_clear = False
    await session.commit()
    printer_manager.set_awaiting_plate_clear(printer_id, False)
    queue_engine.wake()
    return {"ok": True}


@router.patch(
    "/{printer_id}/active-preset",
    summary="Switch active OrcaSlicer preset",
    responses={
        404: {"description": "Printer not found"},
        422: {"description": "Preset not in this printer's configured profiles"},
    },
    dependencies=[Depends(require_scope("printers:write"))],
)
async def switch_active_preset(
    printer_id: int,
    body: ActivePresetUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Change the active OrcaSlicer machine preset. The preset must already be in
    the printer's `orca_printer_profiles` list."""
    printer = await _get_or_404(printer_id, session)
    if body.preset not in (printer.orca_printer_profiles or []):
        raise HTTPException(422, f"Preset {body.preset!r} not in this printer's configured profiles")
    printer.current_orca_printer_profile = body.preset
    await session.commit()
    await session.refresh(printer)
    return _to_dict(printer)


@router.post(
    "/{printer_id}/reconnect",
    summary="Reconnect printer",
    responses={
        404: {"description": "Printer not found"},
        503: {"description": "Connection attempt failed"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def reconnect_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Disconnect the current session and open a fresh connection."""
    printer = await _get_or_404(printer_id, session)
    printer_manager.disconnect_printer(printer_id)
    try:
        client = create_client(printer)
        printer_manager.connect_printer(printer_id, client)
    except Exception as exc:
        raise HTTPException(503, f"Failed to connect: {exc}")
    return {"ok": True}


@router.post(
    "/{printer_id}/pause",
    summary="Pause print",
    responses={
        404: {"description": "Printer not found"},
        503: {"description": "Printer not connected"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def pause_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.pause_print()
    return {"ok": True}


@router.post(
    "/{printer_id}/resume",
    summary="Resume print",
    responses={
        404: {"description": "Printer not found"},
        503: {"description": "Printer not connected"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def resume_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.resume_print()
    return {"ok": True}


@router.post(
    "/{printer_id}/stop",
    summary="Stop print",
    responses={
        404: {"description": "Printer not found"},
        503: {"description": "Printer not connected"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def stop_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Stop the active print and cancel any Themis job the printer was physically running."""
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.stop_print()
    # Reconcile any Themis job this printer was physically running so it doesn't
    # stay stuck "printing" after the machine is stopped.
    from datetime import datetime, timezone
    from ...models import Job
    result = await session.execute(
        select(Job).where(
            Job.assigned_printer_id == printer_id,
            Job.status.in_(["printing", "paused", "uploading"]),
        )
    )
    now = datetime.now(timezone.utc).isoformat()
    for job in result.scalars().all():
        job.status = "cancelled"
        job.assigned_printer_id = None
        job.queue_position = None
        job.updated_at = now
    await session.commit()
    return {"ok": True}


@router.post(
    "/{printer_id}/light",
    summary="Set chamber light",
    responses={
        404: {"description": "Printer not found"},
        503: {"description": "Printer not connected"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def set_light(
    printer_id: int,
    body: LightBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.set_chamber_light(body.on)
    return {"ok": True}


@router.post(
    "/{printer_id}/jog-z",
    summary="Jog Z axis",
    responses={
        404: {"description": "Printer not found"},
        503: {"description": "Printer not connected"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def jog_z(
    printer_id: int,
    body: JogZBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.jog_z(body.distance_mm)
    return {"ok": True}


@router.post(
    "/{printer_id}/fan",
    summary="Set fan speed",
    responses={
        404: {"description": "Printer not found"},
        422: {"description": "Invalid fan name"},
        503: {"description": "Printer not connected"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def set_fan(
    printer_id: int,
    body: FanBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Set fan speed (0–100 %) for `model`, `auxiliary`, or `box` fan."""
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    state = printer_manager.get_normalized_state(printer_id)
    model = int(state.get("fan_model", 0))
    aux = int(state.get("fan_aux", 0))
    box = int(state.get("fan_box", 0))
    if body.fan == "model":
        model = body.speed_pct
    elif body.fan == "auxiliary":
        aux = body.speed_pct
    elif body.fan == "box":
        box = body.speed_pct
    else:
        raise HTTPException(422, f"Invalid fan name: {body.fan!r}. Valid: model, auxiliary, box")
    client.set_fan_speeds(model, aux, box)
    return {"ok": True}


@router.post(
    "/{printer_id}/bed-temp",
    summary="Set bed temperature",
    responses={
        404: {"description": "Printer not found"},
        503: {"description": "Printer not connected"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def set_bed_temp(
    printer_id: int,
    body: BedTempBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.set_bed_temp(body.celsius)
    return {"ok": True}


def _require_capability(client, name: str, what: str) -> None:
    if not getattr(client.get_capabilities(), name, False):
        raise HTTPException(409, f"This printer does not support {what}")


def _require_not_printing(client) -> None:
    """Motion and nozzle changes need a printer that is positively idle (the queue's own notion), not merely
    "not printing": states like Bambu's PREPARE (heating/levelling) are neither."""
    if client.is_printing or not client.is_idle:
        raise HTTPException(409, "Printer is busy; wait for it to be idle (or stop the print) first")


def _ok_or_502(result) -> dict:
    if not result:
        raise HTTPException(502, "Printer rejected the command")
    return {"ok": True}


_CONSOLE_RESPONSES = {
    404: {"description": "Printer not found"},
    409: {"description": "Not supported by this printer, or the printer is printing"},
    502: {"description": "Printer rejected the command"},
    503: {"description": "Printer not connected"},
}


@router.post(
    "/{printer_id}/jog",
    summary="Jog an axis",
    responses=_CONSOLE_RESPONSES,
    dependencies=[Depends(require_scope("printers:control"))],
)
async def jog_axis(printer_id: int, body: JogBody, session: AsyncSession = Depends(get_session)) -> dict:
    """Relative move of X, Y or Z by `distance_mm` (negative = other direction). X/Y need the `axis_jog`
    capability; refused while a print is running."""
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    if body.axis != "Z":
        _require_capability(client, "axis_jog", "X/Y jog")
    _require_not_printing(client)
    if body.axis == "Z":      # vendors with a native Z command (Elegoo SDCP) override jog_z, not jog
        return _ok_or_502(await asyncio.to_thread(client.jog_z, body.distance_mm))
    return _ok_or_502(await asyncio.to_thread(client.jog, body.axis, body.distance_mm))


@router.post(
    "/{printer_id}/home",
    summary="Home axes",
    responses=_CONSOLE_RESPONSES,
    dependencies=[Depends(require_scope("printers:control"))],
)
async def home_printer(printer_id: int, body: HomeBody, session: AsyncSession = Depends(get_session)) -> dict:
    """Home all axes, or a single axis (needs the `home_axes` capability). Refused while printing."""
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    _require_not_printing(client)
    if body.axes == "all":
        return _ok_or_502(await asyncio.to_thread(client.home))
    _require_capability(client, "home_axes", "single-axis homing")
    return _ok_or_502(await asyncio.to_thread(client.home_axes, body.axes))


@router.post(
    "/{printer_id}/nozzle-temp",
    summary="Set nozzle temperature",
    responses=_CONSOLE_RESPONSES,
    dependencies=[Depends(require_scope("printers:control"))],
)
async def set_nozzle_temp(printer_id: int, body: NozzleTempBody, session: AsyncSession = Depends(get_session)) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    _require_capability(client, "nozzle_temp", "a nozzle setpoint")
    _require_not_printing(client)      # M104 mid-print would cool the nozzle and ruin the print
    return _ok_or_502(await asyncio.to_thread(client.set_nozzle_temp, body.celsius))


@router.post(
    "/{printer_id}/chamber-temp",
    summary="Set chamber temperature",
    responses=_CONSOLE_RESPONSES,
    dependencies=[Depends(require_scope("printers:control"))],
)
async def set_chamber_temp(printer_id: int, body: ChamberTempBody, session: AsyncSession = Depends(get_session)) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    _require_capability(client, "chamber_temp", "a chamber setpoint")
    return _ok_or_502(await asyncio.to_thread(client.set_chamber_temp, body.celsius))


_direct_start_locks: dict[tuple[int, int], asyncio.Lock] = {}     # keyed by (event loop, printer)


async def _start_stored_file(client, file_id: str, failure: str) -> None:
    from ...services.abstract_printer_client import StartPrintOptions
    opts = StartPrintOptions(gcode_path=os.path.basename(file_id))     # same options the queue engine passes
    if not await asyncio.to_thread(client.start_print, file_id, opts):
        raise HTTPException(502, failure)


async def _direct_start(session: AsyncSession, client, printer_id: int, steps) -> None:
    """Run `steps` (which end in a start_print) on a printer that is idle, uncleared-plate-free and not held by
    a queue job. The printer is claimed (plate gate set) BEFORE the steps so the queue engine cannot race them,
    and released again if nothing ended up printing."""
    # One direct start per printer at a time: the checks below await, so without this two concurrent requests
    # could both pass them before either has claimed the printer.
    async with _direct_start_locks.setdefault((id(asyncio.get_running_loop()), printer_id), asyncio.Lock()):
        if not client.is_idle:
            raise HTTPException(409, "Printer is not idle")
        if printer_manager.is_awaiting_plate_clear(printer_id):
            raise HTTPException(409, "The previous plate has not been cleared; mark the printer ready first")
        busy = await session.execute(
            select(Job.id).where(Job.assigned_printer_id == printer_id, Job.status.in_(_BUSY_JOB_STATUSES)).limit(1))
        if busy.first() is not None:
            raise HTTPException(409, "A queued job currently holds this printer")
        printer = await session.get(Printer, printer_id)
        await _set_plate_gate(session, printer, printer_id, True)
        started = False
        try:
            await steps()
            started = True
        finally:
            if not started:
                await _set_plate_gate(session, printer, printer_id, False)


async def _set_plate_gate(session: AsyncSession, printer, printer_id: int, value: bool) -> None:
    printer_manager.set_awaiting_plate_clear(printer_id, value)
    if printer is not None:
        printer.awaiting_plate_clear = value
        await session.commit()


_UPLOAD_EXTENSIONS = (".gcode", ".gcode.3mf", ".3mf", ".bgcode")
_MAX_DIRECT_UPLOAD_BYTES = 300 * 1024 * 1024
_BUSY_JOB_STATUSES = ("slicing", "sliced", "uploading", "printing", "paused")


@router.post(
    "/{printer_id}/upload",
    summary="Upload a file to the printer, bypassing the queue",
    responses={
        **_CONSOLE_RESPONSES,
        413: {"description": "File too large"},
        422: {"description": "Unsupported file type"},
    },
    dependencies=[Depends(require_scope("printers:control"))],
)
async def upload_to_printer(
    printer_id: int,
    file: UploadFile = File(...),
    start: bool = Form(False),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Send a .gcode / .3mf / .bgcode file straight to the printer. With `start`, also begin printing it —
    only when the printer is idle and no queued job holds it; the plate is then marked not-ready (same
    gate as a queue-started print)."""
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    _require_capability(client, "direct_upload", "direct upload")
    name = os.path.basename((file.filename or "").replace("\\", "/"))
    if not name or not name.lower().endswith(_UPLOAD_EXTENSIONS) or any(c in name for c in "\x00\r\n"):
        raise HTTPException(422, f"Unsupported file; expected one of {', '.join(_UPLOAD_EXTENSIONS)}")
    data = await file.read(_MAX_DIRECT_UPLOAD_BYTES + 1)
    if len(data) > _MAX_DIRECT_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 300 MB")
    if not start:
        if not await asyncio.to_thread(client.upload_file, data, name):
            raise HTTPException(502, "Printer rejected the upload")
        return {"ok": True, "filename": name, "started": False}

    async def steps() -> None:
        if not await asyncio.to_thread(client.upload_file, data, name):
            raise HTTPException(502, "Printer rejected the upload")
        await _start_stored_file(client, name, "Uploaded, but the printer would not start the print")

    await _direct_start(session, client, printer_id, steps)
    return {"ok": True, "filename": name, "started": True}


class FileRef(BaseModel):
    file_id: str = Field(..., min_length=1, max_length=1024)


_FILE_RESPONSES = {
    **_CONSOLE_RESPONSES,
    409: {"description": "Not supported by this printer, or the printer/queue state forbids it"},
}


@router.get(
    "/{printer_id}/files",
    summary="List files stored on a printer",
    responses=_FILE_RESPONSES,
    dependencies=[Depends(require_scope("printers:read"))],
)
async def list_files_on_printer(
    printer_id: int, directory: str = "/", session: AsyncSession = Depends(get_session),
) -> dict:
    """One directory of the printer's own storage (`directory` is an entry id from a previous listing, `/` for
    the root). Includes slicer metadata (print time, filament) where the protocol offers it."""
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    _require_capability(client, "file_browser", "browsing its files")
    try:
        files = await _list_printer_files(client, directory)
    except asyncio.TimeoutError:
        raise HTTPException(502, "Timed out listing files")
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:                       # login refused, port closed, 4xx/5xx from the printer…
        raise HTTPException(502, f"Could not list files: {e}")
    caps = client.get_capabilities()
    return {"printer_id": printer_id, "directory": directory, "files": files,
            "can_delete": caps.file_delete, "can_download": caps.file_download}


@router.post(
    "/{printer_id}/files/print",
    summary="Print a file already stored on the printer",
    responses=_FILE_RESPONSES,
    dependencies=[Depends(require_scope("printers:control"))],
)
async def print_stored_file(printer_id: int, body: FileRef, session: AsyncSession = Depends(get_session)) -> dict:
    """Start a stored file. Same gate as a direct upload-and-print: the printer must be idle, its plate cleared
    and not held by a queue job; it is then marked not-ready like any started print."""
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    _require_capability(client, "file_browser", "browsing its files")
    if not body.file_id.lower().endswith((".gcode", ".3mf", ".bgcode")):
        raise HTTPException(422, "Not a printable file")

    async def steps() -> None:
        await _start_stored_file(client, body.file_id, "The printer would not start the print")

    await _direct_start(session, client, printer_id, steps)
    return {"ok": True, "file_id": body.file_id, "started": True}


@router.delete(
    "/{printer_id}/files",
    summary="Delete a file from the printer",
    responses=_FILE_RESPONSES,
    dependencies=[Depends(require_scope("printers:control"))],
)
async def delete_stored_file(printer_id: int, file_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Permanently remove a file from the printer's storage. Refused for the file currently being printed."""
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    _require_capability(client, "file_delete", "deleting files")
    if not client.is_idle:
        # Not idle covers the start-up window too (heating/levelling) when state isn't RUNNING yet. Firmwares
        # report the running file inconsistently (with/without extension, with a path), so compare stems, and
        # when the name is unknown refuse rather than guess.
        current = printer_manager.get_normalized_state(printer_id).get("current_print") or ""
        if not current or _stem(current) == _stem(file_id):
            raise HTTPException(409, "That file is (or may be) being printed right now")
    return _ok_or_502(await asyncio.to_thread(client.delete_file, file_id))


@router.post(
    "/{printer_id}/files/to-library",
    summary="Copy a file from the printer into the file library",
    responses={**_FILE_RESPONSES, 413: {"description": "File too large"}, 422: {"description": "Not a library file type"}},
    dependencies=[Depends(require_scope("files:write"))],
)
async def copy_stored_file_to_library(
    printer_id: int, body: FileRef, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session),
) -> dict:
    """Download a stored .3mf / .stl from the printer and add it to the library (folder `/From Printers`,
    deduplicated by content like any upload). Sliced output (.gcode, .gcode.3mf) is refused: this adds models."""
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    _require_capability(client, "file_download", "downloading files")
    name = os.path.basename(body.file_id)
    if not name.lower().endswith((".3mf", ".stl")) or is_presliced_name(name):
        raise HTTPException(422, "Only .3mf and .stl models can be added to the library (not sliced gcode)")
    from ...services.abstract_printer_client import FileTooLargeError
    try:
        data = await asyncio.to_thread(client.download_file, body.file_id, _MAX_DIRECT_UPLOAD_BYTES)
    except FileTooLargeError:
        raise HTTPException(413, "File is larger than 300 MB")     # aborted mid-transfer, never fully buffered
    if data is None:
        raise HTTPException(502, "The printer would not hand over the file")
    from io import BytesIO
    from starlette.datastructures import UploadFile as StarletteUpload
    from .files import upload_file as library_upload
    # The route's own BackgroundTasks, so the thumbnail job the library upload queues actually runs.
    return await library_upload(StarletteUpload(file=BytesIO(data), filename=name), background_tasks,
                                "/From Printers", session)


async def _activate_camera(client) -> None:
    """Enable the camera stream; runs the blocking call off the event loop."""
    if hasattr(client, "start_video_stream"):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, client.start_video_stream)


@router.get(
    "/{printer_id}/camera",
    summary="Stream camera (MJPEG)",
    responses={
        404: {"description": "Printer not found or has no camera"},
        503: {"description": "Printer not connected or ffmpeg unavailable for RTSP"},
    },
    dependencies=[Depends(require_scope("printers:read"))],
)
async def stream_camera(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """MJPEG multipart stream from the printer camera. Supports both native MJPEG
    and RTSP (transcoded via ffmpeg). A keepalive ping prevents stream drops on
    printers that time out after 60 s of inactivity."""
    await _get_or_404(printer_id, session)
    client = printer_manager._clients.get(printer_id)
    if client is None or not client.connected:
        raise HTTPException(503, "Printer not connected")
    caps = client.get_capabilities()
    if not caps.camera:
        raise HTTPException(404, "This printer has no camera")

    if client.camera_mjpeg_url:
        mjpeg_url, rtsp_url = client.camera_mjpeg_url, None
    elif client.camera_rtsp_url:
        from ...config import get_ffmpeg_executable
        if not shutil.which(get_ffmpeg_executable()):
            raise HTTPException(503, "ffmpeg not available for RTSP streaming")
        mjpeg_url, rtsp_url = None, client.camera_rtsp_url
    else:
        raise HTTPException(404, "No camera URL configured")

    if not camera_hub.hub.has_stream(printer_id):
        await _activate_camera(client)                 # before the response starts, so a failure is a real 5xx

    async def upstream():
        raw = stream_mjpeg(mjpeg_url) if mjpeg_url else stream_rtsp_ffmpeg(rtsp_url)
        async for chunk in raw:
            yield chunk

    # One upstream connection / ffmpeg per printer however many viewers (camera wall, several browsers);
    # Elegoo drops the MJPEG stream after 60 s of silence, so the hub pings it every 45 s.
    ping = client.ping_video_stream if hasattr(client, "ping_video_stream") else None
    if camera_hub.hub.is_full_for(printer_id):
        raise HTTPException(429, "Too many camera streams open")

    async def _stream():
        frames = camera_hub.hub.subscribe(printer_id, upstream, ping)
        try:
            async for part in frames:
                yield part
        except camera_hub.HubFull:
            return
        finally:
            await frames.aclose()

    return StreamingResponse(
        _stream(),
        media_type=f"multipart/x-mixed-replace; boundary={camera_hub.BOUNDARY}",
    )


@router.get(
    "/{printer_id}/snapshot",
    summary="Camera snapshot",
    responses={
        404: {"description": "Printer not found, has no camera, or no camera source"},
        503: {"description": "Printer not connected or camera unavailable"},
    },
    dependencies=[Depends(require_scope("printers:read"))],
)
async def snapshot_camera(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Return a single JPEG frame from any camera source (MJPEG or RTSP)."""
    await _get_or_404(printer_id, session)
    client = printer_manager._clients.get(printer_id)
    if client is None or not client.connected:
        raise HTTPException(503, "Printer not connected")
    caps = client.get_capabilities()
    if not caps.camera:
        raise HTTPException(404, "This printer has no camera")

    async def grab():
        await _activate_camera(client)           # only when a real grab happens, not on a cache/live-frame hit
        return await grab_snapshot_from_client(client)

    try:
        jpeg = await camera_hub.hub.snapshot(printer_id, grab)
    except Exception as exc:
        raise HTTPException(503, f"Camera unavailable: {exc}")

    if jpeg is None:
        raise HTTPException(404, "No camera source available")

    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )
