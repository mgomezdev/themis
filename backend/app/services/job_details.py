"""Generic job details (docs/superpowers/specs/2026-10-20-job-events-and-details-design.md §3).

A job carries open-vocabulary detail fields written by the plugin that produced them (a printer plugin reporting a print, the filament
plugin writing a cost). Core stores `(job, field, origin)` rows and answers `get_details(job_id, fields)`; it enforces neither field
names nor value shapes. A field a source cannot provide is answered with a spec-defined status, never an exception:

* `ok`            a value was reported;
* `unsupported`   the source says it cannot provide the field (manifest `unsupported_job_fields`, or a runtime `UNSUPPORTED` marker);
* `unavailable`   the source declares the field but reported nothing for this job;
* `unknown_field` nobody installed declares it and nothing is stored.

Rows are written in the caller's transaction (`set_details`, no commit): the printer reporter commits them with the state change."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence, TypedDict

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Job, JobDetail, Printer

OK, UNSUPPORTED_STATUS, UNAVAILABLE, UNKNOWN_FIELD = "ok", "unsupported", "unavailable", "unknown_field"
CORE_ORIGIN = "core"

# Standard fields every printer plugin is expected to declare (or mark unsupported): defined by docs/printer-interface.md, checked
# (a load warning, the plugin test kit) but not enforced.
PRINTER_MIN_JOB_FIELDS = ("print_start_time", "print_end_time", "result")


class _Unsupported:
    """Runtime marker: `details={"filament_used_grams": UNSUPPORTED}` records that this source cannot provide the field for this job."""
    def __repr__(self) -> str:
        return "UNSUPPORTED"


UNSUPPORTED = _Unsupported()


class DetailResult(TypedDict):
    status: str
    value: Any
    origin: str | None
    updated_at: str | None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def set_details(session: AsyncSession, job_id: int, origin: str, details: Mapping[str, Any]) -> None:
    """Upsert `details` for `job_id` as written by `origin` in the caller's transaction (no commit). `UNSUPPORTED` stores
    `status=unsupported`; anything else is stored as given (it must be JSON-serialisable)."""
    now = _now()
    for field, value in details.items():
        if value is UNSUPPORTED:
            status, stored = UNSUPPORTED_STATUS, None
        else:
            status, stored = OK, value
        stmt = sqlite_insert(JobDetail).values(job_id=job_id, field=field, origin=origin, status=status, value=stored, updated_at=now)
        await session.execute(stmt.on_conflict_do_update(
            index_elements=["job_id", "field", "origin"], set_={"status": status, "value": stored, "updated_at": now}))


def field_catalog() -> dict[str, list[dict]]:
    """field -> the enabled plugins that declare it (`supported` False where the plugin lists it as unsupported)."""
    from ..plugins import registered_plugins
    from ..plugins.host import plugin_host
    out: dict[str, list[dict]] = {}
    for m in registered_plugins():
        if not plugin_host.is_enabled(m.id):
            continue
        for jf in m.job_fields:
            out.setdefault(jf.name, []).append({"plugin_id": m.id, "description": jf.description, "type_hint": jf.type_hint, "supported": True})
        for name in m.unsupported_job_fields:
            out.setdefault(name, []).append({"plugin_id": m.id, "description": "", "type_hint": "", "supported": False})
    return out


async def _printer_plugin(session: AsyncSession, job: Job) -> str | None:
    printer_id = job.assigned_printer_id if job.assigned_printer_id is not None else job.printed_on_printer_id
    printer = await session.get(Printer, printer_id) if printer_id is not None else None
    return printer.plugin_id if printer is not None else None


def _declares(plugin_id: str | None, field: str) -> str | None:
    """`unsupported` / `declared` / None for `field` in the plugin's manifest."""
    from ..plugins import get_plugin
    m = get_plugin(plugin_id) if plugin_id else None
    if m is None:
        return None
    if field in m.unsupported_job_fields:
        return UNSUPPORTED_STATUS
    return "declared" if any(jf.name == field for jf in m.job_fields) else None


async def get_details_in(session: AsyncSession, job_id: int, fields: Sequence[str], origin: str | None = None) -> dict[str, DetailResult]:
    job = await session.get(Job, job_id)
    printer_plugin = await _printer_plugin(session, job) if job is not None else None
    rows = (await session.execute(select(JobDetail).where(JobDetail.job_id == job_id, JobDetail.field.in_(list(fields))))).scalars().all()
    by_field: dict[str, list[JobDetail]] = {}
    for r in rows:
        by_field.setdefault(r.field, []).append(r)
    catalog = field_catalog()
    out: dict[str, DetailResult] = {}
    for field in fields:
        candidates = [r for r in by_field.get(field, []) if origin is None or r.origin == origin]
        if candidates:
            # The single-writer rule is not enforced yet; with several origins, the printer plugin's value wins, then the latest.
            best = max(candidates, key=lambda r: (r.origin == printer_plugin, r.updated_at))
            out[field] = {"status": best.status, "value": best.value, "origin": best.origin, "updated_at": best.updated_at}
            continue
        owner = origin or printer_plugin
        declared = _declares(owner, field)
        if declared == UNSUPPORTED_STATUS:
            out[field] = {"status": UNSUPPORTED_STATUS, "value": None, "origin": owner, "updated_at": None}
        elif declared == "declared" or (origin is None and any(e["supported"] for e in catalog.get(field, []))):
            out[field] = {"status": UNAVAILABLE, "value": None, "origin": owner if declared else None, "updated_at": None}
        else:
            out[field] = {"status": UNKNOWN_FIELD, "value": None, "origin": None, "updated_at": None}
    return out


async def get_details(job_id: int, fields: Sequence[str], origin: str | None = None) -> dict[str, DetailResult]:
    """`get_details(job_id, ["filament_usage", "print_end_time"])` -> `{field: {status, value, origin, updated_at}}`. Never raises for a
    missing job or field (an unknown job answers `unknown_field` for every name). Opens its own session."""
    from ..eventing.hub import hub
    async with hub.session_factory() as session:
        return await get_details_in(session, job_id, fields, origin)
