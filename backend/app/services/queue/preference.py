"""A preference only ranks printers already eligible for the job; it never adds targets."""
from sqlalchemy import select

from ...models import Job, JobPrinterConfig, Printer, UploadedFile
from .. import filament_matching, gcode_eligibility, scheduling, slicer_intent
from ..library_scanner import is_presliced_file


def allowed(preference, printer):
    return not preference or not preference.get("strict") or slicer_intent.rank(preference,printer) < len(preference["entries"])


async def defer(engine, session, job, printer):
    preference = job.printer_preference
    if not preference:
        return False
    if not allowed(preference,printer):
        return True
    current = slicer_intent.rank(preference,printer)
    if current == 0:
        return False
    # A busy preferred printer never delays an idle fallback. Readiness alone is not
    # enough: filament, default process, bed, quiet hours and file compatibility count too.
    import app.services.queue_engine as qe
    rows = (await session.execute(select(JobPrinterConfig,Printer).join(Printer,Printer.id == JobPrinterConfig.printer_id)
        .where(JobPrinterConfig.job_id == job.id))).all()
    source = await session.get(UploadedFile,job.uploaded_file_id)
    for config,candidate in rows:
        if candidate.id == printer.id or slicer_intent.rank(preference,candidate) >= current:
            continue
        if not candidate.enabled or not candidate.queue_on or not candidate.configured or config.slice_failed:
            continue
        if not engine._mgr.is_printer_ready(candidate.id) or candidate.id in engine._pool_busy.values():
            continue
        if scheduling.in_quiet_hours(candidate.quiet_start,candidate.quiet_end):
            continue
        if (await session.execute(select(Job.id).where(Job.assigned_printer_id == candidate.id,
            Job.status.in_(["slicing","sliced","uploading","printing","paused"])).limit(1))).first():
            continue
        if await qe.bed_requirement.reason(session,job.min_bed_mm,candidate):
            continue
        if not qe.model_targets.accepts_file(candidate.printer_type,source.original_filename):
            continue
        if not (await gcode_eligibility.evaluate(session,source,candidate,confirmed=bool(job.eligibility_confirmed))).allowed:
            continue
        if qe.missing_process_reason(source,config,candidate):
            continue
        conflicts = await filament_matching.conflicts_for(candidate.loaded_filaments or [])
        if not filament_matching.evaluate_for_printer(config,candidate,conflicts).eligible:
            continue
        if candidate.test_print_job_id not in (None,job.id):
            continue
        waived = await qe.part_waivers.waived_keys(session,candidate.id)
        if qe.part_waivers.stopped(candidate,len(waived)) or await qe.part_waivers.keys_of_job(session,job) & waived:
            continue
        if not is_presliced_file(source):
            resolution = await qe.slicer_profiles.resolve_for_printer(session,candidate)
            decision = await qe.resolve_slicing(qe.PrinterView.of(candidate,resolution),source.original_filename)
            if decision.blocked:
                continue
        return True
    return False


async def defer_pool(engine,session,group,printer):
    from .. import pools
    preference = group.printer_preference
    if not allowed(preference,printer):
        return True
    if not preference or slicer_intent.rank(preference,printer) == 0:
        return False
    for candidate in (await session.execute(select(Printer))).scalars():
        if slicer_intent.rank(preference,candidate) >= slicer_intent.rank(preference,printer):
            continue
        if not candidate.enabled or not candidate.queue_on or not candidate.configured or not engine._mgr.is_printer_ready(candidate.id):
            continue
        if candidate.id in engine._pool_busy.values() or scheduling.in_quiet_hours(candidate.quiet_start,candidate.quiet_end):
            continue
        if not await pools.eligible_on(session,group,candidate):
            continue
        if not await pools.process_for(session,group,candidate) and not candidate.default_process_profile:
            continue
        conflicts = await filament_matching.conflicts_for(candidate.loaded_filaments or [])
        if await pools.takeable(session,group,candidate,conflicts):
            return True
    return False
