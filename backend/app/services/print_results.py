"""Print results on plate clear (BIZ-364): record what printed per part, requeue failed parts as a new pack in the same print
group (on the printers, profiles and asks the failed job had), and hold a part that has failed on `part_retry_limit` different
printers for a person to Reprint or Drop. Every failure lands in the project's failure log (`job_item_failures`)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Job, JobItemFailure, JobModelTarget, JobPrinterConfig, Printer, Project, ProjectGroup, ProjectItem, QueueConfig, UploadedFile
from . import project_groups

DEFAULT_RETRY_LIMIT = 2
logger = logging.getLogger(__name__)

# jobs.outcome: who said what came off the plate. A person ("reviewed") always wins; a plate cleared without results gets the
# Settings default — and says so. `None` = awaiting results.
REVIEWED = "reviewed"
ASSUMED_GOOD = "assumed_good"          # cleared without results after a completed print: every part credited (can be corrected)
ASSUMED_FAILED = "assumed_failed"      # cleared without results after a failed print: every part printed again (final)
CLEAR_COMPLETE = ("assume_good", "ask")
CLEAR_FAILED = ("assume_failed", "ask")


def final(job: Job) -> bool:
    """Whether this plate's result can no longer be changed: its parts are already being printed again."""
    return job.outcome == ASSUMED_FAILED


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _retry_limit(session: AsyncSession) -> int:
    cfg = await session.get(QueueConfig, 1)
    return int(cfg.part_retry_limit) if cfg is not None and cfg.part_retry_limit else DEFAULT_RETRY_LIMIT


async def record_and_restore(session: AsyncSession, job: Job, printer_id: int | None, failures: dict[int, int],
                             assumed: str | None = None) -> dict:
    """Record `job`'s per-part results (failures = {project_item_id: quantity_failed}; everything else on the plate succeeded),
    and decide per failed part: requeue or hold. Caller commits, then calls `requeue_reported` (so the results and the plate
    clear are never lost to a packing error). Returns {"held": [...], "requeue": {project_item_id: quantity}}.

    `assumed` (ASSUMED_GOOD / ASSUMED_FAILED) records a default instead of a person's report: the parts move the same way, but
    nobody said which part failed, so an assumed failure never holds a part and never counts toward a printer's waivers.
    A person may still correct an ASSUMED_GOOD plate (its assumed rows are replaced); an ASSUMED_FAILED one is final."""
    if job.project_item_quantities is None:
        raise HTTPException(400, "This job has no project items to report")
    if final(job):
        raise HTTPException(409, "This plate was cleared without results and counted as failed: its parts are already being printed again")
    if job.outcome != ASSUMED_GOOD and (await session.execute(select(JobItemFailure.id).where(
            JobItemFailure.job_id == job.id, JobItemFailure.resolution.is_not(None)).limit(1))).first():
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
            # Distinct printers it failed on since a person last Reprinted or Dropped it (those reset the count). Assumed
            # failures (a plate cleared without results) are not evidence against a part: they neither count nor hold.
            printers = set((await session.execute(select(JobItemFailure.printer_id).join(Job, Job.id == JobItemFailure.job_id).where(
                JobItemFailure.project_item_id == item_id, JobItemFailure.quantity_failed > 0,
                JobItemFailure.printer_id.is_not(None), or_(Job.outcome.is_(None), Job.outcome != ASSUMED_FAILED),
                JobItemFailure.resolution.in_(["requeued", "held"])))).scalars()) | {printer_id}
            if assumed is None and len(printers) >= limit:
                resolution = "held"
                pi.quantity_held += failed
                held.append({"project_item_id": item_id, "quantity": failed})
            else:
                resolution = "requeued"
                requeue[item_id] = failed
        session.add(JobItemFailure(job_id=job.id, project_item_id=item_id, quantity_failed=failed, quantity_on_plate=qty,
                                   printer_id=printer_id, created_at=now, resolution=resolution or "printed",
                                   requeue_pending=failed if resolution == "requeued" else 0))
    job.outcome = assumed or REVIEWED
    job.updated_at = now
    await session.flush()
    from . import part_waivers           # BIZ-365: a printer that keeps failing a part is waived for it
    for item_id in ({} if assumed else {**requeue, **{h["project_item_id"]: h["quantity"] for h in held}}):
        pi = await session.get(ProjectItem, item_id)
        if pi is not None:
            await part_waivers.after_failure(session, printer_id, pi)
    return {"held": held, "requeue": requeue}


