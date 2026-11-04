"""Printer part waivers (BIZ-365): a printer that keeps failing the same part stops getting it. A part is the model, not the
project item: `<file content hash>:<item kind>:<source_ref>`, so an STL (or a 3MF object / plate) is the same part in every
project. A waiver is cleared only by a person (Clear / Clear all / a good test print), never by maintenance."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Job, JobItemFailure, PartWaiver, Printer, ProjectItem, QueueConfig, UploadedFile

DEFAULT_WAIVE_AFTER = 2


def part_key(item, file: UploadedFile | None) -> str:
    content = (file.content_hash if file is not None else None) or f"file:{item.file_id}"
    return f"{content}:{getattr(item, 'kind', None) or 'stl'}:{getattr(item, 'source_ref', None) or ''}"


async def _key_of_item(session: AsyncSession, item: ProjectItem) -> str:
    return part_key(item, await session.get(UploadedFile, item.file_id))


async def keys_of_job(session: AsyncSession, job: Job) -> set[str]:
    """The parts a project job's plate carries (empty for a job outside a project)."""
    if not job.project_item_quantities:
        return set()
    keys: set[str] = set()
    for key, qty in json.loads(job.project_item_quantities).items():
        item = await session.get(ProjectItem, int(key))
        if item is not None and int(qty) > 0:
            keys.add(await _key_of_item(session, item))
    return keys


_ACTIVE = PartWaiver.cleared_at.is_(None)


async def waived_keys(session: AsyncSession, printer_id: int) -> set[str]:
    return set((await session.execute(select(PartWaiver.part_key).where(PartWaiver.printer_id == printer_id, _ACTIVE))).scalars())


async def count(session: AsyncSession, printer_id: int) -> int:
    return int((await session.execute(select(func.count(PartWaiver.id)).where(PartWaiver.printer_id == printer_id, _ACTIVE))).scalar() or 0)


async def counts(session: AsyncSession) -> dict[int, int]:
    rows = await session.execute(select(PartWaiver.printer_id, func.count(PartWaiver.id)).where(_ACTIVE)
                                 .group_by(PartWaiver.printer_id))
    return {pid: int(n) for pid, n in rows.all()}


def stopped(printer: Printer, waived: int) -> bool:
    return bool(printer.waiver_stop_at) and waived >= int(printer.waiver_stop_at)


def flagged(printer: Printer, waived: int) -> bool:
    return bool(printer.waiver_flag_at) and waived >= int(printer.waiver_flag_at)


async def after_failure(session: AsyncSession, printer_id: int, item: ProjectItem) -> PartWaiver | None:
    """Count this printer's failures of `item`'s part (its rows are already flushed) and waive the printer for it once they
    reach `waive_after`. Returns the waiver when one exists (new or updated)."""
    key = await _key_of_item(session, item)
    cfg = await session.get(QueueConfig, 1)
    limit = int(cfg.waive_after) if cfg is not None and cfg.waive_after else DEFAULT_WAIVE_AFTER
    rows = (await session.execute(
        select(JobItemFailure, ProjectItem).join(ProjectItem, ProjectItem.id == JobItemFailure.project_item_id)
        .where(JobItemFailure.printer_id == printer_id, JobItemFailure.quantity_failed > 0))).all()
    waiver = (await session.execute(select(PartWaiver).where(PartWaiver.printer_id == printer_id,
                                                             PartWaiver.part_key == key))).scalar_one_or_none()
    since = waiver.cleared_at if waiver is not None else None          # a Clear starts the count again
    failures = [jif for jif, pi in rows if await _key_of_item(session, pi) == key
                and (since is None or (jif.created_at or "") > since)]
    now = datetime.now(timezone.utc).isoformat()
    total = sum(j.quantity_failed for j in failures)
    if waiver is not None and waiver.cleared_at is None:
        waiver.failures, waiver.last_failure_at = total, now
    elif waiver is not None:
        if total >= limit:                                             # failed again N times since it was cleared
            waiver.cleared_at, waiver.failures, waiver.last_failure_at = None, total, now
    elif total >= limit:
        waiver = PartWaiver(printer_id=printer_id, part_key=key, file_id=item.file_id, failures=total,
                            last_failure_at=now, created_at=now)
        session.add(waiver)
    return waiver


async def listing(session: AsyncSession, printer_id: int) -> list[dict]:
    from ..models import Project
    out = []
    for w in (await session.execute(select(PartWaiver).where(PartWaiver.printer_id == printer_id, _ACTIVE)
                                    .order_by(PartWaiver.id))).scalars():
        f = await session.get(UploadedFile, w.file_id) if w.file_id else None
        projects: set[str] = set()
        for jif, pi in (await session.execute(
                select(JobItemFailure, ProjectItem).join(ProjectItem, ProjectItem.id == JobItemFailure.project_item_id)
                .where(JobItemFailure.printer_id == printer_id, JobItemFailure.quantity_failed > 0))).all():
            if await _key_of_item(session, pi) == w.part_key:
                proj = await session.get(Project, pi.project_id)
                if proj is not None:
                    projects.add(proj.name)
        out.append({"id": w.id, "part_key": w.part_key, "file_name": f.original_filename if f else None,
                    "failures": w.failures, "last_failure_at": w.last_failure_at, "projects": sorted(projects)})
    return out


async def clear(session: AsyncSession, printer_id: int, waiver_id: int | None = None) -> int:
    """Clear active waivers (one, or all of the printer's). The rows stay, marked cleared, so earlier failures stop counting."""
    from sqlalchemy import update
    stmt = update(PartWaiver).where(PartWaiver.printer_id == printer_id, _ACTIVE)
    if waiver_id is not None:
        stmt = stmt.where(PartWaiver.id == waiver_id)
    return (await session.execute(stmt.values(cleared_at=datetime.now(timezone.utc).isoformat()))).rowcount or 0


async def end_stale_test_print(session: AsyncSession, printer: Printer) -> None:
    """Drop the printer's test-print pointer when that job can no longer run (gone, cancelled, failed, or done and reported),
    so a printer never waits on it forever. Caller commits."""
    if printer.test_print_job_id is None:
        return
    job = await session.get(Job, printer.test_print_job_id)
    if job is None or job.status in ("cancelled", "failed") or (job.status == "complete" and job.outcome is not None):
        printer.test_print_job_id = printer.test_print_waiver_id = None


async def source_of(session: AsyncSession, waiver: PartWaiver) -> tuple[Job | None, ProjectItem | None]:
    """The latest failed job on this printer that carried the waived part, and that part's item (what a test print repeats)."""
    rows = (await session.execute(
        select(JobItemFailure, ProjectItem).join(ProjectItem, ProjectItem.id == JobItemFailure.project_item_id)
        .where(JobItemFailure.printer_id == waiver.printer_id, JobItemFailure.quantity_failed > 0)
        .order_by(JobItemFailure.id.desc()))).all()
    for jif, pi in rows:
        if await _key_of_item(session, pi) == waiver.part_key:
            return await session.get(Job, jif.job_id), pi
    return None, None
