"""Core printer-model registry (BIZ-262): one stable UUID per supported printer model.

Plugins *contribute* `Manufacturer`/`PrinterModel` declarations in their manifests; core assigns and persists the UUID keyed to
the plugin's own (plugin_id, manufacturer_id, model_id), so printers, files and eligibility reference a machine identity that
survives restarts, plugin upgrades and plugin removal. Nothing here imports a plugin module: it reads the registered manifests.
Rows are never deleted or re-keyed."""
from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Printer, PrinterModelRecord
from ..plugins import get_plugin, registered_plugins
from ..plugins.host import plugin_host


async def sync_registry(session: AsyncSession) -> int:
    """Upsert every declared model (no duplicates; existing UUIDs and the user's `enabled` choice are kept) and mark rows no
    registered plugin declares any more as not `declared`. Idempotent; returns how many rows were created. Caller commits."""
    existing = {(r.plugin_id, r.manufacturer_id, r.model_id): r
                for r in (await session.execute(select(PrinterModelRecord))).scalars()}
    seen: set[tuple[str, str, str]] = set()
    created = 0
    for manifest in registered_plugins():
        for mfr in manifest.manufacturers:
            for model in mfr.models:
                key = (manifest.id, mfr.id, model.id)
                seen.add(key)
                row = existing.get(key)
                if row is None:
                    row = PrinterModelRecord(id=str(uuid.uuid4()), plugin_id=manifest.id, manufacturer_id=mfr.id, model_id=model.id,
                                             enabled=True)
                    session.add(row)
                    created += 1
                row.manufacturer_name, row.display_name = mfr.name, model.name
                row.bed_x_mm, row.bed_y_mm, row.toolheads = float(model.bed_mm[0]), float(model.bed_mm[1]), model.toolheads
                row.declared = True
    for key, row in existing.items():
        if key not in seen:
            row.declared = False
    await session.flush()
    return created


def dormant_reason(row: PrinterModelRecord) -> str | None:
    """Why a model cannot be used for new work right now. The row, its UUID and every reference stay intact."""
    if get_plugin(row.plugin_id) is None:
        return "plugin_removed"
    if not row.declared:
        return "model_removed"
    if not plugin_host.is_enabled(row.plugin_id):
        return "plugin_disabled"
    return None


async def model_uuid_for(session: AsyncSession, plugin_id: str, manufacturer_id: str, model_id: str) -> str | None:
    return (await session.execute(select(PrinterModelRecord.id).where(
        PrinterModelRecord.plugin_id == plugin_id, PrinterModelRecord.manufacturer_id == manufacturer_id,
        PrinterModelRecord.model_id == model_id))).scalar_one_or_none()


async def get_model(session: AsyncSession, model_uuid: str) -> PrinterModelRecord | None:
    return await session.get(PrinterModelRecord, model_uuid)


async def list_models(session: AsyncSession, *, enabled: bool | None = None, plugin_id: str | None = None,
                      usable_only: bool = False) -> list[dict]:
    """The neutral query other capabilities use to filter models: no plugin module is imported to build it."""
    q = select(PrinterModelRecord)
    if enabled is not None:
        q = q.where(PrinterModelRecord.enabled == enabled)
    if plugin_id is not None:
        q = q.where(PrinterModelRecord.plugin_id == plugin_id)
    rows = (await session.execute(q)).scalars().all()
    counts = dict((await session.execute(
        select(Printer.model_uuid, func.count()).where(Printer.model_uuid.is_not(None)).group_by(Printer.model_uuid))).all())
    out = []
    for r in rows:
        reason = dormant_reason(r)
        if usable_only and reason is not None:
            continue
        out.append(model_view(r, reason, counts.get(r.id, 0)))
    return sorted(out, key=lambda m: (m["manufacturer_name"].lower(), m["display_name"].lower()))


def model_view(r: PrinterModelRecord, reason: str | None, printer_count: int = 0) -> dict:
    return {"id": r.id, "plugin_id": r.plugin_id, "manufacturer_id": r.manufacturer_id, "manufacturer_name": r.manufacturer_name,
            "model_id": r.model_id, "display_name": r.display_name, "bed_mm": [r.bed_x_mm, r.bed_y_mm], "toolheads": r.toolheads,
            "enabled": r.enabled, "dormant": reason is not None, "dormant_reason": reason, "printer_count": printer_count}


async def set_enabled(session: AsyncSession, model_uuid: str, enabled: bool) -> PrinterModelRecord | None:
    row = await session.get(PrinterModelRecord, model_uuid)
    if row is not None:
        row.enabled = enabled
        await session.flush()
    return row


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


async def match_free_text(session: AsyncSession, text: str | None, *, plugin_id: str | None = None) -> str | None:
    """Match a discovered printer's free-text model (e.g. "Bambu Lab P1S") to a known registry entry: the UUID when exactly one
    enabled, usable model matches, else None — the caller then offers the explicit custom/unmatched path."""
    if not text or not _norm(text):
        return None
    want = _norm(text)
    hits = []
    for m in await list_models(session, enabled=True, plugin_id=plugin_id, usable_only=True):
        name, full = _norm(m["display_name"]), _norm(m["manufacturer_name"] + m["display_name"])
        if want in (name, full, _norm(m["model_id"])):
            hits.append(m["id"])
    return hits[0] if len(set(hits)) == 1 else None