class _AlreadyRepacked(Exception):
    """Another repack of the same failure row committed first (a concurrent retry or the startup sweep): drop this pack."""


_requeue_locks: dict[int, tuple[asyncio.Lock, int]] = {}     # job id -> (lock, how many repacks hold or await it)


@contextlib.asynccontextmanager
async def _job_requeue_lock(job_id: int):
    """One repack per job at a time; the entry is dropped once nobody holds or awaits it (GH #249: it never shrank)."""
    lock, users = _requeue_locks.get(job_id, (asyncio.Lock(), 0))
    _requeue_locks[job_id] = (lock, users + 1)
    try:
        async with lock:
            yield
    finally:
        lock, users = _requeue_locks[job_id]
        if users <= 1:
            del _requeue_locks[job_id]
        else:
            _requeue_locks[job_id] = (lock, users - 1)


async def requeue_reported(session: AsyncSession, job: Job, requeue: dict[int, int] | None = None, *,
                           hold_when_unready: bool = True) -> dict:
    """After the results are committed: pack each failed part set anew, one pack at a time. A pack that can't be built (no
    builder, a missing model, a provider error) holds its parts for a person instead of losing them; the error is returned.

    What is repacked is what the job's failure rows still have `requeue_pending` (committed with the results), not `requeue`
    (kept for the callers' signature): each pack's jobs, their configs and the decrement of its parts commit together
    (GH #227), the decrement being a conditional claim, so an interruption or a concurrent repack never queues the same parts
    twice and leaves exactly the unpacked remainder pending. `hold_when_unready=False` (the startup sweep) leaves the parts
    pending, rather than holding them, while the project builder is not ready yet."""
    job_ids: list[int] = []
    held: list[dict] = []
    errors: list[str] = []
    job_id = job.id                        # a rollback below expires the ORM objects: re-read them by id
    async with _job_requeue_lock(job_id):
        pending = (await session.execute(select(JobItemFailure.project_item_id).where(
            JobItemFailure.job_id == job_id, JobItemFailure.requeue_pending > 0).order_by(JobItemFailure.id))).scalars().all()
        for item_id in pending:
            row = await _failure_row(session, job_id, item_id)
            if row is None or row.requeue_pending <= 0:
                continue

            async def packed(sess: AsyncSession, quantities: dict[int, int], row_id: int = row.id, item: int = item_id) -> None:
                qty = quantities.get(item, 0)
                if qty <= 0:
                    return
                claimed = await sess.execute(update(JobItemFailure).where(
                    JobItemFailure.id == row_id, JobItemFailure.requeue_pending >= qty)
                    .values(requeue_pending=JobItemFailure.requeue_pending - qty).execution_options(synchronize_session=False))
                if claimed.rowcount != 1:
                    raise _AlreadyRepacked

            try:
                job_ids += await requeue_parts(session, await session.get(Job, job_id), {item_id: row.requeue_pending},
                                               on_pack_jobs=packed)
                row = await _failure_row(session, job_id, item_id)
                if row is not None and row.requeue_pending:     # a pool job (its parts go back into the pool) or a short pack
                    source = await session.get(Job, job_id)
                    if source is not None and source.pool_group_id is None:
                        logger.warning("Repack of job %s item %s packed %s fewer part(s) than failed", job_id, item_id,
                                       row.requeue_pending)
                    row.requeue_pending = 0
                    await session.commit()
            except _AlreadyRepacked:
                await session.rollback()            # another repack queued these parts: nothing to hold, nothing to report
            except Exception as exc:          # noqa: BLE001 - any failure here must turn into a hold, never a lost part
                await session.rollback()
                if not hold_when_unready and getattr(exc, "status_code", None) == 503:
                    continue                        # the builder is still starting: leave the parts pending for a later try
                pi = await session.get(ProjectItem, item_id)
                row = await _failure_row(session, job_id, item_id)
                if pi is not None and row is not None and row.requeue_pending > 0:
                    errors.append(str(getattr(exc, "detail", exc)))
                    qty = row.requeue_pending          # what no committed pack covered: the rest is already queued
                    pi.quantity_held += qty
                    row.resolution, row.requeue_pending = "held", 0
                    held.append({"project_item_id": item_id, "quantity": qty})
                else:                               # every part was queued before the error (e.g. moving the pack's folder)
                    logger.warning("Repack of job %s item %s failed after its parts were queued: %s", job_id, item_id, exc)
                await session.commit()
    return {"requeued_job_ids": job_ids, "held": held, **({"requeue_errors": errors} if errors else {})}


