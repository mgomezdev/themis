"""Materialize "any printer of this make/model" job targets into per-printer JobPrinterConfig rows.

A `JobModelTarget` is persistent intent; the per-printer `JobPrinterConfig` rows it produces (tagged with
`model_target_id`) are what the queue engine, slicer and estimates actually read, so nothing downstream needs to
know about targets. A printer's make/model is its `current_orca_printer_profile`.
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Job, JobModelTarget, JobPrinterConfig, Printer

# Only waiting jobs are re-synced: a job that is slicing/printing keeps the config it is running with.
_SYNCABLE = ("queued", "blocked")


def _config_from_target(target: JobModelTarget, printer_id: int) -> JobPrinterConfig:
    return JobPrinterConfig(
        job_id=target.job_id,
        printer_id=printer_id,
        print_profile=target.print_profile,
        filament_profile=target.filament_profile or target.filament_type or "",
        filament_id=target.filament_id,
        filament_type=target.filament_type,
        filament_color=target.filament_color,
        filament_map=target.filament_map,
        model_target_id=target.id,
    )


async def sync_targets_for_printer(session: AsyncSession, printer: Printer) -> None:
    """Make this printer's materialized configs match the waiting jobs' model targets (caller commits)."""
    profile = printer.current_orca_printer_profile or None

    # Drop rows left over from a profile change (and rows whose target no longer matches this printer).
    stale_q = (
        select(JobPrinterConfig.id)
        .join(JobModelTarget, JobModelTarget.id == JobPrinterConfig.model_target_id)
        .join(Job, Job.id == JobPrinterConfig.job_id)
        .where(JobPrinterConfig.printer_id == printer.id, Job.status.in_(_SYNCABLE))
    )
    if profile is not None:
        stale_q = stale_q.where(JobModelTarget.machine_profile != profile)
    stale_ids = (await session.execute(stale_q)).scalars().all()
    if stale_ids:
        await session.execute(delete(JobPrinterConfig).where(JobPrinterConfig.id.in_(stale_ids)))

    if profile is None:
        return
    have = set((await session.execute(
        select(JobPrinterConfig.job_id).where(JobPrinterConfig.printer_id == printer.id)
    )).scalars().all())
    targets = (await session.execute(
        select(JobModelTarget)
        .join(Job, Job.id == JobModelTarget.job_id)
        .where(JobModelTarget.machine_profile == profile, Job.status.in_(_SYNCABLE))
        .order_by(JobModelTarget.id)
    )).scalars().all()
    for target in targets:
        if target.job_id in have:  # an explicit pick (or an earlier target) already covers this printer
            continue
        session.add(_config_from_target(target, printer.id))
        have.add(target.job_id)
    await session.flush()


async def materialize_job(session: AsyncSession, job_id: int) -> None:
    """Create configs for one job's targets on every printer currently matching them (caller commits)."""
    targets = (await session.execute(
        select(JobModelTarget).where(JobModelTarget.job_id == job_id).order_by(JobModelTarget.id)
    )).scalars().all()
    if not targets:
        return
    have = set((await session.execute(
        select(JobPrinterConfig.printer_id).where(JobPrinterConfig.job_id == job_id)
    )).scalars().all())
    printers = (await session.execute(select(Printer).order_by(Printer.id))).scalars().all()
    for target in targets:
        for printer in printers:
            if printer.id in have or printer.current_orca_printer_profile != target.machine_profile:
                continue
            session.add(_config_from_target(target, printer.id))
            have.add(printer.id)
    await session.flush()


async def target_dicts(session: AsyncSession, job_id: int) -> list[dict]:
    rows = (await session.execute(
        select(JobModelTarget).where(JobModelTarget.job_id == job_id).order_by(JobModelTarget.id)
    )).scalars().all()
    return [
        {
            "machine_profile": t.machine_profile,
            "print_profile": t.print_profile,
            "filament_profile": t.filament_profile,
            "filament_id": t.filament_id,
            "filament_type": t.filament_type,
            "filament_color": t.filament_color,
            "filament_map": t.filament_map,
        }
        for t in rows
    ]
