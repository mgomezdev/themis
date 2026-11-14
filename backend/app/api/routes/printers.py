from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AliasChoices, BaseModel, Field, model_validator
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


from ...auth import require_scope
from ...database import get_session
from ...models import GcodeFile, Job, JobModelTarget, JobPrinterConfig, Printer
from ...plugins import providers_of
from ...services import catalog_service
from ...services.inventory import refs as inventory_refs
from ...services.providers.slicing import Catalog, default_compatible_presets, get_slicing_provider
from ...services.printer_client_factory import (
    client_class, enabled_client_classes, printer_type_names, printer_type_plugins,
)
from ...services import printer_model_registry as registry
from ...services.printer_identity import IdentityError, declared_model, dormant_reason, printer_model_catalog, resolve_legacy
from ...services import filament_matching, printer_setup, scheduling, slicer_profiles
from ...services.printer_manager import printer_manager
from ...services.printer_client_factory import create_client
from ...services.queue_engine import queue_engine

async def _fetch_sidecar_catalog() -> Catalog | None:
    """Return the Themis-side catalog cache (never calls the slicing provider directly)."""
    try:
        return await catalog_service.get_cached_catalog()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Could not get catalog: %s", e)
        return None


# Collaborators the modules imported at the bottom of this file call as `_printers.<name>` rather than importing
# themselves: tests swap them out on THIS module, so this is the one place they are looked up.
_PATCH_POINTS = (create_client, queue_engine, printer_manager)

router = APIRouter(prefix="/api/v1/printers", tags=["printers"])


class PrinterCreate(BaseModel):
    name: str | None = None               # default: "<model> <last IP octet>"
    # Identity: either the plugin/manufacturer/model triple, or a legacy printer_type (mapped to the triple).
    plugin_id: str | None = None
    manufacturer_id: str | None = None
    model_id: str | None = None
    model_uuid: str | None = None         # the registry's stable id for the model, instead of the triple
    printer_type: str | None = None
    connection_config: dict
    orca_printer_profiles: list[str] = []
    # advanced: a raw slicer machine profile that wins over the model mapping (BIZ-294); the pre-BIZ-294 name is still accepted on input
    machine_profile_override: str | None = Field(default=None, validation_alias=AliasChoices("machine_profile_override", "current_orca_printer_profile"))
    nozzle_variant: dict | None = None              # {"diameter_mm", "flow"}; must be mapped for the model
    loaded_filaments: list[dict] = []
    build_plate_type: str | None = None
    no_snapshots_while_idle: bool = False
    bed_x_mm: float | None = Field(default=None, gt=0)         # default: the declared model's bed
    bed_y_mm: float | None = Field(default=None, gt=0)
    bed_z_mm: float | None = Field(default=None, gt=0, le=5000)   # BIZ-363: build height (null = the machine profile's)
    machine_rate_per_hour: float | None = Field(default=None, ge=0, le=100_000)
    default_process_profile: str | None = None      # BIZ-358: what a "Printer default" job config slices with
    single_colour_only: bool = False


class PrinterUpdate(BaseModel):
    name: str | None = None
    manufacturer_id: str | None = None
    model_id: str | None = None
    connection_config: dict | None = None
    orca_printer_profiles: list[str] | None = None
    machine_profile_override: str | None = Field(default=None, validation_alias=AliasChoices("machine_profile_override", "current_orca_printer_profile"))   # explicit null clears
    nozzle_variant: dict | None = None             # explicit null = the model's default variant
    enabled: bool | None = None
    queue_on: bool | None = None
    loaded_filaments: list[dict] | None = None
    build_plate_type: str | None = None
    no_snapshots_while_idle: bool | None = None
    bed_x_mm: float | None = Field(default=None, gt=0)
    bed_y_mm: float | None = Field(default=None, gt=0)
    bed_z_mm: float | None = Field(default=None, gt=0, le=5000)   # explicit null clears (use the machine profile's height)
    machine_rate_per_hour: float | None = Field(default=None, ge=0, le=100_000)  # null clears (use the shop rate)
    quiet_start: str | None = None
    quiet_end: str | None = None
    # The `gcode.produce` plugin this printer slices with (BIZ-291); null clears it (use the capability default).
    preferred_slicer_plugin_id: str | None = None
    default_process_profile: str | None = None      # explicit null (or "") clears it
    single_colour_only: bool | None = None
    # BIZ-365: flag / stop the printer at this many waived parts (0 = off)
    waiver_flag_at: int | None = Field(default=None, ge=0, le=1000)
    waiver_stop_at: int | None = Field(default=None, ge=0, le=1000)

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