async def _failure_row(session: AsyncSession, job_id: int, item_id: int) -> JobItemFailure | None:
    return (await session.execute(select(JobItemFailure).where(
        JobItemFailure.job_id == job_id, JobItemFailure.project_item_id == item_id)
        .execution_options(populate_existing=True))).scalar_one_or_none()


async def has_pending(session: AsyncSession, job_id: int) -> bool:
    """Results were committed for this job but some failed parts were never repacked (an interrupted clear, GH #227)."""
    return (await session.execute(select(JobItemFailure.id).where(
        JobItemFailure.job_id == job_id, JobItemFailure.requeue_pending > 0).limit(1))).first() is not None


async def resume_pending_for_printer(session: AsyncSession, printer_id: int) -> dict:
    """GH #249: any clear of a printer finishes the interrupted repacks of plates that printer ran (assumed-failed plates of
    an interrupted clear included), not only the job a retried report names. While the project builder is still starting the
    parts stay pending (never held behind the operator's back): the next clear or the startup sweep tries again."""
    job_ids = sorted(set((await session.execute(select(JobItemFailure.job_id).where(
        JobItemFailure.requeue_pending > 0, JobItemFailure.printer_id == printer_id))).scalars()))
    out: dict = {"requeued_job_ids": [], "held": []}
    for job_id in job_ids:
        job = await session.get(Job, job_id)
        if job is None:
            continue
        again = await requeue_reported(session, job, hold_when_unready=False)
        out["requeued_job_ids"] += again["requeued_job_ids"]
        out["held"] += again["held"]
        if again.get("requeue_errors"):
            out.setdefault("requeue_errors", []).extend(again["requeue_errors"])
    return out


async def resume_pending(session_factory) -> list[int]:
    """Startup recovery (GH #227): repack every failed part left pending by an interruption. Returns the job ids it tried;
    parts it could not repack yet (the project builder still starting) stay pending, so calling it again retries them."""
    async with session_factory() as session:
        job_ids = sorted(set((await session.execute(select(JobItemFailure.job_id).where(
            JobItemFailure.requeue_pending > 0))).scalars()))
    for job_id in job_ids:
        async with session_factory() as session:
            job = await session.get(Job, job_id)
            if job is not None:
                await requeue_reported(session, job, hold_when_unready=False)
    return job_ids


def _targets_of_job(configs: list[JobPrinterConfig], model_targets: list[JobModelTarget]) -> list[tuple[str, str]]:
    """The eligibility a job was created with, as print-group target keys (so a requeue goes where the original could)."""
    out: list[tuple[str, str]] = [(str(c.printer_id), c.print_profile) for c in configs if c.model_target_id is None]
    for t in model_targets:
        out.append((project_groups.ANY_KEY if t.model_uuid is None else project_groups.MODEL_PREFIX + t.model_uuid, t.print_profile))
    return out


