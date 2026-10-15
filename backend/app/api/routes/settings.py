from __future__ import annotations

import json
from datetime import datetime, timezone


from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import CostConfig, Printer, PrinterModelRecord, PrinterModelSlicerProfile, QueueConfig
from ...services import slicer_profiles, webhook_service
from ...services import printer_model_registry as model_registry
from ...services import catalog_service
from ...services.inventory import refs as inventory_refs
from ...services.printer_client_factory import client_class, create_client
from ...services.printer_identity import IdentityError, LEGACY_IDENTITY, resolve_legacy
from ...services.printer_manager import printer_manager

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

_BACKUP_VERSION = 1


class QueueConfigOut(BaseModel):
    check_interval_minutes: int
    operator_name: str | None
    snapshot_interval_seconds: int
    estimates_enabled: bool
    # Slicing cache (BIZ-191): reslice a cached version whose presets/slicer changed (True) or still print it (False).
    slice_cache_use_latest_settings: bool


class QueueConfigIn(BaseModel):
    check_interval_minutes: int | None = None
    operator_name: str | None = None
    snapshot_interval_seconds: int | None = None
    estimates_enabled: bool | None = None
    slice_cache_use_latest_settings: bool | None = None


class CostConfigModel(BaseModel):
    """Shop-wide hourly rates used for every project's expenses (applied live — see services/job_costs.py)."""
    machine_rate_per_hour: float = Field(default=0.0, ge=0, le=100_000)
    labour_rate_per_hour: float = Field(default=0.0, ge=0, le=100_000)


@router.get("/costs", response_model=CostConfigModel, summary="Get the shop cost model",
            dependencies=[Depends(require_scope("settings:read"))])
async def get_cost_config(session: AsyncSession = Depends(get_session)):
    row = await session.get(CostConfig, 1)
    return CostConfigModel(machine_rate_per_hour=row.machine_rate_per_hour if row else 0.0,
                           labour_rate_per_hour=row.labour_rate_per_hour if row else 0.0)


@router.put("/costs", response_model=CostConfigModel, summary="Set the shop cost model",
            dependencies=[Depends(require_scope("settings:write"))])
async def put_cost_config(body: CostConfigModel, session: AsyncSession = Depends(get_session)):
    """Re-prices every project's machine and labour expenses immediately — past jobs included."""
    row = await session.get(CostConfig, 1)
    if row is None:
        row = CostConfig(id=1)
        session.add(row)
    row.machine_rate_per_hour = body.machine_rate_per_hour
    row.labour_rate_per_hour = body.labour_rate_per_hour
    await session.commit()
    return body


async def _get_or_create_queue(session: AsyncSession) -> QueueConfig:
    row = await session.get(QueueConfig, 1)
    if row is None:
        row = QueueConfig(id=1, check_interval_minutes=5, snapshot_interval_seconds=2, estimates_enabled=False,
                          slice_cache_use_latest_settings=True)
        session.add(row)
        await session.flush()
    return row


@router.get("/queue", response_model=QueueConfigOut, summary="Get queue config",
           dependencies=[Depends(require_scope("settings:read"))])
async def get_queue_config(session: AsyncSession = Depends(get_session)):
    """Queue engine settings: poll interval, operator name, snapshot interval, background estimates, and the slicing
    cache's stale-version policy."""
    return await _get_or_create_queue(session)


@router.put("/queue", response_model=QueueConfigOut, summary="Update queue config",
           dependencies=[Depends(require_scope("settings:write"))])
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
    if body.slice_cache_use_latest_settings is not None:
        row.slice_cache_use_latest_settings = body.slice_cache_use_latest_settings
    await session.commit()
    await session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Webhook config
# ---------------------------------------------------------------------------

class WebhookConfigOut(BaseModel):
    url: str | None
    has_secret: bool
    events: list[str]


class WebhookConfigIn(BaseModel):
    url: str | None = None
    secret: str | None = None
    events: list[str] | None = None


def _webhook_out(row) -> WebhookConfigOut:
    return WebhookConfigOut(url=row.url if row else None, has_secret=bool(row and row.secret is not None),
                            events=list(row.events or []) if row else [])


@router.get("/webhook", response_model=WebhookConfigOut, summary="Get webhook config",
           dependencies=[Depends(require_scope("settings:read"))])
async def get_webhook_config(session: AsyncSession = Depends(get_session)):
    """The `default` outbound webhook (see `/api/v1/webhooks` for any number of destinations): endpoint URL, whether an HMAC secret is
    set, and subscribed event types. The secret itself never round-trips - omit `secret` on PUT to leave it unchanged."""
    return _webhook_out(await webhook_service.default_destination(session))