class PartResult(BaseModel):
    project_item_id: int
    quantity_failed: int = Field(ge=0)


class PlateResults(BaseModel):
    """What printed on the plate being cleared (BIZ-364). Parts not listed all succeeded."""
    job_id: Optional[int] = None
    results: list[PartResult] = []
    clear_waiver: bool = False        # BIZ-365: the job was a test print and it came out fine: clear its printer's waiver


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


async def _check_variant(session: AsyncSession, model_uuid: str | None, raw: dict | None) -> dict | None:
    """A posted nozzle variant must be one the printer's model has a mapping for (any provider); returns its canonical form."""
    if raw is None:
        return None
    variant = slicer_profiles.NozzleVariant.parse(raw)
    if variant is None:
        raise HTTPException(422, "nozzle_variant must be {diameter_mm > 0, flow: standard|high_flow}")
    rows = await slicer_profiles.mapping_rows(session, model_uuid) if model_uuid else []
    if not any(slicer_profiles.variant_of(r) == variant for r in rows):
        label = await slicer_profiles.model_display_name(session, model_uuid) or "this printer"
        raise HTTPException(422, f"No slicer profile is mapped for {label} {variant.label}; map it in Settings > Printer models")
    return variant.as_dict()


async def _check_default_process(session: AsyncSession, printer: Printer, name: str | None) -> str | None:
    """A default process profile must be one of the process presets compatible with the printer's machine profile (BIZ-358)."""
    if not name:
        return None
    machine = await slicer_profiles.machine_profile_for(session, printer)
    if not machine:
        raise HTTPException(422, f"{printer.name} has no machine profile yet: map its model in Settings > Printer models first")
    cat = await _fetch_sidecar_catalog()
    if cat is None:
        raise HTTPException(422, "The slicer's profile catalog is unavailable, so the process profile can't be checked; try again later")
    slicer = get_slicing_provider()
    compat = slicer.compatible_presets if slicer else default_compatible_presets
    if name not in {p.name for p in compat(cat, machine, "process")}:
        raise HTTPException(422, f"Process profile {name!r} is not compatible with {machine!r}")
    return name


