"""From sliced to done: upload and start, the plate-clear gate, and the started / completed / failed / blocked transitions printers report.

Methods of `QueueEngine` (app/services/queue_engine.py), which inherits this mixin: `self` is the engine. Names that
module defines, and the collaborators tests replace there, are read as `_qe.<name>` at call time. See README.md here.
"""
from __future__ import annotations
import asyncio
import os
from typing import Any, Mapping
from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ...models import (
    JobDetail,
    GcodeFile,
    Job,
    JobPrinterConfig,
    Printer,
)
from .. import completion_events, job_details
from ...eventing.hub import hub as event_hub
from .. import filament_matching

import app.services.queue_engine as _qe


class PrintingMixin:
    async def _reconcile_printing_jobs(self) -> None:
        """Reconcile jobs stuck in 'printing' against live printer state.

        Runs every queue cycle to catch missed _on_print_complete callbacks —
        not just on restart but also after MQTT reconnects or network blips.

        Decision matrix (printer must be connected + idle to act):
        - Printer not connected or not idle → skip (still in progress, paused, or offline)
        - Printer idle + normalized state FAILED → physical cancel on printer → job 'failed'
        - Printer idle + any other state → print completed → job 'complete'

        Bambu note: physical cancel goes to IDLE (no distinct cancelled state in the
        firmware), so it's indistinguishable from a successful finish here. The
        awaiting_plate_clear gate still holds the printer until the user clears the plate.
        """
        async with self._factory() as session:
            result = await session.execute(select(Job).where(Job.status == "printing"))
            jobs_to_check = [
                (job.id, job.assigned_printer_id)
                for job in result.scalars().all()
            ]

        for job_id, printer_id in jobs_to_check:
            if printer_id is None:
                continue
            client = self._mgr._clients.get(printer_id)
            if client is None or not client.connected or not client.is_idle:
                continue  # still printing, paused, or offline — leave it alone

            # Printer is connected and idle: the print has ended one way or another.
            # Use the normalized state to distinguish a clean idle from a cancel/failure.
            ended_in_failure = False
            try:
                normalized = self._mgr.get_normalized_state(printer_id)
                ended_in_failure = normalized.get("state") == "FAILED"
            except Exception:
                _qe.logger.exception("Reconcile: could not get state for printer %s", printer_id)
                continue

            if ended_in_failure:
                # Elegoo/Snapmaker physical cancel: normalized state is FAILED.
                # Mark the job failed so the user can adjust settings before re-queueing.
                async with self._factory() as session:
                    origin = await self._origin_of(session, printer_id)
                await self.fail_started_job(job_id, printer_id, origin=origin,
                                            reason="print cancelled or ended with failure on the printer")
            else:
                # Normal completion (or Bambu cancel, which is indistinguishable from
                # a successful finish at the firmware level). Delegate to the same
                # handle_print_complete path used by the normal callback so gcode cleanup
                # and broadcasting are consistent.
                _qe.logger.info(
                    "Reconcile: job %s → complete (printer %s idle)", job_id, printer_id,
                )
                await self.handle_print_complete(printer_id)

    async def _has_pending_sliced_job(self, session: AsyncSession, printer_id: int) -> bool:
        stmt = (
            select(Job.id)
            .join(GcodeFile, and_(GcodeFile.job_id == Job.id, GcodeFile.printer_id == printer_id))
            .where(Job.status == "sliced")
            .limit(1)
        )
        return (await session.execute(stmt)).first() is not None

    async def _try_resume_sliced_job(self, session: AsyncSession, printer_id: int) -> bool:
        """Pick up a pre-sliced job (gcode exists) and proceed to upload+print."""
        stmt = (
            select(Job, GcodeFile)
            .join(GcodeFile, and_(GcodeFile.job_id == Job.id, GcodeFile.printer_id == printer_id))
            .where(Job.status == "sliced")
            .order_by(Job.queue_position.asc())
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
        if row is None:
            return False

        job, gcode = row
        if not os.path.exists(gcode.path):
            _qe.logger.warning("Sliced gcode missing for job %s (printer %s); re-queuing", job.id, printer_id)
            await session.delete(gcode)
            job.status = "queued"
            job.assigned_printer_id = None
            job.updated_at = _qe._now()
            await session.commit()
            await self._broadcast_job(job.id)
            return False

        config_result = await session.execute(
            select(JobPrinterConfig).where(
                JobPrinterConfig.job_id == job.id,
                JobPrinterConfig.printer_id == printer_id,
            )
        )
        config = config_result.scalar_one_or_none()
        printer = await session.get(Printer, printer_id)
        if printer is not None:
            bed_reason = await _qe.bed_requirement.reason(session, job.min_bed_mm, printer)
            if bed_reason:
                await self._block_job(session, job, bed_reason)
                return False
        loaded = (printer.loaded_filaments if printer else None) or []
        try:
            ams_tray_id = _qe._ams_mapping(filament_matching.plan_for(config, loaded), loaded)
        except ValueError as exc:
            await self._handle_slice_failure(job.id, printer_id, str(exc))
            return False
        gcode_path = gcode.path
        plate_number = job.plate_number

        # Conditional UPDATE guards against a status change (e.g. a user cancel) that
        # committed during the DB round-trips above (config/printer lookups).
        result = await session.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "sliced")
            .values(status="uploading", assigned_printer_id=printer_id, printed_on_printer_id=printer_id,
                    updated_at=_qe._now())
        )
        if result.rowcount == 0:
            return False  # no longer claimable
        await session.commit()
        self._spawn(
            self._do_upload_and_print(job.id, printer_id, gcode_path, plate_number, ams_tray_id),
            name=f"upload-{job.id}-{printer_id}",
        )
        await self._broadcast_job(job.id)
        return True

    async def _do_upload_and_print(
        self, job_id: int, printer_id: int, gcode_path: str, plate_number: int, ams_tray_id
    ) -> None:
        """Upload an already-sliced gcode file to the printer and start the print."""
        loop = asyncio.get_running_loop()
        client = self._mgr.get_client(printer_id)
        gcode_filename = os.path.basename(gcode_path)

        if client.file_upload_supported:
            upload_error_msg = None
            try:
                with open(gcode_path, "rb") as fh:
                    data = fh.read()
                upload_ok = await loop.run_in_executor(
                    self._executor, client.upload_file, data, gcode_filename
                )
            except Exception as e:
                _qe.logger.exception("Gcode upload failed for job %s on printer %s", job_id, printer_id)
                upload_ok = False
                upload_error_msg = f"Gcode upload failed: {e}"
            if not upload_ok:
                _qe.logger.warning("Upload of %s to printer %s reported failure for job %s",
                               gcode_filename, printer_id, job_id)
                reason = upload_error_msg or "Gcode upload reported failure by printer"
                await self._fail_job_post_slice(job_id, printer_id, reason)
                return

        from ..abstract_printer_client import StartPrintOptions
        opts = StartPrintOptions(
            plate_id=plate_number,
            gcode_path=gcode_filename,
            ams_mapping=ams_tray_id if isinstance(ams_tray_id, list) else ([ams_tray_id] if ams_tray_id is not None else None),
        )
        start_error_msg = None
        try:
            start_ok = await loop.run_in_executor(
                self._executor, client.start_print, gcode_filename, opts
            )
        except Exception as e:
            _qe.logger.exception("start_print failed for job %s on printer %s", job_id, printer_id)
            start_ok = False
            start_error_msg = f"Start print failed: {e}"
        if not start_ok:
            _qe.logger.warning("start_print of %s on printer %s reported failure for job %s",
                           gcode_filename, printer_id, job_id)
            reason = start_error_msg or "Start print reported failure by printer"
            await self._fail_job_post_slice(job_id, printer_id, reason)
            return

        async with self._factory() as session:
            origin = await self._origin_of(session, printer_id)
        if not await self.mark_printing(job_id, printer_id, origin=origin):
            return
        await self._broadcast_job(job_id)

    async def _origin_of(self, session: AsyncSession, printer_id: int | None) -> str | None:
        """The plugin that owns a printer: the `origin` of the events core publishes for what that printer's plugin reported."""
        printer = await session.get(Printer, printer_id) if printer_id is not None else None
        return printer.plugin_id if printer is not None else None

    async def _cleanup_gcode(self, session: AsyncSession, job_id: int, printer_id: int) -> None:
        gcode_result = await session.execute(select(GcodeFile).where(GcodeFile.job_id == job_id, GcodeFile.printer_id == printer_id))
        gcode = gcode_result.scalar_one_or_none()
        if gcode:
            try:
                os.remove(gcode.path)
            except OSError:
                pass
            await session.delete(gcode)

    async def _hold_plate_gate(self, session: AsyncSession, printer_id: int) -> Printer | None:
        """The printer must not auto-claim the next job onto an uncleared plate: set from the start of a print, and again at its end
        (a missed completion callback can never let it grab another job)."""
        self._mgr.set_awaiting_plate_clear(printer_id, True)
        printer = await session.get(Printer, printer_id)
        if printer is not None:
            printer.awaiting_plate_clear = True
        return printer

    async def mark_printing(self, job_id: int, printer_id: int, *, origin: str | None, details: Mapping[str, Any] | None = None,
                            only_from: tuple[str, ...] | None = None) -> bool:
        """The printer began a job's physical print: status `printing`, the plate gate, the print-start detail and the durable
        `job.started` event in ONE transaction. `only_from` makes it an atomic claim (a repeat or a job in another state is a no-op
        returning False); without it only a cancelled/complete job is refused (the queue's own start path)."""
        async with self._factory() as session:
            now = _qe._now()
            if only_from is not None:
                claim = await session.execute(update(Job).where(Job.id == job_id, Job.status.in_(only_from))
                                              .values(status="printing", printed_on_printer_id=printer_id, updated_at=now))
                if claim.rowcount == 0:
                    return False
                job = await session.get(Job, job_id)
            else:
                job = await session.get(Job, job_id)
                if job is None or job.status in ("cancelled", "complete"):
                    return False
                job.status = "printing"
                job.printed_on_printer_id = printer_id
                job.updated_at = now
            printer = await self._hold_plate_gate(session, printer_id)
            await job_details.set_details(session, job_id, origin or "core", {"print_start_time": now, **(details or {})})
            # Observers (inventory snapshots the spools in use) react to this durable event; the queue names none of them.
            await completion_events.enqueue_job_started(session, job, printer_id, origin=origin or "core",
                                                        loaded_filaments=(printer.loaded_filaments if printer else None))
            await session.commit()
        event_hub.wake()
        return True

    async def handle_print_complete(self, printer_id: int) -> None:
        """Called by PrinterManager when the printer's vendor client signals print done: the printer's plugin reporting `complete`."""
        async with self._factory() as session:
            job = (await session.execute(select(Job).where(Job.status == "printing", Job.assigned_printer_id == printer_id))
                   ).scalar_one_or_none()
            if job is None:
                return
            job_id, origin = job.id, await self._origin_of(session, printer_id)
        await self.complete_job(job_id, printer_id, origin=origin)

    async def complete_job(self, job_id: int, printer_id: int, *, origin: str | None, details: Mapping[str, Any] | None = None) -> bool:
        """Finish a `printing` job: the status, gcode cleanup, plate gate, the reported details and the durable `job.complete` event
        in ONE transaction (so a forced failure leaves none of them). False when another completion path already won."""
        async with self._factory() as session:
            now = _qe._now()
            # Conditional UPDATE claims the completion atomically. The vendor client's completion callback and
            # _reconcile_printing_jobs can both observe status=="printing" for the same job (several awaits separate that read
            # from this commit); without the guard both would proceed and double the inventory deduction, lifetime counters, and
            # job.complete webhook. Only the caller that flips the row wins.
            claim = await session.execute(update(Job).where(Job.id == job_id, Job.status == "printing")
                                          .values(status="complete", completed_at=now, updated_at=now))
            if claim.rowcount == 0:
                return False
            # The wear counters, the spool deduction and the notices are effects of the durable `job.complete` event written below,
            # in THIS transaction (completion_events.py): they cannot be lost after the commit and cannot run twice. The printer's
            # loaded slots travel with it, because they may change once the print is over.
            job = await session.get(Job, job_id)
            printer = await self._hold_plate_gate(session, printer_id)
            await self._cleanup_gcode(session, job_id, printer_id)
            reported: dict[str, Any] = {"print_end_time": now, "result": "success"}
            if job.actual_seconds is not None:
                reported["print_seconds"] = job.actual_seconds
            if job.actual_filament_grams is not None:
                reported["filament_usage"] = {"grams": job.actual_filament_grams, "breakdown": job.actual_filament_breakdown}
            await job_details.set_details(session, job_id, origin or "core", {**reported, **(details or {})})
            await session.execute(delete(JobDetail).where(JobDetail.job_id == job_id, JobDetail.field == "blocked_reason"))
            await completion_events.enqueue_job_complete(session, job, printer_id, source="queue", origin=origin or "core",
                                                         loaded_filaments=printer.loaded_filaments if printer else None)
            await session.commit()
        event_hub.wake()
        return True

    async def fail_started_job(self, job_id: int, printer_id: int, *, reason: str, origin: str | None,
                               details: Mapping[str, Any] | None = None, fire_notices: bool = False) -> bool:
        """A started print ended in failure: `failed` (terminal), the printer KEPT (after the start only a user releases it), no spool
        deduction, the failure details, and a best-effort `job.failed`. False when the job is not `printing` any more."""
        async with self._factory() as session:
            now = _qe._now()
            claim = await session.execute(update(Job).where(Job.id == job_id, Job.status == "printing")
                                          .values(status="failed", completed_at=now, updated_at=now, block_reason=reason,
                                                  deduction_skipped=True))
            if claim.rowcount == 0:
                return False
            await self._cleanup_gcode(session, job_id, printer_id)
            await job_details.set_details(session, job_id, origin or "core",
                                          {"print_end_time": now, "result": "failure", "failure_reason": reason, **(details or {})})
            await session.execute(delete(JobDetail).where(JobDetail.job_id == job_id, JobDetail.field == "blocked_reason"))
            printer = await session.get(Printer, printer_id)
            await completion_events.enqueue_attempt_ended(session, job_id, printer_id, result="failed", origin=origin or "core",
                                                          loaded_filaments=printer.loaded_filaments if printer else None)
            await session.commit()
        event_hub.wake()
        _qe.logger.warning("Job %s → failed on printer %s: %s", job_id, printer_id, reason)
        await self._broadcast_job(job_id)
        await self._publish_job_event("job.failed", job_id, printer_id, origin, {"reason": reason, "phase": "print"}, dedup=f"job.failed:{job_id}:{now}")
        if fire_notices:
            await self._fire_webhooks(job_id, "job.failed")
            await self._fire_notifications(job_id, "job.failed", printer_id=printer_id, reason=reason, origin=origin)
        return True

    async def note_print_blocked(self, job_id: int, printer_id: int, *, origin: str | None, reason: str,
                                 details: Mapping[str, Any] | None = None) -> bool:
        """A printer halted a started print (filament runout, a hardware fault, ...). A NOTICE ONLY: the job stays `printing` on its
        printer (`blocked` elsewhere means "released, re-evaluate": flipping it here would let another printer claim a job whose
        physical print is only paused). The reason is kept as the `blocked_reason` detail and published as best-effort `job.blocked`
        (`phase: "print"`); the same reason repeated while the job still prints is one notice. The print ends through `complete` /
        `failed`, which clear it. False for a job that is not `printing` or a repeated reason."""
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status != "printing":
                return False
            who = origin or "core"
            known = (await session.execute(select(JobDetail).where(JobDetail.job_id == job_id, JobDetail.field == "blocked_reason",
                                                                  JobDetail.origin == who))).scalar_one_or_none()
            if known is not None and known.value == reason:
                return False
            await job_details.set_details(session, job_id, who, {"blocked_reason": reason, **(details or {})})
            await session.commit()
        await self._publish_job_event("job.blocked", job_id, printer_id, origin, {"reason": reason, "phase": "print"},
                                      dedup=f"job.blocked:{job_id}:print:{_qe._now()}")
        await self._fire_webhooks(job_id, "job.blocked")
        await self._fire_notifications(job_id, "job.blocked", printer_id=printer_id, reason=reason, origin=origin)
        return True
