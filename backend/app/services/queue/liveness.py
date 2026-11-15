"""Printers that stay offline past the cutoff are declared dead and their work released; jobs nothing in the fleet could take are blocked.

Methods of `QueueEngine` (app/services/queue_engine.py), which inherits this mixin: `self` is the engine. Names that
module defines, and the collaborators tests replace there, are read as `_qe.<name>` at call time. See README.md here.
"""
from __future__ import annotations
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ...models import (
    GcodeFile,
    Job,
    JobPrinterConfig,
    Printer,
    Project,
    QueueConfig,
    UploadedFile,
)
from ..library_scanner import (
    is_presliced_file,
)
from .. import gcode_eligibility, model_targets, part_waivers
from .. import completion_events, job_details
from ...eventing.hub import hub as event_hub
from .. import filament_matching, filament_tuple

import app.services.queue_engine as _qe


class LivenessMixin:
    # --- printer liveness: a printer offline past the user's cutoff is dead -------------------------------------------

    async def _dead_after_minutes(self) -> int:
        """`queue_config.printer_dead_after_minutes` (0 = a printer is never declared dead)."""
        try:
            async with self._factory() as session:
                cfg = await session.get(QueueConfig, 1)
                return cfg.printer_dead_after_minutes if cfg is not None else 30
        except Exception:
            _qe.logger.exception("Failed to read the printer dead-after setting; using 30 minutes")
            return 30

    async def on_connectivity(self, printer_id: int, edge: str, was_dead: bool) -> None:
        """A printer that was offline is back. Nothing happens for a blip that healed before the cutoff, unless work is waiting for
        it: a job sliced for it while it was away (undead) is picked up by waking the queue, and the normal ready path uploads and
        starts it only if the printer is idle with a clear plate. A dead printer returning reopens what its absence blocked."""
        if edge in ("slots", "configured"):                  # what is loaded changed / setup just confirmed (BIZ-369): re-match
            self.wake()
            return
        if edge != "online":
            return
        if was_dead:
            self.wake()
            return
        async with self._factory() as session:
            if await self._has_pending_sliced_job(session, printer_id):
                self.wake()

    async def _liveness_pass(self) -> None:
        """Sample every printer's connection, hand the printers that came back to `on_connectivity`, and declare the ones that crossed
        the cutoff dead (each once per outage)."""
        for printer_id, edge, was_dead in self._mgr.sample_all():
            try:
                await self.on_connectivity(printer_id, edge, was_dead)
            except Exception:
                _qe.logger.exception("Connectivity handling failed for printer %s", printer_id)
        cutoff = await self._dead_after_minutes()
        for printer_id in self._mgr.newly_dead(cutoff):
            try:
                await self._declare_dead(printer_id, cutoff)
            except Exception:
                _qe.logger.exception("Declaring printer %s dead failed", printer_id)

    async def _declare_dead(self, printer_id: int, cutoff_minutes: int) -> None:
        """A dead printer cannot hold its work. Its `printing` job fails (`job.failed`, origin core) and returns to the TOP of the queue;
        a job merely sliced for it (not started) goes back to `queued` where it was, because only that printer could start it. The
        plate gate stays set: a half-printed part may be on the bed, so the user still clears it when the printer returns."""
        reason = f"printer offline for more than {cutoff_minutes} minutes"
        _qe.logger.warning("Printer %s is dead (%s)", printer_id, reason)
        async with self._factory() as session:
            printing = (await session.execute(select(Job.id).where(Job.status == "printing", Job.assigned_printer_id == printer_id))).scalars().all()
            sliced = (await session.execute(select(Job.id).join(GcodeFile, and_(GcodeFile.job_id == Job.id, GcodeFile.printer_id == printer_id))
                                            .where(Job.status == "sliced"))).scalars().all()
        for job_id in printing:
            await self.requeue_dead_print(job_id, printer_id, reason)
        for job_id in sliced:
            await self._release_sliced_job(job_id, printer_id)
        self.wake()

    async def requeue_dead_print(self, job_id: int, printer_id: int, reason: str) -> bool:
        async with self._factory() as session:
            now = _qe._now()
            top = (await session.execute(select(func.min(Job.queue_position)).where(
                Job.status.in_(("queued", "blocked", "sliced", "slicing", "uploading"))))).scalar()
            claim = await session.execute(update(Job).where(Job.id == job_id, Job.status == "printing").values(
                status="queued", queue_position=(top if top is not None else 1.0) - 1.0, assigned_printer_id=None,
                completed_at=None, block_reason=None, updated_at=now))
            if claim.rowcount == 0:
                return False                                   # a late completion won: nothing to requeue
            await self._cleanup_gcode(session, job_id, printer_id)
            await job_details.set_details(session, job_id, "core", {"last_failure": {"reason": reason, "printer_id": printer_id, "at": now}})
            printer = await session.get(Printer, printer_id)
            await completion_events.enqueue_attempt_ended(session, job_id, printer_id, result="requeued", origin="core",
                                                          loaded_filaments=printer.loaded_filaments if printer else None)
            await session.commit()
        event_hub.wake()
        await self._broadcast_job(job_id)
        await self._publish_job_event("job.failed", job_id, printer_id, "core", {"reason": reason, "phase": "print"},
                                      dedup=f"job.failed:{job_id}:{now}")
        await self._fire_webhooks(job_id, "job.failed")
        await self._fire_notifications(job_id, "job.failed", printer_id=printer_id, reason=reason, origin="core")
        return True

    async def _release_sliced_job(self, job_id: int, printer_id: int) -> None:
        async with self._factory() as session:
            claim = await session.execute(update(Job).where(Job.id == job_id, Job.status == "sliced").values(
                status="queued", assigned_printer_id=None, updated_at=_qe._now()))
            if claim.rowcount == 0:
                return
            await self._cleanup_gcode(session, job_id, printer_id)
            await session.commit()
        await self._broadcast_job(job_id)

    # --- a job nothing in the fleet could ever take is blocked: waiting cannot fix it --------------------------------------

    def _counts_for_matching(self, printer: Printer, cutoff_minutes: int) -> bool:
        """A printer that could take a job once the situation changes without anyone touching the fleet: every printer except a DEAD one
        (offline for the user's cutoff). Online and undead (offline under the cutoff) count; so do printers the operator switched off or
        whose plugin is dormant, and ones with no client yet (just booted): none of those makes a job *impossible*, and blocking and
        announcing jobs the moment someone toggles a queue or restarts Themis would be noise."""
        return self._mgr.liveness(printer.id, cutoff_minutes) != "dead"

    async def _no_match_reason(self, session: AsyncSession, job: Job, configs: list[JobPrinterConfig], printers: dict[int, Printer],
                               counted: dict[int, Printer], conflicts: dict[int, set]) -> tuple[str | None, dict[str, list[int]]]:
        """Why no printer in the fleet could take `job` (None when one could, or when a per-printer path already owns the block)."""
        configs = [c for c in configs if c.printer_id in printers]
        if not configs:
            if job.min_bed_mm:
                size = job.min_bed_mm
                return f"{_qe.NO_MATCH_PREFIX}: plate requires {size['x']:g} x {size['y']:g} x {size.get('z') or 0:g} mm; no compatible printer has a large enough bed", {}
            return f"{_qe.NO_MATCH_PREFIX}: no printer in the fleet is compatible with this job", {}
        candidates = [(c, counted[c.printer_id]) for c in configs if c.printer_id in counted]
        if not candidates:
            return f"{_qe.NO_MATCH_PREFIX}: every compatible printer has been offline past the limit", {}
        source_file = await session.get(UploadedFile, job.uploaded_file_id)
        reasons: list[str] = []
        unmatched: dict[str, list[int]] = {}
        for config, printer in candidates:
            if not _qe.preference.allowed(job.printer_preference,printer):
                reasons.append(f"{printer.name}: outside the strict printer preference")
                continue
            bed_reason = await _qe.bed_requirement.reason(session, job.min_bed_mm, printer)
            if bed_reason:
                reasons.append(bed_reason)
                continue
            if config.slice_failed:
                return None, {}                                  # the slicing-failure path blocks and rescues this one
            if source_file is not None and is_presliced_file(source_file):
                verdict = await gcode_eligibility.evaluate(session, source_file, printer, confirmed=bool(job.eligibility_confirmed))
                if not verdict.allowed:
                    reasons.append(f"{printer.name}: {verdict.message}")
                    continue
            fit = filament_matching.evaluate_for_printer(config, printer, conflicts.get(printer.id, set()))
            missing = _qe.missing_process_reason(source_file, config, printer) if source_file is not None else None
            if fit.eligible and not missing and await part_waivers.keys_of_job(session, job) & await part_waivers.waived_keys(session, printer.id):
                reasons.append(f"{printer.name} is waived for a part on this plate")
                continue
            if fit.eligible and not missing:
                return None, {}
            if missing:
                reasons.append(missing)
            if fit.eligible:
                continue
            if fit.unmatched_filaments:
                unmatched[str(printer.id)] = fit.unmatched_filaments
                entries = {e.get("model_filament"): e for e in config.filament_map or []}
                failures = []
                for mf in fit.unmatched_filaments:
                    entry = entries.get(mf)
                    ask = filament_tuple.describe(filament_matching.entry_ask(entry) if entry is not None
                                                  else filament_matching.config_ask(config))
                    pin = entry.get("tool_index") if entry is not None else config.tool_index
                    detail = f"T{pin} unavailable" if pin is not None else "no loaded match"
                    # Keep slot diagnostics too: a conflicting spool, missing tool or empty slot should be actionable.
                    failures.append(f"Filament {mf} ({ask}): {detail}")
                reasons.append(f"{printer.name}: " + "; ".join(failures))
                reasons.extend(f"{printer.name}: {reason}" for reason in fit.reasons)
            else:
                reasons.append(f"{printer.name}: " + "; ".join(fit.reasons))
        unique = list(dict.fromkeys(r for r in reasons if r))
        return f"{_qe.NO_MATCH_PREFIX}: " + ("; ".join(unique) or "no printer in the fleet can run this job"), unmatched

    async def _sweep_unmatched_jobs(self) -> None:
        """Each cycle, for every job that is up for work (queued, due, its project promoted): when no printer in the fleet could take it
        — whatever its current state — block it (one `job.blocked`, plus webhook/notification) because requeue-and-wait cannot fix
        it. A job this sweep blocked is released back to `queued` as soon as a match exists (a printer added or back, filament
        loaded). Blocks with any other cause are left alone."""
        cutoff = await self._dead_after_minutes()
        if not isinstance(self._mgr.liveness(0, cutoff), (str, type(None))):
            return                                           # a manager that cannot report liveness: no basis to decide
        now = _qe._now()
        async with self._factory() as session:
            jobs = (await session.execute(
                select(Job).outerjoin(Project, Project.id == Job.project_id)
                .where(or_(Job.status == "queued", and_(Job.status == "blocked", Job.block_reason.like(f"{_qe.NO_MATCH_PREFIX}%"))))
                .where(or_(Job.project_id.is_(None), Project.stage == "queued"))
                .where(or_(Job.not_before.is_(None), Job.not_before <= now))
                .order_by(Job.queue_position.asc()))).scalars().all()
            if not jobs:
                return
            printers = {p.id: p for p in (await session.execute(select(Printer))).scalars()}
            counted = {pid: p for pid, p in printers.items() if self._counts_for_matching(p, cutoff)}
            for printer in counted.values():          # a printer skipped by the claim loop (quiet hours, pending sliced work) must still have its model targets materialized
                try:
                    await model_targets.sync_targets_for_printer(session, printer)
                except Exception:                     # one printer's failure must not stop the others being swept
                    _qe.logger.exception("Syncing model targets for printer %s failed", printer.id)
                    await session.rollback()
            await session.commit()
            conflicts = {pid: await filament_matching.conflicts_for(p.loaded_filaments or []) for pid, p in counted.items()}
            by_job: dict[int, list[JobPrinterConfig]] = {}
            for c in (await session.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id.in_([j.id for j in jobs])))).scalars():
                by_job.setdefault(c.job_id, []).append(c)
            for job in jobs:
                reason, unmatched = await self._no_match_reason(session, job, by_job.get(job.id, []), printers, counted, conflicts)
                if reason is None:
                    if job.status == "blocked":
                        released = await session.execute(update(Job).where(
                            Job.id == job.id, Job.status == "blocked", Job.block_reason.like(f"{_qe.NO_MATCH_PREFIX}%")).values(
                            status="queued", block_reason=None, unmatched_filaments=None, updated_at=_qe._now()))
                        await session.commit()
                        if released.rowcount:
                            await self._broadcast_job(job.id)
                elif job.status == "queued":
                    await self._block_job(session, job, reason, notify=True, unmatched_filaments=unmatched)
                elif job.block_reason != reason or job.unmatched_filaments != unmatched:
                    # Already ours and still blocked, only the wording moved (a slot changed, reasons reordered): update it quietly, one
                    # announcement per block, not one per cycle.
                    await session.execute(update(Job).where(Job.id == job.id, Job.status == "blocked",
                                                            Job.block_reason.like(f"{_qe.NO_MATCH_PREFIX}%")).values(block_reason=reason, unmatched_filaments=unmatched, updated_at=_qe._now()))
                    await session.commit()
                    await self._broadcast_job(job.id)
