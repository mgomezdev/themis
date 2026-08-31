from __future__ import annotations

import json
from datetime import datetime, timezone

from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import NotificationConfig, Printer, QueueConfig, SpoolmanConfig, WebhookConfig
from ...services import spoolman_service
from ...services.notification_service import send_discord, send_email, send_ntfy
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


@router.get("/queue", response_model=QueueConfigOut, summary="Get queue config",
           dependencies=[Depends(require_scope("settings:read"))])
async def get_queue_config(session: AsyncSession = Depends(get_session)):
    """Queue engine settings: poll interval, operator name, and snapshot interval."""
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


@router.get("/spoolman", response_model=SpoolmanConfigOut, summary="Get Spoolman config",
           dependencies=[Depends(require_scope("settings:read"))])
async def get_spoolman_config(session: AsyncSession = Depends(get_session)):
    """Spoolman integration settings: enabled flag, base URL, and API key."""
    return await _get_or_create(session)


@router.put("/spoolman", response_model=SpoolmanConfigOut, summary="Update Spoolman config",
           dependencies=[Depends(require_scope("settings:write"))])
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


@router.post("/spoolman/test", summary="Test Spoolman connection",
            dependencies=[Depends(require_scope("settings:write"))])
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

    # --- Spoolman profile-name sanity check (best-effort) ---
    # Check that profile name strings in each filament's orca_profiles exist in the catalog.
    import app.api.routes.laminus as _lam_mod
    from ...services.catalog_utils import catalog_name_sets

    _catalog = _lam_mod._catalog_dict
    if _catalog is not None:
        try:
            _, _, catalog_filaments, _ = catalog_name_sets(_catalog)
            spool_filaments = await spoolman_service.fetch_filaments(url, api_key)
            spoolman_groups: dict[tuple[str, str], dict] = {}
            for fil in spool_filaments:
                raw_extra = (fil.get("extra") or {}).get("orca_profiles")
                if not raw_extra:
                    continue
                try:
                    profiles: dict = json.loads(json.loads(raw_extra))
                except Exception:
                    continue
                for printer_preset, names in profiles.items():
                    if not isinstance(names, list):
                        continue
                    for name in names:
                        if name not in catalog_filaments:
                            key = (printer_preset, name)
                            g = spoolman_groups.setdefault(key, {
                                "printer_preset": printer_preset,
                                "stale_name": name,
                                "required": False,
                                "affected_filament_ids": [],
                                "affected_filament_names": [],
                            })
                            g["affected_filament_ids"].append(fil["id"])
                            g["affected_filament_names"].append(fil.get("name", str(fil["id"])))

            if spoolman_groups:
                import uuid as _uuid
                import time as _time
                sync_id = str(_uuid.uuid4())
                pending_entries = list(spoolman_groups.values())
                _lam_mod._pending_sync = {
                    "sync_id": sync_id,
                    "raw": None,
                    "catalog": None,
                    "pending": {
                        "printers": [],
                        "jobs": [],
                        "spoolman_filaments": pending_entries,
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


@router.get("/webhook", response_model=WebhookConfigOut, summary="Get webhook config",
           dependencies=[Depends(require_scope("settings:read"))])
async def get_webhook_config(session: AsyncSession = Depends(get_session)):
    """Outbound webhook settings: endpoint URL, HMAC secret, and subscribed event types."""
    return await _get_or_create_webhook(session)


@router.put("/webhook", response_model=WebhookConfigOut, summary="Update webhook config",
           dependencies=[Depends(require_scope("settings:write"))])
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
# Notification config (ntfy / Discord / email)
# ---------------------------------------------------------------------------

_TEST_TITLE = "Themis test notification"
_TEST_MESSAGE = "This is a test notification from Themis."


class NtfyChannelConfig(BaseModel):
    enabled: bool = False
    server_url: str | None = None
    topic: str | None = None
    events: list[str] = []


class DiscordChannelConfig(BaseModel):
    enabled: bool = False
    webhook_url: str | None = None
    events: list[str] = []


class EmailChannelConfig(BaseModel):
    enabled: bool = False
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    from_addr: str | None = None
    to_addrs: list[str] = []
    events: list[str] = []


class NotificationConfigOut(BaseModel):
    ntfy: NtfyChannelConfig
    discord: DiscordChannelConfig
    email: EmailChannelConfig


class NotificationConfigIn(BaseModel):
    ntfy: NtfyChannelConfig | None = None
    discord: DiscordChannelConfig | None = None
    email: EmailChannelConfig | None = None


class NotificationTestIn(BaseModel):
    channel: Literal["ntfy", "discord", "email"]
    config: dict


async def _get_or_create_notifications(session: AsyncSession) -> NotificationConfig:
    row = await session.get(NotificationConfig, 1)
    if row is None:
        row = NotificationConfig(id=1, ntfy_events=[], discord_events=[], email_to_addrs=[], email_events=[])
        session.add(row)
        await session.flush()
    return row


def _to_notification_out(row: NotificationConfig) -> NotificationConfigOut:
    return NotificationConfigOut(
        ntfy=NtfyChannelConfig(
            enabled=row.ntfy_enabled,
            server_url=row.ntfy_server_url,
            topic=row.ntfy_topic,
            events=row.ntfy_events or [],
        ),
        discord=DiscordChannelConfig(
            enabled=row.discord_enabled,
            webhook_url=row.discord_webhook_url,
            events=row.discord_events or [],
        ),
        email=EmailChannelConfig(
            enabled=row.email_enabled,
            host=row.email_host,
            port=row.email_port,
            username=row.email_username,
            password=row.email_password,
            from_addr=row.email_from_addr,
            to_addrs=row.email_to_addrs or [],
            events=row.email_events or [],
        ),
    )


@router.get("/notifications", response_model=NotificationConfigOut, summary="Get notification config",
           dependencies=[Depends(require_scope("settings:read"))])
async def get_notification_config(session: AsyncSession = Depends(get_session)):
    """Built-in notification channel settings (ntfy, Discord, email). All three
    channels are always present in the response, even when unconfigured."""
    row = await _get_or_create_notifications(session)
    return _to_notification_out(row)


@router.put("/notifications", response_model=NotificationConfigOut, summary="Update notification config",
           dependencies=[Depends(require_scope("settings:write"))])
async def update_notification_config(
    body: NotificationConfigIn,
    session: AsyncSession = Depends(get_session),
):
    """Update notification channel settings. A channel key omitted from the request
    body leaves that channel's stored config unchanged; a channel key present
    replaces that whole channel's config."""
    row = await _get_or_create_notifications(session)
    if body.ntfy is not None:
        row.ntfy_enabled = body.ntfy.enabled
        row.ntfy_server_url = body.ntfy.server_url
        row.ntfy_topic = body.ntfy.topic
        row.ntfy_events = body.ntfy.events
    if body.discord is not None:
        row.discord_enabled = body.discord.enabled
        row.discord_webhook_url = body.discord.webhook_url
        row.discord_events = body.discord.events
    if body.email is not None:
        row.email_enabled = body.email.enabled
        row.email_host = body.email.host
        row.email_port = body.email.port
        row.email_username = body.email.username
        row.email_password = body.email.password
        row.email_from_addr = body.email.from_addr
        row.email_to_addrs = body.email.to_addrs
        row.email_events = body.email.events
    await session.commit()
    await session.refresh(row)
    return _to_notification_out(row)


@router.post("/notifications/test", summary="Send a test notification",
            dependencies=[Depends(require_scope("settings:write"))])
async def test_notification_config(body: NotificationTestIn):
    """Send a test notification for one channel using the supplied (unsaved) config,
    without requiring it be saved first. Returns `{ok, message}` — including for a
    missing required field, which is reported this way rather than as a 4xx."""
    cfg = body.config

    if body.channel == "ntfy":
        server_url = cfg.get("server_url")
        topic = cfg.get("topic")
        if not server_url or not topic:
            return {"ok": False, "message": "server_url and topic are required"}
        error = await send_ntfy(server_url, topic, _TEST_TITLE, _TEST_MESSAGE, cfg.get("priority"))
    elif body.channel == "discord":
        webhook_url = cfg.get("webhook_url")
        if not webhook_url:
            return {"ok": False, "message": "webhook_url is required"}
        error = await send_discord(webhook_url, f"{_TEST_TITLE}\n{_TEST_MESSAGE}")
    else:  # email
        host = cfg.get("host")
        port = cfg.get("port")
        from_addr = cfg.get("from_addr")
        to_addrs = cfg.get("to_addrs")
        if not host or not port or not from_addr or not to_addrs:
            return {"ok": False, "message": "host, port, from_addr, and to_addrs are required"}
        error = await send_email(
            host, port, cfg.get("username"), cfg.get("password"),
            from_addr, to_addrs, _TEST_TITLE, _TEST_MESSAGE,
        )

    if error is None:
        return {"ok": True, "message": "Test notification sent"}
    return {"ok": False, "message": error}


# ---------------------------------------------------------------------------
# Fleet backup / restore
# ---------------------------------------------------------------------------

def _redact_connection_config(printer_type: str, cfg: dict) -> dict:
    """Blank out credential fields (access codes, API keys, passwords) before a
    connection_config is written to a downloadable file. Which fields are secret is
    driven by each printer client's own `connection_fields()` declaration
    (`field_type == "password"`), not a hardcoded key list, so a new vendor client is
    covered automatically as long as it marks its own secret fields correctly."""
    cls = REGISTRY.get(printer_type)
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

    data = {
        "themis_backup_version": _BACKUP_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "credentials_redacted": not include_credentials,
        "printers": [
            {
                "name": p.name,
                "printer_type": p.printer_type,
                "connection_config": (
                    (p.connection_config or {}) if include_credentials
                    else _redact_connection_config(p.printer_type, p.connection_config or {})
                ),
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
        conn_cfg: dict = pr.get("connection_config") or {}

        secret_names = {f.name for f in REGISTRY[ptype].connection_fields() if f.field_type == "password"}
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

        printer = Printer(
            name=pname,
            printer_type=ptype,
            connection_config=conn_cfg,
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