@router.put("/webhook", response_model=WebhookConfigOut, summary="Update webhook config",
           dependencies=[Depends(require_scope("settings:write"))])
async def update_webhook_config(
    body: WebhookConfigIn,
    session: AsyncSession = Depends(get_session),
):
    """Update webhook settings. Omitted fields (including `secret`) are left unchanged; an
    empty string for `secret` clears it."""
    row = await webhook_service.default_destination(session, create=True)
    if body.url is not None:
        row.url = body.url or None
    if body.secret is not None:
        row.secret = body.secret or None
    if body.events is not None:
        row.events = body.events
    row.updated_at = webhook_service.now_iso()
    await session.commit()
    await session.refresh(row)
    return _webhook_out(row)


# ---------------------------------------------------------------------------
# Notification config (ntfy / Discord / email)

# ---------------------------------------------------------------------------
# Fleet backup / restore
# ---------------------------------------------------------------------------

def _redact_connection_config(printer_type: str, cfg: dict) -> dict:
    """Blank out credential fields (access codes, API keys, passwords) before a
    connection_config is written to a downloadable file. Which fields are secret is
    driven by each printer client's own `connection_fields()` declaration
    (`field_type == "password"`), not a hardcoded key list, so a new vendor client is
    covered automatically as long as it marks its own secret fields correctly."""
    cls = client_class(printer_type)
    if cls is None:
        return dict(cfg)
    secret_names = {f.name for f in cls.connection_fields() if f.field_type == "password"}
    return {k: ("" if k in secret_names else v) for k, v in cfg.items()}