async def requeue_parts(session: AsyncSession, source_job: Job, parts: dict[int, int], *, on_pack_jobs=None) -> list[int]:
    """Pack `parts` ({project_item_id: quantity}) anew in their print group and queue the jobs, eligible on what `source_job` was.
    Returns the new job ids. Caller commits (the pack step commits as it goes, like generation)."""
    # Reached through the projects route module on purpose: requeueing repacks exactly as generation does, and the tests
    # that fake the project builder / library replace them there (`app.api.routes.projects.get_project_constructor`).
    from ..api.routes.projects import _plan_group_packs, _queue_packs, get_project_constructor
    from .providers.contract import Reason, resolve_construct

    proj = await session.get(Project, source_job.project_id)
    if proj is None or source_job.pool_group_id is not None:
        return []                    # BIZ-362: a pool plate's failed parts go back into the pool (`pools.remaining` counts them)
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
    jobs, _ = await _queue_packs(session, proj, packs, constructor, allow_cached=False, save_slice=bool(source_job.save_slice),
                                 on_pack_jobs=on_pack_jobs)
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
    if action == "reprint":                     # quantity_failed keeps counting failed attempts: it is not reduced here
        source = await session.get(Job, held[-1].job_id) if held else None
        if source is not None:
            await session.flush()
            job_ids = await requeue_parts(session, source, {item_id: qty})
    return {"requeued_job_ids": job_ids}


async def clear_defaults(session: AsyncSession) -> tuple[str, str]:
    """(completed print, failed print): what a plate cleared without results is taken to mean (Settings -> Print defaults)."""
    cfg = await session.get(QueueConfig, 1)
    done = cfg.plate_clear_complete if cfg is not None else None
    failed = cfg.plate_clear_failed if cfg is not None else None
    return (done if done in CLEAR_COMPLETE else CLEAR_COMPLETE[0], failed if failed in CLEAR_FAILED else CLEAR_FAILED[0])


async def awaiting_results(session: AsyncSession, printer_id: int) -> list[Job]:
    """Finished project plates this printer ran that nobody has given a result for, oldest first. Plates that finished before
    the defaults existed (`queue_config.plate_clear_since`) are left to a person: they are reported from History, never assumed."""
    seen = select(JobItemFailure.job_id)
    stmt = select(Job).where(
        Job.assigned_printer_id == printer_id, Job.project_id.is_not(None), Job.project_item_quantities.is_not(None),
        Job.status.in_(["complete", "failed"]), Job.outcome.is_(None), Job.id.not_in(seen))
    cfg = await session.get(QueueConfig, 1)
    if cfg is not None and cfg.plate_clear_since:
        stmt = stmt.where(Job.completed_at >= cfg.plate_clear_since)
    return list((await session.execute(stmt.order_by(Job.id))).scalars().all())


async def apply_clear_defaults(session: AsyncSession, printer: Printer) -> list[dict]:
    """The plate was cleared WITHOUT results: give every finished, unreported project plate of this printer the Settings default
    for how its print ended (a test print is not a project result and is skipped). Returns [{job_id, result, requeue}] for the
    plates a default was applied to; the caller commits and then requeues (`requeue_reported`) each `requeue`."""
    on_complete, on_failed = await clear_defaults(session)
    applied: list[dict] = []
    for job in await awaiting_results(session, printer.id):
        if job.id == printer.test_print_job_id:
            continue
        on_plate = {int(k): int(n) for k, n in json.loads(job.project_item_quantities).items() if int(n) > 0}
        if job.status == "complete" and on_complete == "assume_good":
            await record_and_restore(session, job, printer.id, {}, assumed=ASSUMED_GOOD)
            applied.append({"job_id": job.id, "result": ASSUMED_GOOD, "requeue": {}})
        elif job.status == "failed" and on_failed == "assume_failed":
            recorded = await record_and_restore(session, job, printer.id, on_plate, assumed=ASSUMED_FAILED)
            applied.append({"job_id": job.id, "result": ASSUMED_FAILED, "requeue": recorded["requeue"]})
    return applied


async def resume_pending_with_retries(session_factory, *, attempts: int = 20, delay_s: float = 30.0, on_resumed=None) -> None:
    """The startup sweep (GH #227/#249): `resume_pending` until nothing is left, at most `attempts` passes `delay_s` apart
    (the project builder may still be starting). A pass that fails (e.g. a database error) is logged and retried, never
    the end of the sweep."""
    for _ in range(attempts):
        try:
            resumed = await resume_pending(session_factory)
            if not resumed:
                return
            logger.info("Resuming interrupted requeues of failed parts for job(s) %s", resumed)
            if on_resumed is not None:
                on_resumed()
        except Exception:
            logger.exception("Resuming interrupted requeues failed; retrying")
        await asyncio.sleep(delay_s)