def _to_dict(p: Printer, res: slicer_profiles.MachineProfileResolution | None = None) -> dict:
    live_client = printer_manager._clients.get(p.id)
    return {
        "id": p.id,
        "name": p.name,
        "printer_type": p.printer_type,
        "plugin_id": p.plugin_id,
        "manufacturer_id": p.manufacturer_id,
        "model_id": p.model_id,
        "model_uuid": p.model_uuid,
        "connection_config": p.connection_config,
        "awaiting_plate_clear": p.awaiting_plate_clear,
        "orca_printer_profiles": p.orca_printer_profiles,
        "machine_profile_override": p.machine_profile_override,
        "nozzle_variant": p.nozzle_variant,
        # The machine profile this printer slices with right now (override, else model mapping + variant); null + reason when unmapped.
        "machine_profile": res.profile if res else None,
        "machine_profile_reason": res.reason if res else None,
        "model_label": res.model_label if res else None,      # "<manufacturer> <model>" of the registry model, for pickers
        "enabled": p.enabled,
        "queue_on": p.queue_on,
        "configured": p.configured,
        "config_error": p.config_error,
        "loaded_filaments": p.loaded_filaments or [],
        "build_plate_type": p.build_plate_type,
        "no_snapshots_while_idle": p.no_snapshots_while_idle,
        "bed_x_mm": p.bed_x_mm,
        "bed_y_mm": p.bed_y_mm,
        "bed_z_mm": p.bed_z_mm,
        "quiet_start": p.quiet_start,
        "quiet_end": p.quiet_end,
        "machine_rate_per_hour": p.machine_rate_per_hour,
        "preferred_slicer_plugin_id": p.preferred_slicer_plugin_id,
        "default_process_profile": p.default_process_profile,
        "single_colour_only": bool(p.single_colour_only),
        "waiver_flag_at": p.waiver_flag_at or 0,
        "waiver_stop_at": p.waiver_stop_at or 0,
        "test_print_job_id": p.test_print_job_id,
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


def _factory_of(session: AsyncSession) -> async_sessionmaker:
    """Sessions on the request's database, for work that outlives the request (the background connection test)."""
    return async_sessionmaker(session.bind, class_=AsyncSession, expire_on_commit=False)


def _ip_of(p: Printer) -> str | None:
    cfg = p.connection_config or {}
    for key in ("ip_address", "host"):
        if cfg.get(key):
            return str(cfg[key]).strip()
    return None


def _same_model(p: Printer, plugin_id, manufacturer_id, model_id, model_uuid) -> bool:
    """A printer's model is its registry UUID when both sides have one, else its plugin/manufacturer/model triple."""
    if p.model_uuid and model_uuid:
        return p.model_uuid == model_uuid
    return (p.plugin_id, p.manufacturer_id, p.model_id) == (plugin_id, manufacturer_id, model_id)


def _identity_for_create(body: PrinterCreate, registered=None):
    """(plugin_id, manufacturer_id, model_id, stored printer_type, declared model). A legacy printer_type is mapped; an
    explicit triple must be declared by its plugin. Raises IdentityError (-> 422) before anything is stored."""
    if body.model_uuid is not None and body.plugin_id is None and not body.manufacturer_id and not body.model_id:
        if registered is None:
            raise IdentityError(f"Unknown printer model: {body.model_uuid!r}")
        plugin_id, manufacturer_id, model_id = registered.plugin_id, registered.manufacturer_id, registered.model_id
        printer_type = getattr(client_class(plugin_id), "printer_type", plugin_id)
    elif body.plugin_id is None and body.printer_type is not None:
        plugin_id, manufacturer_id, model_id = resolve_legacy(body.printer_type)
        printer_type = body.printer_type
    elif body.plugin_id is not None and body.manufacturer_id and body.model_id:
        plugin_id, manufacturer_id, model_id = body.plugin_id, body.manufacturer_id, body.model_id
        # the legacy column keeps the client's own key (e.g. snapmaker_extended), which badges and old consumers still read
        printer_type = getattr(client_class(plugin_id), "printer_type", plugin_id)
    else:
        raise IdentityError("Give model_uuid, plugin_id + manufacturer_id + model_id, or a legacy printer_type")
    _, model = declared_model(plugin_id, manufacturer_id, model_id)
    if dormant_reason(plugin_id) == "plugin_disabled":
        raise IdentityError(f"Plugin {plugin_id!r} is disabled; enable it before adding printers")
    return plugin_id, manufacturer_id, model_id, printer_type, model


@router.get("/types", summary="List printer models every plugin declares", dependencies=[Depends(require_scope("printers:read"))])
async def list_printer_types(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Available printer driver types with display name and required connection config fields. Each entry carries the
    registry's stable `model_uuid` and whether the user has `model_enabled` it for setup (the add-printer flow offers only those)."""
    await registry.sync_registry(session)
    await session.commit()
    ids = {(m["plugin_id"], m["manufacturer_id"], m["model_id"]): m for m in await registry.list_models(session)}
    out = []
    for entry in printer_model_catalog():
        reg = ids.get((entry["plugin_id"], entry["manufacturer_id"], entry["model_id"]))
        out.append({**entry, "model_uuid": reg["id"] if reg else None, "model_enabled": reg["enabled"] if reg else True})
    return out


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
    printers = list((await session.execute(select(Printer))).scalars().all())
    resolved = await slicer_profiles.resolve_many(session, printers)
    return [_to_dict(p, resolved[p.id]) for p in printers]


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
    """Register a printer. Only its identity (model + `connection_config.ip_address`) is required; blank connection values are
    dropped. It is saved unconfigured and a connection test runs in the background: success makes it `configured` (only
    configured printers take queue work), failure records `config_error`. 409 when a printer of the same model already uses
    that IP."""
    cfg = printer_setup.strip_blank(body.connection_config)
    await registry.sync_registry(session)
    registered = await registry.get_model(session, body.model_uuid) if body.model_uuid else None
    try:
        plugin_id, manufacturer_id, model_id, printer_type, model = _identity_for_create(body, registered)
    except IdentityError as e:
        raise HTTPException(422, str(e))
    model_uuid = await registry.model_uuid_for(session, plugin_id, manufacturer_id, model_id)
    record = await registry.get_model(session, model_uuid) if model_uuid else None
    if record is not None and not record.enabled and body.printer_type is None:
        raise HTTPException(422, f"Model {record.display_name!r} is disabled in the printer-model registry")
    model_name = record.display_name if record else model.name
    identity = SimpleNamespace(plugin_id=plugin_id, printer_type=printer_type, manufacturer_id=manufacturer_id, model_id=model_id)
    has_ip = any(f.name == "ip_address" for f in printer_setup.fields_for(identity))   # a plugin with no address (e.g. a mock) needs none
    ip = str(cfg.get("ip_address") or "").strip()
    if has_ip and not ip:
        raise HTTPException(422, "connection_config.ip_address is required")
    if ip:
        for other in (await session.execute(select(Printer))).scalars().all():
            if _ip_of(other) == ip and _same_model(other, plugin_id, manufacturer_id, model_id, model_uuid):
                raise HTTPException(409, f"{other.name} already uses {ip} as {model_name}")
    printer = Printer(
        model_uuid=model_uuid,
        name=body.name or (printer_setup.default_name(model_name, ip) if ip else model_name),
        configured=False,
        printer_type=printer_type,
        plugin_id=plugin_id,
        manufacturer_id=manufacturer_id,
        model_id=model_id,
        connection_config=cfg,
        orca_printer_profiles=body.orca_printer_profiles,
        machine_profile_override=body.machine_profile_override,
        nozzle_variant=await _check_variant(session, model_uuid, body.nozzle_variant),
        loaded_filaments=inventory_refs.normalize_slots(body.loaded_filaments),
        build_plate_type=body.build_plate_type,
        no_snapshots_while_idle=body.no_snapshots_while_idle,
        bed_x_mm=body.bed_x_mm if body.bed_x_mm is not None else model.bed_mm[0],
        bed_y_mm=body.bed_y_mm if body.bed_y_mm is not None else model.bed_mm[1],
        bed_z_mm=body.bed_z_mm,
        machine_rate_per_hour=body.machine_rate_per_hour,
        single_colour_only=body.single_colour_only,
    )
    printer.default_process_profile = await _check_default_process(session, printer, body.default_process_profile)
    session.add(printer)
    await session.commit()
    await session.refresh(printer)
    printer_manager.set_printer_plugin(printer.id, plugin_id)
    printer_manager.set_configured(printer.id, False)
    printer_setup.schedule(printer.id, _factory_of(session))
    return _to_dict(printer, await slicer_profiles.resolve_for_printer(session, printer))


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
    plugin_id: str | None = None


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
    what answers to a vendor's documented discovery signature. Trailing x/* wildcards are accepted. Private ranges
    only, at most 65,536 addresses total. Optional plugin_id limits probes to one enabled printer plug-in. Printers
    already added (same IP) are flagged. Secrets (access codes, API keys) are never discovered."""
    from ...services import discovery
    ranges = [r for r in body.ranges if r.strip()] or discovery.local_ranges()
    if not ranges:
        raise HTTPException(422, "Could not work out this host's network; pass a range such as 192.168.1.0/24")
    classes = enabled_client_classes()
    if body.plugin_id is not None:
        plugin_ids = printer_type_plugins()
        classes = {key: cls for key, cls in classes.items() if plugin_ids.get(key) == body.plugin_id}
        if not classes:
            raise HTTPException(422, "Choose an enabled printer plug-in")
    if _discovery_lock.locked():
        raise HTTPException(409, "A network scan is already running")       # each scan holds hundreds of sockets
    net = _discovery_network()
    try:
        async with _discovery_lock:
            result = await discovery.scan(net, ranges, classes)
    except discovery.ScanRangeError as e:
        raise HTTPException(422, str(e))
    finally:
        close = getattr(net, "aclose", None)
        if close is not None:
            await close()
    by_ip: dict[str, list[Printer]] = {}
    for p in (await session.execute(select(Printer).order_by(Printer.id))).scalars().all():
        if (ip := _ip_of(p)) is not None:
            by_ip.setdefault(ip, []).append(p)
    names = printer_type_names()
    plugin_ids = printer_type_plugins()
    await registry.sync_registry(session)
    await session.commit()
    labels = {m["id"]: m["display_name"] for m in await registry.list_models(session)}
    found = []
    for d in result.found:
        plugin_id = plugin_ids.get(d.printer_type)
        # A discovered free-text model matches a registry entry only when exactly one enabled model fits; otherwise the UI offers
        # the explicit custom/unmatched path (`model_uuid` is None).
        model_uuid = await registry.match_free_text(session, d.model, plugin_id=plugin_id)
        here = by_ip.get(d.ip, [])
        # (IP, model) is the printer; with no matched model the same plugin at that IP is taken to be it (BIZ-369)
        added = next((p for p in here if (p.model_uuid == model_uuid if model_uuid and p.model_uuid
                                          else p.plugin_id == plugin_id)), None)
        other = next((p for p in here if p is not added), None) if added is None else None
        found.append({
            "model_uuid": model_uuid,
            "printer_type": d.printer_type, "plugin_id": plugin_id,
            "display_name": names.get(d.printer_type, d.printer_type),
            "ip": d.ip, "model": d.model, "name": d.name, "serial": d.serial,
            "connection_config": d.connection_config, "note": d.note,
            "already_added": added is not None,
            "added_printer": None if added is None else {"printer_id": added.id, "name": added.name,
                                                         "configured": added.configured, "config_error": added.config_error},
            "ip_in_use_by": None if other is None else {"printer_id": other.id, "name": other.name,
                                                        "model": labels.get(other.model_uuid) or other.model_id},
        })
    return {"ranges": ranges, "scanned": result.scanned, "truncated": result.truncated, "found": found}


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
    out = await printer_setup.test_unsaved(body.printer_type, body.connection_config)
    return {"ok": True} if out.ok else {"ok": False, "error": out.error}


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
    machine_name = await slicer_profiles.machine_profile_for(session, printer)
    if not machine_name:
        return {"print_profiles": [], "filament_profiles": []}

    cat = await _fetch_sidecar_catalog()
    if cat is None:
        return {"print_profiles": [], "filament_profiles": []}

    slicer = get_slicing_provider()   # may be None: the catalog is a cached copy, matching needs no live server
    compat = slicer.compatible_presets if slicer else default_compatible_presets
    processes = sorted(p.name for p in compat(cat, machine_name, "process"))
    filaments = sorted(f.name for f in compat(cat, machine_name, "filament"))
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
    printer = await _get_or_404(printer_id, session)
    return _to_dict(printer, await slicer_profiles.resolve_for_printer(session, printer))


@router.get(
    "/{printer_id}/slot-conflicts",
    summary="Slots whose bound spool differs from what the printer reports",
    responses={
        404: {"description": "Printer not found"},
    },
    dependencies=[Depends(require_scope("printers:read"))],
)
async def slot_conflicts(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Indexes of the slots in conflict (a hardware-reported slot whose bound spool's material differs in a part known on both
    sides). A conflicting slot is skipped by the queue's filament matching; the UI flags it so the operator can rebind, correct or
    unbind the spool. Empty when no slot is bound or the inventory cannot be read."""
    printer = await _get_or_404(printer_id, session)
    return {"conflicts": sorted(await filament_matching.conflicts_for(printer.loaded_filaments or []))}


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
    `machine_profile_override` and `nozzle_variant` support explicit null to clear them."""
    printer = await _get_or_404(printer_id, session)
    if body.manufacturer_id is not None or body.model_id is not None:
        # validated before anything is changed: a rejected identity leaves the printer exactly as it was
        manufacturer_id = body.manufacturer_id or printer.manufacturer_id or ""
        model_id = body.model_id or printer.model_id or ""
        try:
            declared_model(printer.plugin_id or "", manufacturer_id, model_id)
        except IdentityError as e:
            raise HTTPException(422, str(e))
        await registry.sync_registry(session)      # commits: done before any field below is touched
        printer.manufacturer_id, printer.model_id = manufacturer_id, model_id
        new_uuid = await registry.model_uuid_for(session, printer.plugin_id or "", manufacturer_id, model_id)
        if new_uuid != printer.model_uuid:
            # A different model: the old model's nozzle variant and raw override do not carry over. The variant is kept only when the new
            # model maps the same one (else cleared to NULL = the new model's default, never a silent 422 on an identity edit); the override
            # is cleared unless this same request sets it.
            current = slicer_profiles.NozzleVariant.parse(printer.nozzle_variant)
            if "nozzle_variant" not in body.model_fields_set and current is not None:
                rows = await slicer_profiles.mapping_rows(session, new_uuid) if new_uuid else []
                if not any(slicer_profiles.variant_of(r) == current for r in rows):
                    printer.nozzle_variant = None
            if "machine_profile_override" not in body.model_fields_set:
                printer.machine_profile_override = None
        printer.model_uuid = new_uuid
    if body.name is not None:
        printer.name = body.name
    retest = False
    if body.connection_config is not None and printer_setup.connection_changed(printer.connection_config, body.connection_config):
        # only a real change to a field it connects with voids the confirmed setup; a per-print option is just stored (BIZ-369)
        retest = printer_setup.connection_changed(printer.connection_config, body.connection_config,
                                                  printer_setup.fields_for(printer))
        live = printer_manager._clients.get(printer.id)
        if retest and live is not None and getattr(live, "connected", False) is True and getattr(live, "is_printing", False) is True:
            raise HTTPException(409, "The printer is printing: change how Themis connects to it once the print is done")
        printer.connection_config = printer_setup.strip_blank(body.connection_config)
        if retest:
            printer.configured, printer.config_error = False, None
    if body.orca_printer_profiles is not None:
        printer.orca_printer_profiles = body.orca_printer_profiles
    # Use model_fields_set so an explicit null clears the preset (EditForm sends
    # null to unset make/model); an omitted key leaves it unchanged.
    if "machine_profile_override" in body.model_fields_set:
        printer.machine_profile_override = body.machine_profile_override
    if "nozzle_variant" in body.model_fields_set:
        printer.nozzle_variant = await _check_variant(session, printer.model_uuid, body.nozzle_variant)
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
    if "bed_z_mm" in body.model_fields_set:
        printer.bed_z_mm = body.bed_z_mm
    if "quiet_start" in body.model_fields_set or "quiet_end" in body.model_fields_set:
        printer.quiet_start, printer.quiet_end = body.quiet_start, body.quiet_end
    if "machine_rate_per_hour" in body.model_fields_set:
        printer.machine_rate_per_hour = body.machine_rate_per_hour
    if "preferred_slicer_plugin_id" in body.model_fields_set:
        wanted = body.preferred_slicer_plugin_id or None
        if wanted is not None and wanted not in {m.id for m in providers_of("gcode.produce")}:
            raise HTTPException(422, f"{wanted!r} is not a plugin that provides gcode.produce")
        printer.preferred_slicer_plugin_id = wanted
    if "default_process_profile" in body.model_fields_set and (body.default_process_profile or None) != printer.default_process_profile:
        # re-validated only when it changes: an unrelated edit must not 422 because the catalog is down or the profile went stale
        printer.default_process_profile = await _check_default_process(session, printer, body.default_process_profile)
    if body.single_colour_only is not None:
        printer.single_colour_only = body.single_colour_only
    if body.waiver_flag_at is not None:
        printer.waiver_flag_at = body.waiver_flag_at
    if body.waiver_stop_at is not None:
        printer.waiver_stop_at = body.waiver_stop_at
    await session.commit()
    await session.refresh(printer)
    if retest:
        printer_manager.set_configured(printer.id, False)
        printer_manager.disconnect_printer(printer.id)      # the old connection must not keep printing/claiming on stale settings
        printer_setup.schedule(printer.id, _factory_of(session))
    queue_engine.wake()                      # enabled, queue on or loaded filament changed: what the queue can match may have too
    return _to_dict(printer, await slicer_profiles.resolve_for_printer(session, printer))


@router.post(
    "/{printer_id}/test",
    summary="Test a saved printer's connection",
    responses={404: {"description": "Printer not found"}},
    dependencies=[Depends(require_scope("printers:control"))],
)
async def test_saved_printer(printer_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Connect the printer with its stored config. Success makes it `configured` (and leaves it connected); failure records why in
    `config_error`. A printer missing a required field is not contacted."""
    await _get_or_404(printer_id, session)
    out = await printer_setup.evaluate(printer_id, _factory_of(session))
    session.expire_all()
    printer = await _get_or_404(printer_id, session)
    return {"ok": out.ok, "error": out.error, "configured": printer.configured, "config_error": printer.config_error}


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
    printer_manager.forget_printer(printer_id)
    if printer_manager._on_state_broadcast is not None:   # its alarms went with it (FK cascade)
        await printer_manager._on_state_broadcast("alarms_changed", {"printer_id": printer_id})


# ---- the rest of the printers routes ---------------------------------------------------------------
# Imported last: each module below registers its routes on `router` (so they match after the ones above, in this order)
# and uses the names defined above. Their names are re-exported here for existing importers.
from .printer_plate import (  # noqa: E402,F401,I001
    cancel_test_print,  # noqa: F401
    clear_all_waivers,  # noqa: F401
    clear_waiver,  # noqa: F401
    list_waivers,  # noqa: F401
    plate_cleared,  # noqa: F401
    plate_results,  # noqa: F401
    test_print_waiver,  # noqa: F401
)
from .printer_controls import (  # noqa: E402,F401,I001
    _CONSOLE_RESPONSES,  # noqa: F401
    _direct_start_locks,  # noqa: F401
    _ok_or_502,  # noqa: F401
    _require_capability,  # noqa: F401
    _require_not_printing,  # noqa: F401
    home_printer,  # noqa: F401
    jog_axis,  # noqa: F401
    jog_z,  # noqa: F401
    pause_printer,  # noqa: F401
    reconnect_printer,  # noqa: F401
    resume_printer,  # noqa: F401
    set_bed_temp,  # noqa: F401
    set_chamber_temp,  # noqa: F401
    set_fan,  # noqa: F401
    set_light,  # noqa: F401
    set_nozzle_temp,  # noqa: F401
    stop_printer,  # noqa: F401
)
from .printer_files import (  # noqa: E402,F401,I001
    FileRef,  # noqa: F401
    _BUSY_JOB_STATUSES,  # noqa: F401
    _FILE_RESPONSES,  # noqa: F401
    _MAX_DIRECT_UPLOAD_BYTES,  # noqa: F401
    _UPLOAD_EXTENSIONS,  # noqa: F401
    _direct_start,  # noqa: F401
    _set_plate_gate,  # noqa: F401
    _start_stored_file,  # noqa: F401
    copy_stored_file_to_library,  # noqa: F401
    delete_stored_file,  # noqa: F401
    list_files_on_printer,  # noqa: F401
    print_stored_file,  # noqa: F401
    upload_to_printer,  # noqa: F401
)
from .printer_camera import (  # noqa: E402,F401,I001
    _activate_camera,  # noqa: F401
    snapshot_camera,  # noqa: F401
    stream_camera,  # noqa: F401
)