@router.get(
    "/fleet-backup",
    summary="Download fleet backup",
    responses={},
    dependencies=[Depends(require_scope("settings:read"))],
)
async def fleet_backup(
    include_credentials: bool = False,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Export all printer configs as a downloadable JSON file.

    By default, credential fields in each printer's `connection_config` (access codes,
    API keys — whatever that printer's client marks as `field_type: "password"`) are
    blanked out. The file is a plaintext download that ends up in ~/Downloads and
    whatever syncs it; printer LAN credentials shouldn't ride along by default. Pass
    `include_credentials=true` to export them anyway (e.g. for a personal encrypted
    backup you control end to end).

    The response has `Content-Disposition: attachment; filename=themis-fleet-backup.json`
    so browsers will prompt to save it. Import with `POST /settings/fleet-import`."""
    result = await session.execute(select(Printer))
    printers = result.scalars().all()
    # The model -> slicer machine profile mappings (BIZ-294) are what a restored fleet slices with: keyed by the model's
    # plugin/manufacturer/model identity, since the registry UUID is local to this install.
    models = {m.id: m for m in (await session.execute(select(PrinterModelRecord))).scalars()}
    mappings = [
        {"plugin_id": models[r.model_uuid].plugin_id, "manufacturer_id": models[r.model_uuid].manufacturer_id,
         "model_id": models[r.model_uuid].model_id, "provider_plugin_id": r.provider_plugin_id,
         "nozzle_diameter_mm": r.nozzle_diameter_mm, "flow": r.flow, "machine_profile": r.machine_profile,
         "is_default": r.is_default}
        for r in await slicer_profiles.mapping_rows(session) if r.model_uuid in models]

    data = {
        "themis_backup_version": _BACKUP_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "credentials_redacted": not include_credentials,
        "printers": [
            {
                "name": p.name,
                "printer_type": p.printer_type,
                "plugin_id": p.plugin_id,
                "manufacturer_id": p.manufacturer_id,
                "model_id": p.model_id,
                "connection_config": (
                    (p.connection_config or {}) if include_credentials
                    else _redact_connection_config(p.printer_type, p.connection_config or {})
                ),
                "orca_printer_profiles": p.orca_printer_profiles or [],
                "machine_profile_override": p.machine_profile_override,
                "nozzle_variant": p.nozzle_variant,
                "loaded_filaments": p.loaded_filaments or [],
                "build_plate_type": p.build_plate_type,
                "enabled": p.enabled,
                "queue_on": p.queue_on,
            }
            for p in printers
        ],
        "slicer_profiles": mappings,
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
    dependencies=[Depends(require_scope("settings:write"))],
)
async def fleet_import(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> FleetImportReport:
    """Import printer configs from a backup file. Profile resolution failures are non-fatal.

    Backups exported without `include_credentials=true` (the default) have blank
    credential fields — imported printers will need those re-entered before they can
    connect; the report's `warnings` flags which ones.

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
    cat = None
    try:
        cat = await catalog_service.get_cached_catalog()
    except Exception:
        pass

    machine_names: set[str] = set()
    filament_names: set[str] = set()
    if cat is not None:
        from ...services.catalog_utils import catalog_name_sets
        machine_names, _, filament_names, _ = catalog_name_sets(cat)

    warnings: list[str] = []
    imported = 0
    skipped = 0

    await model_registry.sync_registry(session)
    for m in data.get("slicer_profiles") or []:
        try:
            provider = m["provider_plugin_id"]
            uuid_ = await model_registry.model_uuid_for(session, m["plugin_id"], m["manufacturer_id"], m["model_id"])
            variant = slicer_profiles.NozzleVariant(float(m["nozzle_diameter_mm"]), m.get("flow") or "standard")
            if uuid_ is None or variant.flow not in slicer_profiles.FLOWS or not m.get("machine_profile") or variant.diameter_mm <= 0:
                raise ValueError("unknown model or malformed row")
        except (KeyError, TypeError, ValueError):
            warnings.append(f"slicer profile mapping skipped — {m!r}")
            continue
        rows = await slicer_profiles.mapping_rows(session, uuid_)
        mine = [r for r in rows if r.provider_plugin_id == provider]
        if any(slicer_profiles.variant_of(r) == variant for r in mine):
            continue                                  # an existing mapping on this install wins
        # the file's default wins only when this install has none; a model/provider is never left without a default, so the first
        # imported row is promoted when the file marks none
        session.add(PrinterModelSlicerProfile(
            model_uuid=uuid_, provider_plugin_id=provider, nozzle_diameter_mm=variant.diameter_mm,
            flow=variant.flow, machine_profile=m["machine_profile"],
            is_default=not mine or (bool(m.get("is_default")) and not any(r.is_default for r in mine))))
        await session.flush()

    for pr in data.get("printers", []):
        pname = pr.get("name") or "Unnamed Printer"
        ptype = pr.get("printer_type", "")

        has_identity = bool(pr.get("plugin_id") and pr.get("manufacturer_id") and pr.get("model_id"))
        # An old backup carries only the legacy `printer_type`; one with no legacy mapping must not be adopted by whichever plugin
        # happens to share that key now (that would silently pick a connection provider): it is skipped, never guessed.
        if client_class(ptype) is None or (not has_identity and ptype not in LEGACY_IDENTITY):
            warnings.append(f"'{pname}': skipped — unknown printer type '{ptype}'")
            skipped += 1
            continue

        orca_profiles: list[str] = pr.get("orca_printer_profiles") or []
        # `current_orca_printer_profile` is the pre-BIZ-294 name of the override
        current_profile: str | None = pr.get("machine_profile_override") or pr.get("current_orca_printer_profile")
        loaded: list[dict] = pr.get("loaded_filaments") or []
        conn_cfg: dict = pr.get("connection_config") or {}

        secret_names = {f.name for f in client_class(ptype).connection_fields() if f.field_type == "password"}
        blank_secrets = sorted(n for n in secret_names if not conn_cfg.get(n))
        if blank_secrets:
            warnings.append(
                f"'{pname}': credentials not included in this backup "
                f"({', '.join(blank_secrets)}) — re-enter before connecting"
            )

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

        if pr.get("plugin_id") and pr.get("manufacturer_id") and pr.get("model_id"):
            # a backup's own identity is kept as written: if its plugin is not installed here the printer is imported dormant
            # (plugin_removed) rather than lost or guessed
            identity = (pr["plugin_id"], pr["manufacturer_id"], pr["model_id"])
        else:                                   # an older backup only has the legacy printer_type
            try:
                identity = resolve_legacy(ptype)
            except IdentityError:               # a type with no legacy mapping (only a hand-edited file): no identity to invent
                identity = (None, None, None)
        model_uuid = await model_registry.model_uuid_for(session, *identity) if identity[0] else None
        printer = Printer(
            name=pname,
            printer_type=ptype,
            model_uuid=model_uuid,
            plugin_id=identity[0],
            manufacturer_id=identity[1],
            model_id=identity[2],
            connection_config=conn_cfg,
            orca_printer_profiles=orca_profiles,
            machine_profile_override=current_profile,
            nozzle_variant=slicer_profiles.NozzleVariant.parse(pr.get("nozzle_variant")).as_dict()
            if slicer_profiles.NozzleVariant.parse(pr.get("nozzle_variant")) else None,
            loaded_filaments=inventory_refs.normalize_slots(loaded),
            build_plate_type=pr.get("build_plate_type"),
            enabled=pr.get("enabled", True),
            queue_on=pr.get("queue_on", True),
        )
        session.add(printer)
        await session.flush()
        printer_manager.set_printer_plugin(printer.id, printer.plugin_id)

        try:
            client = create_client(printer)
            printer_manager.connect_printer(printer.id, client)
        except Exception:
            pass

        imported += 1

    await session.commit()

    return FleetImportReport(imported=imported, skipped=skipped, warnings=warnings)
