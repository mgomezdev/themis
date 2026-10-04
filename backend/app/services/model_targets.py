"""Materialize "any printer of this make/model" job targets into per-printer JobPrinterConfig rows.

A `JobModelTarget` is persistent intent; the per-printer `JobPrinterConfig` rows it produces (tagged with
`model_target_id`) are what the queue engine, slicer and estimates actually read, so nothing downstream needs to
know about targets. A printer's make/model is its `current_orca_printer_profile`.
"""
from __future__ import annotations

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Job, JobModelTarget, JobPrinterConfig, Printer, UploadedFile
from .library_scanner import file_kind

# Only waiting jobs are re-synced: a job that is slicing/printing keeps the config it is running with.
_SYNCABLE = ("queued", "blocked")


def accepts_file(printer_type: str, filename: str | None) -> bool:
    """Whether this vendor can print the file as-is. Sliceable models: always (the slicer targets the printer). A raw
    .gcode needs `raw_gcode_supported`; a .gcode.3mf sliced archive needs `sliced_archive_supported` (BIZ-190)."""
    kind = file_kind(filename)
    if kind not in ("gcode", "gcode_3mf"):
        return True
    from .printer_client_factory import REGISTRY   # lazy: the factory imports models
    cls = REGISTRY.get(printer_type)
    if kind == "gcode":
        return True if cls is None else bool(getattr(cls, "raw_gcode_supported", True))
    return False if cls is None else bool(getattr(cls, "sliced_archive_supported", False))


def _config_from_target(target: JobModelTarget, printer_id: int) -> JobPrinterConfig:
    return JobPrinterConfig(
        job_id=target.job_id,
        printer_id=printer_id,
        print_profile=target.print_profile,
        filament_profile=target.filament_profile or None,
        filament_id=target.filament_id,
        filament_type=target.filament_type,
        filament_color=target.filament_color,
        filament_map=target.filament_map,
        model_target_id=target.id,
    )


async def sync_targets_for_printer(session: AsyncSession, printer: Printer) -> None:
    """Make this printer's materialized configs match the waiting jobs' model targets (caller commits)."""
    profile = printer.current_orca_printer_profile or None

    # Drop rows left over from a profile change, and rows whose target was deleted (e.g. the job was re-edited).
    mismatch = JobModelTarget.id.is_(None) if profile is None else or_(
        JobModelTarget.id.is_(None), JobModelTarget.machine_profile != profile)
    stale_ids = (await session.execute(
        select(JobPrinterConfig.id)
        .join(Job, Job.id == JobPrinterConfig.job_id)
        .outerjoin(JobModelTarget, JobModelTarget.id == JobPrinterConfig.model_target_id)
        .where(JobPrinterConfig.printer_id == printer.id, JobPrinterConfig.model_target_id.is_not(None),
               Job.status.in_(_SYNCABLE), mismatch)
    )).scalars().all()
    if stale_ids:
        await session.execute(delete(JobPrinterConfig).where(JobPrinterConfig.id.in_(stale_ids)))

    if profile is None:
        return
    have = set((await session.execute(
        select(JobPrinterConfig.job_id).where(JobPrinterConfig.printer_id == printer.id)
    )).scalars().all())
    rows = (await session.execute(
        select(JobModelTarget, UploadedFile.original_filename)
        .join(Job, Job.id == JobModelTarget.job_id)
        .join(UploadedFile, UploadedFile.id == Job.uploaded_file_id)
        .where(JobModelTarget.machine_profile == profile, Job.status.in_(_SYNCABLE))
        .order_by(JobModelTarget.id)
    )).all()
    for target, filename in rows:
        if target.job_id in have:  # an explicit pick (or an earlier target) already covers this printer
            continue
        if not accepts_file(printer.printer_type, filename):
            continue
        session.add(_config_from_target(target, printer.id))
        have.add(target.job_id)
    try:
        # A concurrent edit of the job can delete a target between our reads and this insert; the unique
        # (job, printer) index turns the resulting duplicate into an error we simply retry next cycle.
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        pass   # the savepoint rolled the insert back; the next cycle re-evaluates


async def materialize_job(session: AsyncSession, job_id: int) -> None:
    """Create configs for one job's targets on every printer currently matching them (caller commits)."""
    targets = (await session.execute(
        select(JobModelTarget).where(JobModelTarget.job_id == job_id).order_by(JobModelTarget.id)
    )).scalars().all()
    if not targets:
        return
    filename = (await session.execute(
        select(UploadedFile.original_filename).join(Job, Job.uploaded_file_id == UploadedFile.id).where(Job.id == job_id)
    )).scalar_one_or_none()
    have = set((await session.execute(
        select(JobPrinterConfig.printer_id).where(JobPrinterConfig.job_id == job_id)
    )).scalars().all())
    printers = (await session.execute(select(Printer).order_by(Printer.id))).scalars().all()
    for target in targets:
        for printer in printers:
            if printer.id in have or printer.current_orca_printer_profile != target.machine_profile:
                continue
            if not accepts_file(printer.printer_type, filename):
                continue
            session.add(_config_from_target(target, printer.id))
            have.add(printer.id)
    await session.flush()


def _target_dict(t: JobModelTarget) -> dict:
    return {
        "machine_profile": t.machine_profile,
        "print_profile": t.print_profile,
        "filament_profile": t.filament_profile,
        "filament_id": t.filament_id,
        "filament_type": t.filament_type,
        "filament_color": t.filament_color,
        "filament_map": t.filament_map,
    }


async def target_dicts_by_job(session: AsyncSession, job_ids: list[int]) -> dict[int, list[dict]]:
    """Targets for many jobs in one query (polled routes must not query per job); every id gets a key."""
    out: dict[int, list[dict]] = {jid: [] for jid in job_ids}
    if not job_ids:
        return out
    rows = (await session.execute(
        select(JobModelTarget).where(JobModelTarget.job_id.in_(job_ids)).order_by(JobModelTarget.id)
    )).scalars().all()
    for t in rows:
        out[t.job_id].append(_target_dict(t))
    return out


async def target_dicts(session: AsyncSession, job_id: int) -> list[dict]:
    return (await target_dicts_by_job(session, [job_id]))[job_id]
