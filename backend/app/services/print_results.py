"""Print results on plate clear (BIZ-364): record what printed per part, requeue failed parts as a new pack in the same print
group (on the printers, profiles and asks the failed job had), and hold a part that has failed on `part_retry_limit` different
printers for a person to Reprint or Drop. Every failure lands in the project's failure log (`job_item_failures`)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Job, JobItemFailure, JobModelTarget, JobPrinterConfig, Printer, Project, ProjectGroup, ProjectItem, QueueConfig, UploadedFile
from . import project_groups

DEFAULT_RETRY_LIMIT = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _retry_limit(session: AsyncSession) -> int:
    cfg = await session.get(QueueConfig, 1)
    return int(cfg.part_retry_limit) if cfg is not None and cfg.part_retry_limit else DEFAULT_RETRY_LIMIT


async def record_and_restore(session: AsyncSession, job: Job, printer_id: int, failures: dict[int, int]) -> dict:
    """Record `job`'s per-part results (failures = {project_item_id: quantity_failed}; everything else on the plate succeeded),
    then requeue or hold the failed parts. Caller commits. Returns {"requeued_job_ids", "held": [{project_item_id, quantity}]}."""
    if job.project_item_quantities is None:
        raise HTTPException(400, "This job has no project items to report")
    if (await session.execute(select(JobItemFailure.id).where(JobItemFailure.job_id == job.id,
                                                              JobItemFailure.resolution.is_not(None)).limit(1))).first():
        raise HTTPException(409, "This job's print results were already reported")
    on_plate: dict[str, int] = json.loads(job.project_item_quantities)
    unknown = set(failures) - {int(k) for k in on_plate}
    if unknown:
        raise HTTPException(422, f"Project item(s) {sorted(unknown)} are not on this plate")

    # Replace a plain outcome review of this job, if one was recorded, so counts are never applied twice.
    for old in (await session.execute(select(JobItemFailure).where(JobItemFailure.job_id == job.id))).scalars():
        pi = await session.get(ProjectItem, old.project_item_id)
        if pi is not None:
            pi.quantity_failed = max(0, pi.quantity_failed - old.quantity_failed)
            pi.quantity_completed = max(0, pi.quantity_completed - (old.quantity_on_plate - old.quantity_failed))
    await session.execute(delete(JobItemFailure).where(JobItemFailure.job_id == job.id))

    limit = await _retry_limit(session)
    now = _now()
    requeue: dict[int, int] = {}
    held: list[dict] = []
    for key, qty in on_plate.items():
        item_id, qty = int(key), int(qty)
        if qty <= 0:
            continue
        failed = max(0, min(failures.get(item_id, 0), qty))
        pi = await session.get(ProjectItem, item_id)
        if pi is None:
            continue
        pi.quantity_failed += failed
        pi.quantity_completed += qty - failed
        resolution = None
        if failed:
            printers = set((await session.execute(select(JobItemFailure.printer_id).where(
                JobItemFailure.project_item_id == item_id, JobItemFailure.quantity_failed > 0,
                JobItemFailure.printer_id.is_not(None)))).scalars()) | {printer_id}
            if len(printers) >= limit:
                resolution = "held"
                pi.quantity_held += failed
                held.append({"project_item_id": item_id, "quantity": failed})
            else:
                resolution = "requeued"
                requeue[item_id] = failed
        session.add(JobItemFailure(job_id=job.id, project_item_id=item_id, quantity_failed=failed, quantity_on_plate=qty,
                                   printer_id=printer_id, created_at=now, resolution=resolution or "printed"))
    job.outcome = "reviewed"
    job.updated_at = now
    await session.flush()
    job_ids = await requeue_parts(session, job, requeue) if requeue else []
    return {"requeued_job_ids": job_ids, "held": held}


def _targets_of_job(configs: list[JobPrinterConfig], model_targets: list[JobModelTarget]) -> list[tuple[str, str]]:
    """The eligibility a job was created with, as print-group target keys (so a requeue goes where the original could)."""
    out: list[tuple[str, str]] = [(str(c.printer_id), c.print_profile) for c in configs if c.model_target_id is None]
    for t in model_targets:
        out.append((project_groups.ANY_KEY if t.model_uuid is None else project_groups.MODEL_PREFIX + t.model_uuid, t.print_profile))
    return out


async def requeue_parts(session: AsyncSession, source_job: Job, parts: dict[int, int]) -> list[int]:
    """Pack `parts` ({project_item_id: quantity}) anew in their print group and queue the jobs, eligible on what `source_job` was.
    Returns the new job ids. Caller commits (the pack step commits as it goes, like generation)."""
    from ..api.routes.projects import _plan_group_packs, _queue_packs, get_project_constructor, resolve_construct, Reason

    proj = await session.get(Project, source_job.project_id)
    if proj is None:
        return []
    constructor = get_project_constructor()
    if resolve_construct(constructor, None).reason is Reason.NO_PROVIDER:
        raise HTTPException(422, "No project builder is configured to repack the failed parts (Settings -> Plugins)")
    configs = (await session.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == source_job.id))).scalars().all()
    model_targets = (await session.execute(select(JobModelTarget).where(JobModelTarget.job_id == source_job.id))).scalars().all()
    targets = _targets_of_job(list(configs), list(model_targets))
    default = await project_groups.ensure_default_group(session, proj.id)

    from ..api.routes.projects import get_library_store
    library = get_library_store()
    by_group: dict[int, list] = {}
    for item_id, qty in parts.items():
        item = await session.get(ProjectItem, item_id)
        if item is None or qty <= 0:
            continue
        stand_in = SimpleNamespace(**{c: getattr(item, c) for c in ProjectItem.__table__.columns.keys()})
        stand_in.quantity = qty
        by_group.setdefault(item.group_id or default.id, []).append(stand_in)
    packs: list[dict] = []
    for group_id, items in by_group.items():
        group = await session.get(ProjectGroup, group_id) or default
        group_packs, _, _ = await _plan_group_packs(session, group, items, targets, library)
        packs.extend(group_packs)
    jobs, _ = await _queue_packs(session, proj, packs, constructor, allow_cached=False, save_slice=bool(source_job.save_slice))
    return [j["id"] for j in jobs]


async def failure_log(session: AsyncSession, project_id: int) -> list[dict]:
    rows = (await session.execute(
        select(JobItemFailure, ProjectItem, Job)
        .join(ProjectItem, ProjectItem.id == JobItemFailure.project_item_id)
        .join(Job, Job.id == JobItemFailure.job_id)
        .where(ProjectItem.project_id == project_id, JobItemFailure.quantity_failed > 0)
        .order_by(JobItemFailure.id))).all()
    names = {p.id: p.name for p in (await session.execute(select(Printer))).scalars()}
    files = {f.id: f.original_filename for f in (await session.execute(
        select(UploadedFile).where(UploadedFile.id.in_({pi.file_id for _, pi, _ in rows})))).scalars()} if rows else {}
    return [{
        "id": jif.id, "project_item_id": pi.id, "file_name": files.get(pi.file_id), "job_id": job.id,
        "printer_id": jif.printer_id, "printer_name": names.get(jif.printer_id), "quantity_failed": jif.quantity_failed,
        "created_at": jif.created_at, "resolution": jif.resolution,
    } for jif, pi, job in rows]


async def resolve_held(session: AsyncSession, project_id: int, item_id: int, action: str) -> dict:
    """Reprint (requeue on the printers its last failed job could use) or Drop the parts held for a person."""
    item = await session.get(ProjectItem, item_id)
    if item is None or item.project_id != project_id:
        raise HTTPException(404, f"Item {item_id} not found in project {project_id}")
    if not item.quantity_held:
        raise HTTPException(409, "Nothing of this item is held")
    held = (await session.execute(select(JobItemFailure).where(
        JobItemFailure.project_item_id == item_id, JobItemFailure.resolution == "held").order_by(JobItemFailure.id))).scalars().all()
    qty = item.quantity_held
    item.quantity_held = 0
    for jif in held:
        jif.resolution = "reprinted" if action == "reprint" else "dropped"
    job_ids: list[int] = []
    if action == "reprint":
        item.quantity_failed = max(0, item.quantity_failed - qty)          # they get another go: no longer counted as failed
        source = await session.get(Job, held[-1].job_id) if held else None
        if source is not None:
            await session.flush()
            job_ids = await requeue_parts(session, source, {item_id: qty})
    return {"requeued_job_ids": job_ids}
