from __future__ import annotations
import asyncio
import itertools
import logging
import os
import re
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import get_library_dir
from ..models import (
    GcodeFile,
    Job,
    JobPrinterConfig,
    NotificationConfig,
    Printer,
    Project,
    QueueConfig,
    SlicedVersion,
    UploadedFile,
)
from .library_scanner import (
    fresh_content_hash, is_presliced_file, library_abs_path, presliced_suffix, refresh_content_hash,
)
from .printer_manager import PrinterManager
from .inventory import config as inventory_config, deduction as inventory_deduction, refs as inventory_refs, snapshots as inventory_snapshots, tasks as inventory_tasks
from .providers.slicing import SlicingProviderNotReady, get_format_provider, get_slicing_provider
from .slicer_service import SliceError, SliceRequest, SlicerService, tool_mapping_hook
from . import gcode_eligibility, model_targets, slice_cache, slice_saver
from . import completion_events, notification_service
from ..eventing.hub import hub as event_hub
from . import scheduling
from . import webhook_service


logger = logging.getLogger(__name__)


_DEFAULT_CHECK_MINUTES = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_color(value) -> str:
    return str(value or "").strip().lstrip("#").lower()


def _is_any_filament_ask(value) -> bool:
    """True if a JobPrinterConfig.filament_type/filament_color value expresses 'no
    constraint on this field' — blank, or the canonical "any" keyword (case-insensitive).
    Scoped to JobPrinterConfig's own type/color fields only; filament_map entries have
    their own, unrelated NULL semantics and must not go through this."""
    return str(value or "").strip().lower() in ("", "any")


def _is_loaded(slot: dict) -> bool:
    """False only for a tool-changer placeholder (Snapmaker U1 keeps `{"empty": True, "type": ""}` for an empty tool
    so list positions stay equal to tool indexes). A hand-entered slot without a type is still a slot."""
    return bool(str(slot.get("type", "")).strip()) or not slot.get("empty")


def _matching_loaded_filament(config: JobPrinterConfig, loaded: list) -> dict | None:
    """The printer's loaded filament slot that satisfies the job's ask (type AND
    color), or None. A job with no declared requirement (blank or "any") matches
    the first slot.

    The OrcaSlicer filament *profile* used for slicing is a printer-level setting
    that lives on the matched slot (the "provide"); the job only declares the
    desired type/color (the "ask")."""
    req_type = "" if _is_any_filament_ask(config.filament_type) else (config.filament_type or "").strip().lower()
    req_color = "" if _is_any_filament_ask(config.filament_color) else _norm_color(config.filament_color)
    if not req_type and not req_color:
        return next((f for f in loaded or [] if _is_loaded(f)), None)
    for f in loaded or []:
        if str(f.get("type", "")).strip().lower() == req_type and _norm_color(f.get("color")) == req_color:
            return f
    return None


def _slot_for_config(config, loaded: list) -> dict | None:
    """The loaded slot this config should print with: the explicit tool_index slot
    if set (multi-tool printers), else the type/color ask match."""
    ti = getattr(config, "tool_index", None)
    if ti is not None:
        loaded = loaded or []
        return loaded[ti] if 0 <= ti < len(loaded) and _is_loaded(loaded[ti]) else None
    return _matching_loaded_filament(config, loaded)


def _find_slot_for_filament(
    filament_type: str, filament_color: str | None, loaded: list
) -> int | None:
    """Return the index of the first loaded slot matching type (+color if given), or None."""
    req_type = filament_type.strip().lower()
    req_color = _norm_color(filament_color)
    for i, lf in enumerate(loaded or []):
        if (lf.get("type") or "").strip().lower() != req_type:
            continue
        if req_color and _norm_color(lf.get("color")) != req_color:
            continue
        return i
    return None


def _mapped_tools_loaded(filament_map: list, loaded: list) -> bool:
    """True if every slot-assigned entry's tool_index is within the loaded slots list.
    Catalog entries (tool_index is None) are skipped."""
    loaded = loaded or []
    return all(
        0 <= e["tool_index"] < len(loaded) and _is_loaded(loaded[e["tool_index"]])
        for e in (filament_map or [])
        if e.get("tool_index") is not None
    )


def _resolve_filament_map(filament_map: list, loaded: list) -> list:
    """Resolve any catalog-assigned entries (filament_type set, tool_index None)
    to their matching loaded slot index. Returns a new list with all entries
    having tool_index set. Raises ValueError if any catalog entry has no match."""
    resolved = []
    for entry in filament_map:
        if entry.get("tool_index") is not None:
            resolved.append(entry)
        else:
            ft = entry.get("filament_type")
            if not ft:
                raise ValueError("Catalog filament entry is missing filament_type — cannot resolve slot")
            fc = entry.get("filament_color")
            slot_idx = _find_slot_for_filament(ft, fc, loaded or [])
            if slot_idx is None:
                raise ValueError(
                    f"Filament {ft!r} not loaded on printer — cannot slice"
                )
            resolved.append({**entry, "tool_index": slot_idx})
    seen_slots: set[int] = set()
    for entry in resolved:
        ti = entry["tool_index"]
        if ti in seen_slots:
            raise ValueError(
                f"Two model filaments resolved to the same printer slot {ti}"
            )
        seen_slots.add(ti)
    return resolved


def _filament_mismatch(config: JobPrinterConfig, loaded: list) -> str | None:
    """Return a reason string if the config can't be satisfied by the printer's
    loaded filaments, else None."""
    fmap = getattr(config, "filament_map", None)
    if fmap:
        if not _mapped_tools_loaded(fmap, loaded):
            return "a mapped tool has no loaded filament"
        for entry in fmap:
            if entry.get("tool_index") is not None:
                continue  # slot assignment — already validated by _mapped_tools_loaded
            ft = entry.get("filament_type")
            if ft is None:
                continue
            if _find_slot_for_filament(ft, entry.get("filament_color"), loaded or []) is None:
                return f"required filament {ft!r} not loaded"
        return None
    if getattr(config, "tool_index", None) is not None:
        if _slot_for_config(config, loaded) is None:
            return f"tool T{config.tool_index} has no loaded filament"
        return None
    req_type = "" if _is_any_filament_ask(config.filament_type) else (config.filament_type or "").strip().lower()
    req_color = "" if _is_any_filament_ask(config.filament_color) else _norm_color(config.filament_color)
    if not req_type and not req_color:
        return None
    if _matching_loaded_filament(config, loaded) is not None:
        return None
    return (f"loaded filament doesn't match required "
            f"{config.filament_type or '?'} {config.filament_color or '?'}")



def _slice_params(job, config, printer, uploaded_file) -> dict:
    """What a slice of `job` on `printer` is built from, read off the ORM rows so it outlives the session. Dispatch and
    the claim-time cache check (BIZ-201) both build their SliceRequest from this, so they agree on the cache key."""
    loaded = (printer.loaded_filaments if printer else None) or []
    slot = _slot_for_config(config, loaded) if config else None
    return {
        "stored_path": (str(library_abs_path(get_library_dir(), uploaded_file.relative_path))
                        if uploaded_file else None),
        "original_filename": uploaded_file.original_filename if uploaded_file else None,
        "machine_preset": printer.current_orca_printer_profile if printer else None,
        "build_plate_type": printer.build_plate_type if printer else None,
        "loaded": loaded,
        "print_profile": config.print_profile if config else None,
        "filament_color": config.filament_color if config else None,
        # Filament profile: job-level config takes priority; slot's preset is the fallback.
        "filament_profile": (config.filament_profile if config else None) or (slot or {}).get("filament_profile") or None,
        "tool_index": config.tool_index if config else None,
        "filament_map": config.filament_map if config else None,
        "overrides": (job.overrides or {}) if job else {},
    }

def _notification_content(
    event: str, job_id: int, file_name: str, printer_name: str | None, reason: str | None
) -> tuple[str, str]:
    """Build a plain, human-readable (title, message) pair for a job event.
    No templating engine, no config-driven customization — basic per-event
    text, mirroring the fixed shape of the built-in notification channels."""
    if event == "job.complete":
        title = "Themis: job complete"
        if printer_name:
            message = f"{file_name} finished printing on {printer_name}."
        else:
            message = f"{file_name} finished printing."
        return title, message
    if event == "job.failed":
        title = "Themis: job failed"
        if printer_name:
            message = f"{file_name} failed on {printer_name}"
        else:
            message = f"{file_name} failed"
        if reason:
            message = f"{message}: {reason}"
        else:
            message = f"{message}."
        return title, message
    if event == "job.blocked":
        title = "Themis: job blocked"
        if reason:
            message = f"{file_name} is blocked: {reason}"
        else:
            message = f"{file_name} is blocked."
        return title, message
    return f"Themis: {event}", f"{file_name} ({event})"


class QueueEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        printer_manager: PrinterManager,
        slicer_service: SlicerService,
        broadcast_cb: Callable | None = None,
    ) -> None:
        self._factory = session_factory
        self._mgr = printer_manager
        self._slicer = slicer_service
        self._broadcast_cb = broadcast_cb
        self._event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="slicer")
        self._slice_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._slice_seq: itertools.count = itertools.count()
        self._slice_worker_task: asyncio.Task | None = None
        self._estimate_tasks: set[asyncio.Task] = set()
        completion_events.bind_engine(self)      # the `job_complete.notices` subscriber sends through the engine that was last built

    async def _slice_worker(self) -> None:
        while True:
            priority, _seq, coro = await self._slice_queue.get()
            try:
                await coro
            # CancelledError (BaseException) propagates unimpeded — do not broaden this catch
            except Exception:
                logger.exception("Slice worker: unhandled exception in queued coro")
            finally:
                self._slice_queue.task_done()

    def spawn_save_slice(
        self, job_id: int, printer_id: int, artifact_path: str, inputs: "slice_cache.CacheKeyInputs | None",
    ) -> None:
        """Save a finished production slice to the library as a cached version (BIZ-192), in the background: the job
        carries on to upload/print meanwhile, and a failed save is logged and recorded, never raised into the job."""
        async def _run() -> None:
            async with self._factory() as session:
                await slice_saver.save_slice_version(
                    session, job_id=job_id, printer_id=printer_id, artifact_path=artifact_path, inputs=inputs,
                    require_flag=True)
        task = asyncio.create_task(_run(), name=f"save-slice-{job_id}")
        self._estimate_tasks.add(task)
        task.add_done_callback(self._estimate_tasks.discard)

    def spawn_estimate(self, job_id: int) -> None:
        """Create and track a background estimate task for job_id."""
        task = asyncio.create_task(
            self.run_estimate(job_id), name=f"estimate-{job_id}"
        )
        self._estimate_tasks.add(task)
        task.add_done_callback(self._estimate_tasks.discard)

    async def run_estimate(self, job_id: int) -> None:
        """Background test slice to populate estimate_* fields on the Job row.

        Wraps _do_run_estimate so an unexpected exception anywhere in the estimate
        pipeline still marks the estimate failed instead of leaving it "pending"
        forever — spawn_estimate's done-callback only discards the task and never
        retrieves the exception, so without this the UI spinner would never resolve.
        """
        try:
            await self._do_run_estimate(job_id)
        except Exception as exc:
            logger.exception("Unexpected error in run_estimate for job %s", job_id)
            async with self._factory() as session:
                job = await session.get(Job, job_id)
                token = job.estimate_token if job else None
            await self._fail_estimate(job_id, token, f"Unexpected error: {exc}")

    async def _do_run_estimate(self, job_id: int) -> None:
        import json as _json

        # Step 1 — Load job and resolve config
        token: int | None = None
        machine_preset: str | None = None
        stored_path: str | None = None
        filament_profiles: list[str] = []
        print_profile: str | None = None
        printer_name: str | None = None
        preset_label: dict | None = None

        async with self._factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status in ("cancelled", "complete", "failed"):
                return
            if job.estimate_status != "pending":
                return
            token = job.estimate_token

            # Load the first JobPrinterConfig (lowest id)
            cfg_result = await session.execute(
                select(JobPrinterConfig)
                .where(JobPrinterConfig.job_id == job_id)
                .order_by(JobPrinterConfig.id.asc())
                .limit(1)
            )
            config = cfg_result.scalar_one_or_none()
            # No config or no matching printer: fall through to Step 2 below, which
            # already fails the estimate — machine_preset/filament_profiles stay at
            # their empty defaults so that check catches it. (Previously this
            # returned here directly, skipping _fail_estimate and leaving the
            # estimate stuck on "pending" forever.)
            printer = await session.get(Printer, config.printer_id) if config is not None else None
            uploaded_file = await session.get(UploadedFile, job.uploaded_file_id)

            # Capture all scalars before session closes
            machine_preset = (printer.current_orca_printer_profile or "") if printer else ""
            print_profile = (config.print_profile or "") if config else ""
            stored_path = (
                str(library_abs_path(get_library_dir(), uploaded_file.relative_path))
                if uploaded_file else None
            )
            printer_name = printer.name if printer else None
            loaded = (printer.loaded_filaments or []) if printer else []

            # Resolve filament profiles
            fmap = config.filament_map if config else None
            if fmap:
                for entry in sorted(fmap, key=lambda e: e.get("tool_index", 0) or 0):
                    ti = entry.get("tool_index")
                    ep = entry.get("filament_profile")
                    if ep:
                        filament_profiles.append(ep)
                    elif ti is not None and ti < len(loaded):
                        filament_profiles.append(loaded[ti].get("filament_profile", ""))
            elif config is not None:
                slot = _slot_for_config(config, loaded)
                fp = config.filament_profile or (slot.get("filament_profile") if slot else None)
                if fp:
                    filament_profiles.append(fp)

            preset_label = {
                "printer_name": printer_name,
                "machine_profile": machine_preset,
                "process_profile": print_profile,
                "filament_profiles": filament_profiles,
            }

        # Step 2 — Pre-flight validation
        if not machine_preset or not stored_path or not filament_profiles:
            await self._fail_estimate(job_id, token, "missing machine preset, file, or filament profile")
            return

        # Step 3 — Enqueue slice
        output_dir = self._slicer._data_dir / "gcode_estimates" / str(job_id)
        req = SliceRequest(
            job_id=job_id,
            source_3mf=stored_path,
            plate_number=1,
            machine_preset=machine_preset,
            process_preset=print_profile,
            filament_presets=filament_profiles,
            filament_colours=[],
            export_args=[],
            prepare_hook=None,
        )

        fut: asyncio.Future = asyncio.get_running_loop().create_future()

        async def _do_estimate_slice():
            try:
                async with self._factory() as s:
                    j = await s.get(Job, job_id)
                    if j is None or j.status in ("cancelled", "complete", "failed"):
                        if not fut.cancelled():
                            fut.cancel()
                        return
                    if j.estimate_status != "pending":
                        if not fut.cancelled():
                            fut.cancel()
                        return
                result = await asyncio.to_thread(self._slicer.slice, req, output_dir)
                if not fut.done():
                    fut.set_result(result)
            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)

        await self._slice_queue.put((1, next(self._slice_seq), _do_estimate_slice()))

        try:
            gcode_path = await fut
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("Estimate slice failed for job %s: %s", job_id, exc)
            await self._fail_estimate(job_id, token, str(exc))
            return

        # Step 4 — Parse, discard gcode, write results
        grams, secs, extruder_grams = get_format_provider().parse_estimates(gcode_path)
        shutil.rmtree(output_dir, ignore_errors=True)

        breakdown = None
        if extruder_grams is not None:
            breakdown = [
                {
                    "extruder_index": i,
                    "filament_profile": filament_profiles[i] if i < len(filament_profiles) else None,
                    "grams": g,
                }
                for i, g in enumerate(extruder_grams)
            ]

        from sqlalchemy import text as _text
        async with self._factory() as session:
            result = await session.execute(
                _text(
                    "UPDATE jobs SET estimate_status='done', estimate_seconds=:secs, "
                    "estimate_filament_grams=:grams, estimate_filament_breakdown=:bd, "
                    "estimate_preset_label=:label, updated_at=:now "
                    "WHERE id=:id AND estimate_status='pending' AND estimate_token=:token"
                ),
                {
                    "secs": secs,
                    "grams": grams,
                    "bd": _json.dumps(breakdown),
                    "label": _json.dumps(preset_label),
                    "now": _now(),
                    "id": job_id,
                    "token": token,
                }
            )
            if result.rowcount == 0:
                return  # cancelled or retriggered — discard
            await session.commit()

        await self._broadcast_job(job_id)

    async def _fail_estimate(self, job_id: int, token: int, reason: str) -> None:
        from sqlalchemy import text as _text
        async with self._factory() as session:
            result = await session.execute(
                _text(
                    "UPDATE jobs SET estimate_status='failed', updated_at=:now "
                    "WHERE id=:id AND estimate_status='pending' AND estimate_token=:token"
                ),
                {"now": _now(), "id": job_id, "token": token}
            )
            if result.rowcount > 0:
                await session.commit()
        logger.warning("Estimate failed for job %s: %s", job_id, reason)
        await self._broadcast_job(job_id)

    async def run_verify_slice(self, req: SliceRequest, output_dir: Path) -> str:
        """Test-slice through the same serialized _slice_queue as production and
        estimate slices, at the lowest priority. Routing it through self._executor
        directly (the old behaviour) let a debug-only test-slice — which can block
        for up to poll_status's ~620s timeout — hold one of only 4 threads that
        _do_upload_and_print also depends on for upload_file/start_print, so
        finished jobs could sit in "uploading" for minutes behind a verify-slice."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        async def _do_verify_slice():
            try:
                result = await asyncio.to_thread(self._slicer.slice, req, output_dir)
                if not fut.done():
                    fut.set_result(result)
            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)

        await self._slice_queue.put((2, next(self._slice_seq), _do_verify_slice()))
        return await fut

    def wake(self) -> None:
        self._event.set()

    async def start(self) -> None:
        # Sweep stale estimate gcode from a prior run so we don't serve stale data.
        estimate_gcode_dir = self._slicer._data_dir / "gcode_estimates"
        shutil.rmtree(estimate_gcode_dir, ignore_errors=True)

        # slicing and uploading are non-resumable transient states — reset them to
        # queued immediately so they re-enter the queue on this boot.
        async with self._factory() as session:
            result = await session.execute(
                select(Job).where(Job.status.in_(["slicing", "uploading"]))
            )
            orphans = result.scalars().all()
            for job in orphans:
                job.status = "queued"
                job.assigned_printer_id = None
            if orphans:
                await session.commit()
                for job in orphans:
                    if self._broadcast_cb:
                        await self._broadcast_cb("job_updated", {"job_id": job.id})

        # "sliced" jobs park gcode on disk between queue cycles; re-queue only if
        # the file has been deleted (e.g. data volume wiped between restarts).
        async with self._factory() as session:
            sliced_result = await session.execute(
                select(Job, GcodeFile)
                .join(GcodeFile, and_(GcodeFile.job_id == Job.id))
                .where(Job.status == "sliced")
            )
            stale = [(j, g) for j, g in sliced_result.all() if not os.path.exists(g.path)]
            for job, gcode in stale:
                await session.delete(gcode)
                job.status = "queued"
                job.assigned_printer_id = None
                job.updated_at = _now()
            if stale:
                await session.commit()
                for job, _ in stale:
                    if self._broadcast_cb:
                        await self._broadcast_cb("job_updated", {"job_id": job.id})

        # Reset any estimate_status='pending' left from a prior unclean shutdown.
        from sqlalchemy import text as _text
        async with self._factory() as session:
            await session.execute(
                _text("UPDATE jobs SET estimate_status=NULL WHERE estimate_status='pending'")
            )
            await session.commit()

        self._slice_worker_task = asyncio.create_task(
            self._slice_worker(), name="slice_worker"
        )
        self._task = asyncio.create_task(self._loop(), name="queue_engine")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._slice_worker_task:
            self._slice_worker_task.cancel()
            await asyncio.gather(self._slice_worker_task, return_exceptions=True)
        for t in list(self._estimate_tasks):
            t.cancel()
        if self._estimate_tasks:
            await asyncio.gather(*self._estimate_tasks, return_exceptions=True)
        self._executor.shutdown(wait=False)

    async def _loop(self) -> None:
        while True:
            self._event.clear()
            try:
                await self._process_queue()
            except Exception:
                logger.exception("Queue engine error in _process_queue")
            # Wake on an explicit event (new job, print complete, ...) OR after the
            # configurable check interval, whichever comes first.
            interval = await self._check_interval_seconds()
            interval = min(interval, await self._seconds_until_next_schedule())
            try:
                await asyncio.wait_for(self._event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass  # periodic availability re-check

    async def _seconds_until_next_schedule(self) -> float:
        """Seconds until the earliest future `not_before` of a waiting job (inf if none), so a scheduled job
        starts on time instead of at the next periodic check. Floor of 1s avoids a hot loop."""
        try:
            async with self._factory() as session:
                nxt = (await session.execute(
                    select(func.min(Job.not_before)).where(
                        Job.status.in_(["queued", "blocked", "sliced"]), Job.not_before > _now())
                )).scalar()
            if nxt:
                delta = (datetime.fromisoformat(nxt) - datetime.now(timezone.utc)).total_seconds()
                return max(1.0, delta)
        except Exception:
            logger.exception("Failed to read next scheduled start")
        return float("inf")

    async def _check_interval_seconds(self) -> float:
        minutes = _DEFAULT_CHECK_MINUTES
        try:
            async with self._factory() as session:
                cfg = await session.get(QueueConfig, 1)
                if cfg is not None:
                    minutes = cfg.check_interval_minutes
        except Exception:
            logger.exception("Failed to read queue check interval; using default")
        return max(1, minutes) * 60.0

    async def _process_queue(self) -> None:
        await self._reconcile_printing_jobs()
        all_ids = sorted(self._mgr.get_all_printer_ids())
        ready_set = {pid for pid in all_ids if self._mgr.is_printer_ready(pid)}
        # Ready printers: resume pre-sliced gcode if available, else claim and slice.
        # Each printer is isolated in its own try/except — a poisoned job (e.g.
        # malformed stored data) must not stall printers processed after it.
        for printer_id in sorted(ready_set):
            try:
                async with self._factory() as session:
                    printer = await session.get(Printer, printer_id)
                    if printer is not None and scheduling.in_quiet_hours(printer.quiet_start, printer.quiet_end):
                        continue  # quiet hours: don't start anything new on this printer
                    if not await self._try_resume_sliced_job(session, printer_id):
                        await self._try_claim_for_printer(session, printer_id)
            except Exception:
                logger.exception("Queue engine error processing printer %s", printer_id)
        # Offline printers: run the slice step now so gcode is ready when they come online.
        for printer_id in sorted(pid for pid in all_ids if pid not in ready_set):
            try:
                async with self._factory() as session:
                    if not await self._has_pending_sliced_job(session, printer_id):
                        await self._try_claim_for_printer(session, printer_id, slice_only=True)
            except Exception:
                logger.exception("Queue engine error processing printer %s", printer_id)

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
                logger.exception("Reconcile: could not get state for printer %s", printer_id)
                continue

            if ended_in_failure:
                # Elegoo/Snapmaker physical cancel: normalized state is FAILED.
                # Mark the job failed so the user can adjust settings before re-queueing.
                async with self._factory() as session:
                    job = await session.get(Job, job_id)
                    if job is None or job.status != "printing":
                        continue  # already resolved by the normal callback
                    job.status = "failed"
                    job.completed_at = _now()
                    job.block_reason = "print cancelled or ended with failure on the printer"
                    job.assigned_printer_id = None
                    job.updated_at = _now()
                    job.deduction_skipped = True
                    gcode_result = await session.execute(
                        select(GcodeFile).where(
                            GcodeFile.job_id == job_id,
                            GcodeFile.printer_id == printer_id,
                        )
                    )
                    gcode = gcode_result.scalar_one_or_none()
                    if gcode:
                        try:
                            os.remove(gcode.path)
                        except OSError:
                            pass
                        await session.delete(gcode)
                    await session.commit()
                logger.warning(
                    "Reconcile: job %s → failed (printer %s idle with FAILED state)",
                    job_id, printer_id,
                )
                await self._broadcast_job(job_id)
            else:
                # Normal completion (or Bambu cancel, which is indistinguishable from
                # a successful finish at the firmware level). Delegate to the same
                # handle_print_complete path used by the normal callback so gcode cleanup
                # and broadcasting are consistent.
                logger.info(
                    "Reconcile: job %s → complete (printer %s idle)", job_id, printer_id,
                )
                await self.handle_print_complete(printer_id)

    async def _try_claim_for_printer(self, session: AsyncSession, printer_id: int, slice_only: bool = False) -> None:
        printer = await session.get(Printer, printer_id)
        if printer is None or not printer.queue_on:
            return

        # "Any printer of this model" jobs: keep this printer's materialized configs current before looking.
        await model_targets.sync_targets_for_printer(session, printer)
        await session.commit()

        # The FIRST queue item that lists this printer as compatible — head of line.
        # Blocked jobs are re-evaluated (loading the right filament unblocks them).
        stmt = (
            select(Job, JobPrinterConfig)
            .join(
                JobPrinterConfig,
                and_(JobPrinterConfig.job_id == Job.id, JobPrinterConfig.printer_id == printer_id),
            )
            # Jobs of a project only become claimable once the project is promoted to "queued".
            .outerjoin(Project, Project.id == Job.project_id)
            .where(Job.status.in_(["queued", "blocked"]))
            .where(or_(Job.project_id.is_(None), Project.stage == "queued"))
            # A scheduled job that isn't due yet is skipped (not head-of-line): jobs behind it can still run.
            .where(or_(Job.not_before.is_(None), Job.not_before <= _now()))
            .order_by(Job.queue_position.asc())
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
        if row is None:
            return
        job, config = row
        job_id = job.id

        # If this job can't run on this printer, block it and STOP — do not look
        # further down the queue for this printer (head-of-line blocking).
        if config.slice_failed:
            await self._block_job(session, job, config.slice_error or "slicing failed")
            return
        mismatch = _filament_mismatch(config, printer.loaded_filaments)
        if mismatch:
            await self._block_job(session, job, mismatch)
            return

        # A pre-sliced file only goes to a printer that prints that format as-is (create/PATCH and target sync already
        # refuse the rest; this catches configs that predate that check). Block, never fail — failed is terminal.
        source_file = await session.get(UploadedFile, job.uploaded_file_id)
        if source_file is not None and not model_targets.accepts_file(printer.printer_type, source_file.original_filename):
            await self._block_job(session, job, f"{printer.name} can't print this pre-sliced file as-is")
            return
        if source_file is not None and is_presliced_file(source_file):
            # Pre-sliced G-code carries its own machine eligibility (BIZ-263): a printer whose model it is not eligible for
            # (or whose model is unknown) must never get it, even if the config predates the rule. Block, never fail.
            verdict = await gcode_eligibility.evaluate(session, source_file, printer, confirmed=bool(job.eligibility_confirmed))
            if not verdict.allowed:
                await self._block_job(session, job, verdict.message)
                return
            # A cached version was sliced for one make/model: a printer whose machine preset changed since (another
            # nozzle, say) must not print it.
            version = (await session.execute(
                select(SlicedVersion).where(SlicedVersion.file_id == source_file.id))).scalar_one_or_none()
            if version is not None and version.machine_preset != printer.current_orca_printer_profile:
                await self._block_job(session, job, f"{printer.name} is no longer a {version.machine_preset} — this "
                                                    "cached version was sliced for that")
                return

        # Pre-flight: ensure Laminus is reachable before claiming this job.
        # Block (not fail) so the job auto-retries when Laminus comes back.
        # Pre-sliced gcode jobs never touch the slicer, so they don't need it.
        # With Laminus down, a job that allows cached slices may still go to THIS printer when a usable cached version
        # matches this printer's slice of it (BIZ-201) — dispatch then prints that version and never slices.
        cache_only_version: int | None = None
        if not is_presliced_file(source_file):
            down = await self._laminus_down_reason()
            if down:
                if job.allow_cached_slice:
                    cache_only_version = await self._cached_version_for_claim(session, job, config, printer, source_file)
                if cache_only_version is None:
                    await self._block_job(session, job, down)
                    return

        # Claim → slice. Conditional UPDATE guards against a status change (e.g. a
        # user cancel) that committed while we awaited the Laminus health probe above.
        plate_number = job.plate_number
        result = await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status.in_(["queued", "blocked"]))
            .values(status="slicing", assigned_printer_id=printer_id, block_reason=None, updated_at=_now())
        )
        if result.rowcount == 0:
            return  # no longer claimable — bail without starting the slice/print
        await session.commit()

        asyncio.create_task(
            self._run_slice_and_print(job_id, printer_id, plate_number, slice_only=slice_only,
                                      cache_only_version=cache_only_version),
            name=f"slice-{job_id}-{printer_id}",
        )
        await self._broadcast_job(job_id)

    async def _laminus_down_reason(self) -> str | None:
        """Why slicing can't run right now (the job's block reason), or None when Laminus is up."""
        slicing = get_slicing_provider()
        if slicing is None:
            return "Laminus sidecar not configured — slicing paused"
        try:
            await asyncio.to_thread(slicing.health, 2)
        except SlicingProviderNotReady:
            return "Laminus is not ready — slicing paused"
        except Exception:
            return "Laminus is unreachable — slicing paused"
        return None

    async def _cached_version_for_claim(
        self, session: AsyncSession, job: Job, config: JobPrinterConfig, printer: Printer, source_file: UploadedFile,
    ) -> int | None:
        """The usable cached version matching `printer`'s slice of `job` — the key dispatch would look up — or None.
        Only asked while Laminus is down, so staleness can't be checked (unknown counts as usable, as at dispatch).
        Never raises: any doubt means no version, and the job waits for Laminus as before."""
        try:
            if source_file is None or not printer.current_orca_printer_profile:
                return None
            if await refresh_content_hash(source_file, get_library_dir()):   # the key hashes the model's bytes
                await session.commit()
            try:
                req, fmap = self._slice_request(job.id, printer.id, job.plate_number,
                                                _slice_params(job, config, printer, source_file))
            except ValueError:   # a mapped filament isn't loaded: dispatch couldn't build this slice either
                return None
            inputs = slice_cache.key_inputs(req, source_file.content_hash, config.tool_index, fmap)
            if inputs is None:
                return None
            key = slice_cache.cache_key(inputs)
            found, _ = await self._usable_version(source_file.id, key)
            if found is None:
                return None
            version, cached_file = found
            # Same staleness rule as dispatch: a version dispatch would reslice can't carry the claim (it would only be
            # released again, every cycle). Usually unknown with Laminus down, which counts as usable.
            cfg = await session.get(QueueConfig, 1)
            use_latest = True if cfg is None else bool(cfg.slice_cache_use_latest_settings)
            current = await asyncio.to_thread(
                slice_cache.cached_fingerprint, inputs.machine_preset, inputs.process_preset,
                list(inputs.filament_presets), get_slicing_provider())
            stale, _ = slice_cache.staleness(version.preset_content_hash, version.slicer_version, current)
            if stale and use_latest:
                return None
            slice_cache.log_event("gate_bypass", job_id=job.id, printer_id=printer.id, source_file_id=source_file.id,
                                  cache_key=key, sliced_version_id=version.id, cached_file_id=cached_file.id)
            return version.id
        except Exception:
            logger.exception("slicing-cache claim check failed for job %s on printer %s", job.id, printer.id)
            return None

    async def _usable_version(
        self, source_file_id: int, key: str, only_version_id: int | None = None,
    ) -> tuple[tuple[SlicedVersion, UploadedFile] | None, bool]:
        """(newest version for `key` whose library file is present with the bytes that were saved, any rows at all)."""
        async with self._factory() as session:
            stmt = (select(SlicedVersion, UploadedFile)
                    .join(UploadedFile, UploadedFile.id == SlicedVersion.file_id)
                    .where(SlicedVersion.source_file_id == source_file_id, SlicedVersion.cache_key == key)
                    .order_by(SlicedVersion.id.desc()))
            if only_version_id is not None:
                stmt = stmt.where(SlicedVersion.id == only_version_id)
            rows = (await session.execute(stmt)).all()
        library = get_library_dir()
        for v, f in rows:
            if f.missing:
                continue
            fresh = await asyncio.to_thread(
                fresh_content_hash, library_abs_path(library, f.relative_path), f.content_hash, f.size_bytes, f.mtime)
            if fresh is not None and fresh[0] == f.content_hash:
                return (v, f), True
        return None, bool(rows)

    async def _block_job(self, session: AsyncSession, job: Job, reason: str) -> None:
        job_id = job.id
        already = job.status == "blocked" and job.block_reason == reason
        # Conditional UPDATE, mirroring the claim guard above: some call sites reach
        # here after an await (the Laminus health probe), so a concurrent status
        # change (e.g. a user cancel) may have committed in the gap. Only overwrite
        # if the job is still in a blockable state.
        result = await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status.in_(["queued", "blocked"]))
            .values(status="blocked", block_reason=reason, assigned_printer_id=None, updated_at=_now())
        )
        if result.rowcount == 0:
            return  # no longer blockable — don't resurrect a cancelled job
        await session.commit()
        if not already:  # avoid broadcast spam when re-blocking with the same reason
            await self._broadcast_job(job_id)

    async def _run_slice_and_print(self, job_id: int, printer_id: int, plate_number: int, slice_only: bool = False,
                                   cache_only_version: int | None = None) -> None:
        # cache_only_version: the claim passed with Laminus down because this cached version matched (BIZ-201) — print
        # exactly it, and never fall through to a slice that can't run.
        # Load job details for slicing
        async with self._factory() as session:
            uploaded_file = None
            config = None
            job = await session.get(Job, job_id)
            if job is not None:
                uploaded_file = await session.get(UploadedFile, job.uploaded_file_id)
            result = await session.execute(
                select(JobPrinterConfig).where(
                    JobPrinterConfig.job_id == job_id,
                    JobPrinterConfig.printer_id == printer_id,
                    JobPrinterConfig.slice_failed == False,  # noqa: E712
                )
            )
            config = result.scalar_one_or_none()
            printer = await session.get(Printer, printer_id)
            # Capture scalar values before session closes
            loaded = (printer.loaded_filaments if printer else None) or []
            slot = _slot_for_config(config, loaded) if config else None
            cfg_tool_index = config.tool_index if config else None
            # AMS printers (Bambu) map the print's filament to the matched tray.
            ams_tray_id = (slot or {}).get("ams_tray_id")
            stored_path = (
                str(library_abs_path(get_library_dir(), uploaded_file.relative_path))
                if uploaded_file else None
            )
            original_filename = uploaded_file.original_filename if uploaded_file else None
            machine_preset = printer.current_orca_printer_profile if printer else None
            params = _slice_params(job, config, printer, uploaded_file)
            is_gcode = is_presliced_file(uploaded_file)
            allow_cached = bool(job.allow_cached_slice) if job else False
            if uploaded_file is not None and not is_gcode and (allow_cached or (job is not None and job.save_slice)):
                # The slicing cache keys on the model's bytes: never trust an index entry the file has outgrown.
                if await refresh_content_hash(uploaded_file, get_library_dir()):
                    await session.commit()
            source_content_hash = uploaded_file.content_hash if uploaded_file else ""
            source_file_id = uploaded_file.id if uploaded_file else None

        if config is None or uploaded_file is None:
            await self._fail_job_post_slice(job_id, printer_id)
            return
        slice_presets: list = []
        slice_inputs: slice_cache.CacheKeyInputs | None = None   # what this slice was made from (slicing cache)
        cache_hit = False   # printing a cached version instead of a fresh slice
        if is_gcode:
            # Pre-sliced upload: nothing to slice. Stage a private copy (finished jobs delete their gcode file,
            # which must never be the library's own copy) and carry on to upload + print.
            try:
                gcode_path = await asyncio.to_thread(
                    self._stage_gcode, job_id, stored_path, original_filename or "model.gcode"
                )
            except OSError as exc:
                await self._handle_slice_failure(job_id, printer_id, f"could not read the uploaded gcode: {exc}")
                return
        else:
            if not machine_preset:
                await self._handle_slice_failure(
                    job_id, printer_id, "printer has no OrcaSlicer machine preset selected"
                )
                return

            try:
                req, cfg_filament_map = self._slice_request(job_id, printer_id, plate_number, params)
            except ValueError as exc:   # a catalog filament in the map isn't loaded
                await self._handle_slice_failure(job_id, printer_id, f"printer {printer_id}: {exc}")
                return
            loop = asyncio.get_running_loop()
            # Slicing cache (BIZ-193): print a matching cached version instead of slicing, when the job allows it.
            # cfg_filament_map is the resolved (slot-index) map here — exactly what the 3MF remap would use.
            slice_inputs = slice_cache.key_inputs(req, source_content_hash, cfg_tool_index, cfg_filament_map)
            cached = await self._use_cached_slice(job_id, printer_id, source_file_id, slice_inputs, allow_cached,
                                                  only_version_id=cache_only_version)
            down = None
            if cached is None and cache_only_version is not None:
                down = await self._laminus_down_reason()   # back up since the claim? then just slice as usual
            if cached is not None:
                gcode_path = cached
                cache_hit = True
            elif down:
                await self._release_cache_only_claim(job_id, printer_id, down)
                return
            else:
                fut: asyncio.Future = loop.create_future()

                async def _do_prod_slice():
                    try:
                        # Skip the slice if the job was cancelled while waiting in the queue.
                        async with self._factory() as s:
                            j = await s.get(Job, job_id)
                            if j is None or j.status == "cancelled":
                                if not fut.cancelled():
                                    fut.cancel()
                                return
                        result = await asyncio.to_thread(self._slicer.slice, req)
                        if not fut.cancelled():
                            fut.set_result(result)
                    except Exception as exc:
                        if not fut.cancelled():
                            fut.set_exception(exc)

                await self._slice_queue.put((0, next(self._slice_seq), _do_prod_slice()))
                try:
                    gcode_path = await fut
                except asyncio.CancelledError:
                    fut.cancel()
                    raise
                except SliceError as exc:
                    await self._handle_slice_failure(job_id, printer_id, str(exc))
                    return
                except Exception as exc:
                    logger.exception("Unexpected slice error for job %s on printer %s", job_id, printer_id)
                    await self._handle_slice_failure(job_id, printer_id, f"Unexpected error: {exc}")
                    return

            slice_presets = req.filament_presets

        # Store gcode record; park as "sliced" if the printer isn't ready to receive.
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status == "cancelled":
                # No GcodeFile row will exist to reference this artifact — nothing
                # else will ever clean it up, so remove it here.
                try:
                    os.remove(gcode_path)
                except OSError:
                    pass
                return
            grams, secs, extruder_grams = get_format_provider().parse_estimates(gcode_path, plate_number)
            gcode_rec = GcodeFile(
                job_id=job_id, printer_id=printer_id, path=gcode_path,
                filament_grams=grams, estimated_seconds=secs,
                slice_inputs=slice_inputs.as_dict() if slice_inputs else None,
            )
            want_save = bool(job.save_slice) and not is_gcode and not cache_hit
            session.add(gcode_rec)
            # Persist actuals on Job NOW — before GcodeFile is ever deleted.
            job.actual_filament_grams = grams
            job.actual_seconds = secs
            if extruder_grams is not None:
                job.actual_filament_breakdown = [
                    {
                        "extruder_index": i,
                        "filament_profile": slice_presets[i] if i < len(slice_presets) else None,
                        "grams": g,
                    }
                    for i, g in enumerate(extruder_grams)
                ]
            if slice_only or not self._mgr.is_printer_ready(printer_id):
                job.status = "sliced"
                job.assigned_printer_id = None
                job.updated_at = _now()
                await session.commit()
                if want_save:
                    self.spawn_save_slice(job_id, printer_id, gcode_path, slice_inputs)
                await self._broadcast_job(job_id)
                self.wake()
                return
            job.status = "uploading"
            job.updated_at = _now()
            await session.commit()
        if want_save:
            self.spawn_save_slice(job_id, printer_id, gcode_path, slice_inputs)

        await self._broadcast_job(job_id)
        await self._do_upload_and_print(job_id, printer_id, gcode_path, plate_number, ams_tray_id)

    def _slice_request(self, job_id: int, printer_id: int, plate_number: int,
                       p: dict) -> tuple[SliceRequest, list | None]:
        """The production SliceRequest for `p` (see `_slice_params`) plus the RESOLVED filament map (slot indices —
        part of the cache key). Raises ValueError when a catalog filament in the map isn't loaded."""
        # Meaningful, unique artifact name; the printer decides its output format.
        stem = os.path.splitext(os.path.basename(p["original_filename"] or "model"))[0]
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "model"
        file_base = f"{safe}_p{plate_number}_j{job_id}"
        client = self._mgr.get_client(printer_id)
        export_args = client.orca_export_args(file_base) if client else []

        # Resolve any catalog filament entries to slot indices before slicing.
        loaded = p["loaded"]
        tool_index = p["tool_index"]
        filament_map = _resolve_filament_map(p["filament_map"], loaded) if p["filament_map"] else p["filament_map"]

        prepare_hook = tool_mapping_hook(client, tool_index, filament_map, get_slicing_provider())

        multi_presets: list = []
        if filament_map:
            ordered = sorted(loaded or [], key=lambda s: s.get("slot", 0))
            multi_presets = [s.get("filament_profile") for s in ordered if s.get("filament_profile")]
        plate_config = {"curr_bed_type": p["build_plate_type"]} if p["build_plate_type"] else {}
        plate_config.update(p["overrides"])  # job-level overrides win over printer default
        filament_profile, filament_color = p["filament_profile"], p["filament_color"]
        req = SliceRequest(
            job_id=job_id,
            source_3mf=p["stored_path"],
            plate_number=plate_number,
            machine_preset=p["machine_preset"],
            process_preset=p["print_profile"],
            filament_presets=multi_presets if filament_map else ([filament_profile] if filament_profile else []),
            filament_colours=[filament_color] if filament_color else [],
            export_args=export_args,
            prepare_hook=prepare_hook,
            extra_config=plate_config,
        )
        return req, filament_map

    async def _use_cached_slice(
        self, job_id: int, printer_id: int, source_file_id: int | None,
        inputs: "slice_cache.CacheKeyInputs | None", allow: bool, only_version_id: int | None = None,
    ) -> str | None:
        """When the job allows it, find a cached version matching this exact slice and stage a private copy of it to
        print instead of slicing. Returns the staged path, or None to slice as usual. Every decision is logged and
        recorded on the job (slice_cache_info); nothing here can fail or block the job."""
        if not allow:
            slice_cache.log_event("miss", job_id=job_id, printer_id=printer_id, reason="cache_disabled")
            async with self._factory() as session:   # it slices fresh: drop any version an earlier dispatch printed
                await session.execute(update(Job).where(Job.id == job_id, Job.sliced_version_id.is_not(None))
                                      .values(sliced_version_id=None))
                await session.commit()
            return None
        key = slice_cache.cache_key(inputs) if inputs else None
        base = {"job_id": job_id, "printer_id": printer_id, "source_file_id": source_file_id, "cache_key": key,
                **(slice_cache.key_fields(inputs) if inputs else {})}
        slice_cache.log_event("lookup", **base)
        try:
            return await self._try_cached_slice(job_id, inputs, key, source_file_id, base, only_version_id)
        except Exception as exc:   # a lookup bug must never cost the job its print: slice as usual
            logger.exception("slicing-cache lookup failed for job %s", job_id)
            await self._record_cache_decision(job_id, slice_cache.decision_info(
                "miss", reason="lookup_error", cache_key=key, inputs=inputs))
            slice_cache.log_event("miss", **base, reason="lookup_error", error=f"{type(exc).__name__}: {exc}")
            return None

    async def _try_cached_slice(self, job_id, inputs, key, source_file_id, base, only_version_id=None) -> str | None:
        gate = "laminus_down" if only_version_id is not None else None
        if gate:
            base = {**base, "gate": gate}
        if inputs is None or source_file_id is None:
            await self._record_cache_decision(job_id, slice_cache.decision_info(
                "miss", reason="uncacheable", cache_key=None, inputs=None, gate=gate))
            slice_cache.log_event("miss", **base, reason="uncacheable")
            return None
        async with self._factory() as session:
            cfg = await session.get(QueueConfig, 1)
            use_latest = True if cfg is None else bool(cfg.slice_cache_use_latest_settings)
        found, had_rows = await self._usable_version(source_file_id, key, only_version_id)
        library = get_library_dir()
        if found is None:
            reason = "file_missing" if had_rows else "no_version"
            await self._record_cache_decision(job_id, slice_cache.decision_info(
                "miss", reason=reason, cache_key=key, inputs=inputs, policy=slice_cache.policy_name(use_latest),
                gate=gate))
            slice_cache.log_event("miss", **base, reason=reason)
            return None
        version, cached_file = found
        current = await asyncio.to_thread(
            slice_cache.cached_fingerprint, inputs.machine_preset, inputs.process_preset,
            list(inputs.filament_presets), get_slicing_provider())
        stale, reasons = slice_cache.staleness(version.preset_content_hash, version.slicer_version, current)
        policy = slice_cache.policy_name(use_latest)
        detail = {"sliced_version_id": version.id, "cached_file_id": cached_file.id,
                  "cached_file_hash": cached_file.content_hash,
                  "preset_content_hash_stored": version.preset_content_hash,
                  "preset_content_hash_current": current.preset_content_hash,
                  "slicer_version_stored": version.slicer_version, "slicer_version_current": current.slicer_version,
                  "stale": "unknown" if stale is None else stale, "stale_reasons": reasons, "policy": policy}
        info_kw = dict(cache_key=key, inputs=inputs, version=version, cached_file_hash=cached_file.content_hash,
                       current=current, stale=stale, stale_reasons=reasons, policy=policy,
                       gate=gate)
        if stale and use_latest:
            await self._record_cache_decision(job_id, slice_cache.decision_info(
                "miss", reason="stale_resliced", **info_kw))
            slice_cache.log_event("miss", **base, reason="stale_resliced", **detail)
            return None
        try:
            staged = await asyncio.to_thread(
                self._stage_gcode, job_id, str(library_abs_path(library, cached_file.relative_path)),
                cached_file.original_filename)
        except OSError as exc:
            await self._record_cache_decision(job_id, slice_cache.decision_info(
                "miss", reason="file_missing", **info_kw))
            slice_cache.log_event("miss", **base, reason="file_missing", error=str(exc), **detail)
            return None
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            if job is not None:
                job.sliced_version_id = version.id
                job.slice_cache_info = slice_cache.decision_info(
                    "hit", previous=job.slice_cache_info, **info_kw)
                await session.commit()
        slice_cache.log_event("hit_slice_skipped", **base, **detail)
        return staged

    async def _release_cache_only_claim(self, job_id: int, printer_id: int, reason: str) -> None:
        """A claim made on a cached version while Laminus was down (BIZ-201) found it gone by dispatch: give the job
        back as if the health gate had failed — blocked, printer released, config NOT marked slice_failed — so it
        retries on its own when Laminus returns or another printer has a version."""
        async with self._factory() as session:
            result = await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "slicing", Job.assigned_printer_id == printer_id)
                .values(status="blocked", block_reason=reason, assigned_printer_id=None, updated_at=_now())
            )
            await session.commit()
        slice_cache.log_event("gate_bypass_released", job_id=job_id, printer_id=printer_id, reason=reason)
        if result.rowcount:
            await self._broadcast_job(job_id)

    async def _record_cache_decision(self, job_id: int, info: dict) -> None:
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            if job is not None:
                if info.get("decision") == "miss":
                    job.sliced_version_id = None   # an earlier hit no longer describes what this job prints
                if job.slice_cache_info and job.slice_cache_info.get("save"):
                    info["save"] = job.slice_cache_info["save"]
                job.slice_cache_info = info
                await session.commit()

    def _stage_gcode(self, job_id: int, source_path: str, original_filename: str) -> str:
        """Copy a pre-sliced upload (.gcode or .gcode.3mf) into the job's gcode dir (where slicer output would have
        gone), keeping its full extension — the printer tells the two apart by it."""
        out_dir = self._slicer._data_dir / "gcode" / str(job_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = presliced_suffix(original_filename)
        base = os.path.basename(original_filename)
        stem = base[: -len(suffix)] if base.lower().endswith(suffix) else os.path.splitext(base)[0]
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "model"
        dest = out_dir / f"{safe}_j{job_id}{suffix}"
        shutil.copyfile(source_path, dest)
        return str(dest)

    async def _handle_slice_failure(self, job_id: int, printer_id: int, error: str) -> None:
        # A slicing issue blocks the job (per queue policy). This printer's config
        # is marked failed so it won't retry; another compatible printer can still
        # rescue it on a later check (its config isn't failed, so it re-evaluates).
        async with self._factory() as session:
            result = await session.execute(
                select(JobPrinterConfig).where(
                    JobPrinterConfig.job_id == job_id,
                    JobPrinterConfig.printer_id == printer_id,
                )
            )
            config = result.scalar_one_or_none()
            if config:
                config.slice_failed = True
                config.slice_error = error

            job = await session.get(Job, job_id)
            if job:
                job.status = "blocked"
                job.block_reason = f"slicing failed: {error}"
                job.assigned_printer_id = None
                job.updated_at = _now()
            await session.commit()

        await self._broadcast_job(job_id)
        await self._fire_webhooks(job_id, "job.blocked")
        await self._fire_notifications(job_id, "job.blocked", printer_id=printer_id, reason=error)

    async def _fail_job_post_slice(self, job_id: int, printer_id: int, reason: str | None = None) -> None:
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            if job:
                job.status = "failed"
                job.completed_at = _now()
                job.block_reason = reason
                job.assigned_printer_id = None
                job.updated_at = _now()
            # Clean up gcode file from disk and DB
            gcode_result = await session.execute(
                select(GcodeFile).where(
                    GcodeFile.job_id == job_id,
                    GcodeFile.printer_id == printer_id,
                )
            )
            gcode = gcode_result.scalar_one_or_none()
            if gcode:
                try:
                    os.remove(gcode.path)
                except OSError:
                    pass
                await session.delete(gcode)
            await session.commit()
        await self._broadcast_job(job_id)
        await self._fire_webhooks(job_id, "job.failed")
        await self._fire_notifications(job_id, "job.failed", printer_id=printer_id, reason=reason)

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
            logger.warning("Sliced gcode missing for job %s (printer %s); re-queuing", job.id, printer_id)
            await session.delete(gcode)
            job.status = "queued"
            job.assigned_printer_id = None
            job.updated_at = _now()
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
        loaded = (printer.loaded_filaments if printer else None) or []
        slot = _slot_for_config(config, loaded) if config else None
        ams_tray_id = (slot or {}).get("ams_tray_id")
        gcode_path = gcode.path
        plate_number = job.plate_number

        # Conditional UPDATE guards against a status change (e.g. a user cancel) that
        # committed during the DB round-trips above (config/printer lookups).
        result = await session.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "sliced")
            .values(status="uploading", assigned_printer_id=printer_id, printed_on_printer_id=printer_id,
                    updated_at=_now())
        )
        if result.rowcount == 0:
            return False  # no longer claimable
        await session.commit()
        asyncio.create_task(
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
                logger.exception("Gcode upload failed for job %s on printer %s", job_id, printer_id)
                upload_ok = False
                upload_error_msg = f"Gcode upload failed: {e}"
            if not upload_ok:
                logger.warning("Upload of %s to printer %s reported failure for job %s",
                               gcode_filename, printer_id, job_id)
                reason = upload_error_msg or "Gcode upload reported failure by printer"
                await self._fail_job_post_slice(job_id, printer_id, reason)
                return

        from .abstract_printer_client import StartPrintOptions
        opts = StartPrintOptions(
            plate_id=plate_number,
            gcode_path=gcode_filename,
            ams_mapping=[ams_tray_id] if ams_tray_id is not None else None,
        )
        start_error_msg = None
        try:
            start_ok = await loop.run_in_executor(
                self._executor, client.start_print, gcode_filename, opts
            )
        except Exception as e:
            logger.exception("start_print failed for job %s on printer %s", job_id, printer_id)
            start_ok = False
            start_error_msg = f"Start print failed: {e}"
        if not start_ok:
            logger.warning("start_print of %s on printer %s reported failure for job %s",
                           gcode_filename, printer_id, job_id)
            reason = start_error_msg or "Start print reported failure by printer"
            await self._fail_job_post_slice(job_id, printer_id, reason)
            return

        async with self._factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status in ("cancelled", "complete"):
                return
            job.status = "printing"
            job.printed_on_printer_id = printer_id
            job.updated_at = _now()
            # The printer has started a physical print: mark it not-ready for new
            # work so it won't auto-claim the next job after this one finishes — the
            # user must explicitly mark it ready (clear the plate) first. Set on
            # start (not just on completion) so a missed completion event can't let
            # it grab another job onto an uncleared plate.
            self._mgr.set_awaiting_plate_clear(printer_id, True)
            printer = await session.get(Printer, printer_id)
            if printer is not None:
                printer.awaiting_plate_clear = True
            snapshot_ref = await self._snapshot_ref(session, job_id, printer_id, printer)
            await session.commit()

        if snapshot_ref is not None:
            # Provider I/O never runs on the queue loop: the starting weight is read in a host task.
            inventory_tasks.spawn(inventory_snapshots.take(self._factory, job_id, printer_id, snapshot_ref),
                                  name=f"inventory-snapshot-{job_id}")
        await self._broadcast_job(job_id)

    async def _snapshot_ref(self, session, job_id: int, printer_id: int, printer) -> str | None:
        """The spool ref to snapshot at print start (DB only), or None when no deduction will apply."""
        if not (inventory_deduction.can_deduct() and await inventory_config.deduct_enabled(session)):
            return None
        config = (await session.execute(select(JobPrinterConfig).where(
            JobPrinterConfig.job_id == job_id, JobPrinterConfig.printer_id == printer_id))).scalar_one_or_none()
        if config is None:
            return None
        slot = _slot_for_config(config, (printer.loaded_filaments if printer else None) or [])
        ref = inventory_refs.slot_spool_ref(slot) if slot is not None else None
        return str(ref) if ref is not None else None

    async def handle_print_complete(self, printer_id: int) -> None:
        """Called by PrinterManager when the printer's vendor client signals print done."""
        job_id = None

        async with self._factory() as session:
            result = await session.execute(
                select(Job).where(
                    Job.status == "printing",
                    Job.assigned_printer_id == printer_id,
                )
            )
            job = result.scalar_one_or_none()
            if job is None:
                return
            job_id = job.id

            # Conditional UPDATE claims the completion atomically. The vendor client's
            # completion callback and _reconcile_printing_jobs can both observe
            # status=="printing" for the same job (several awaits separate this read
            # from the commit below); without this guard both would proceed and
            # double the inventory deduction, lifetime counters, and job.complete
            # webhook. Only the caller that flips the row wins.
            claim = await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "printing")
                .values(status="complete", completed_at=_now(), updated_at=_now())
            )
            if claim.rowcount == 0:
                return  # already claimed by a concurrent completion path

            # The wear counters, the spool deduction and the notices are effects of the durable `job.complete` event written below,
            # in THIS transaction (completion_events.py): they cannot be lost after the commit and cannot run twice. Which spool
            # was in use is resolved now, because the loaded slot may change once the print is over.
            printer = await session.get(Printer, printer_id)
            inventory_use = None
            actual_grams = job.actual_filament_grams
            if actual_grams is not None and inventory_deduction.can_deduct() \
                    and await inventory_config.deduct_enabled(session):
                loaded = (printer.loaded_filaments if printer else None) or []
                cfg_result = await session.execute(
                    select(JobPrinterConfig).where(
                        JobPrinterConfig.job_id == job_id,
                        JobPrinterConfig.printer_id == printer_id,
                    )
                )
                config = cfg_result.scalar_one_or_none()
                if config is not None:
                    slot = _slot_for_config(config, loaded)
                    if slot is not None:
                        raw_spool_id = inventory_refs.slot_spool_ref(slot)
                        if raw_spool_id is not None:
                            inventory_use = {"spool_ref": str(raw_spool_id), "grams": actual_grams}

            # Delete gcode file from disk and DB
            gcode_result = await session.execute(
                select(GcodeFile).where(
                    GcodeFile.job_id == job_id,
                    GcodeFile.printer_id == printer_id,
                )
            )
            gcode = gcode_result.scalar_one_or_none()
            if gcode:
                try:
                    os.remove(gcode.path)
                except OSError:
                    pass
                await session.delete(gcode)
            await completion_events.enqueue_job_complete(session, job, printer_id, source="queue", inventory=inventory_use)
            await session.commit()

        event_hub.wake()

    async def _fire_webhooks(self, job_id: int, event: str, event_id: str | None = None) -> None:
        try:
            async with self._factory() as session:
                extra = await self._job_webhook_fields(session, job_id)
                await webhook_service.dispatch(session, event, job_id, extra or None, event_id=event_id)
        except Exception:
            logger.exception("Failed to dispatch webhooks for job %s", job_id)

    @staticmethod
    async def _job_webhook_fields(session: AsyncSession, job_id: int) -> dict:
        """What a companion app needs to correlate a job event with its own record: the project, its source app and external ref."""
        job = await session.get(Job, job_id)
        project = await session.get(Project, job.project_id) if job is not None and job.project_id is not None else None
        if project is None:
            return {}
        return {k: v for k, v in (("project_id", project.id), ("source_app", project.source_app),
                                  ("external_ref", project.external_ref)) if v is not None}

    async def _fire_notifications(
        self, job_id: int, event: str, printer_id: int | None = None, reason: str | None = None
    ) -> None:
        try:
            async with self._factory() as session:
                cfg = await session.get(NotificationConfig, 1)
                if not cfg or not (cfg.ntfy_enabled or cfg.discord_enabled or cfg.email_enabled):
                    return
                job = await session.get(Job, job_id)
                if job is None:
                    return
                uploaded_file = await session.get(UploadedFile, job.uploaded_file_id)
                file_name = uploaded_file.original_filename if uploaded_file else f"job {job_id}"
                printer_name = None
                pid = printer_id if printer_id is not None else job.assigned_printer_id
                if pid is not None:
                    printer = await session.get(Printer, pid)
                    printer_name = printer.name if printer else None
            title, message = _notification_content(event, job_id, file_name, printer_name, reason)
            # Fire-and-forget, like _fire_webhooks/webhook_service.schedule: real
            # channel delivery is network/SMTP I/O with a 5s timeout per channel and
            # must not block the queue loop (this is awaited from _reconcile_printing_jobs,
            # which runs before new jobs are claimed each _process_queue iteration).
            # dispatch() already swallows every per-channel exception itself.
            asyncio.create_task(notification_service.dispatch(cfg, event, job_id, title, message))
        except Exception:
            logger.exception("Failed to dispatch notifications for job %s", job_id)

    async def _broadcast_job(self, job_id: int | None) -> None:
        if not self._broadcast_cb or job_id is None:
            return
        try:
            async with self._factory() as session:
                job = await session.get(Job, job_id)
                if job:
                    await self._broadcast_cb("job_update", {
                        "id": job.id,
                        "status": job.status,
                        "assigned_printer_id": job.assigned_printer_id,
                        "queue_position": job.queue_position,
                        "project_id": job.project_id,
                    })
                # Full queue broadcast (active jobs only)
                result = await session.execute(
                    select(Job)
                    .where(Job.status.not_in(["complete", "cancelled"]))
                    .order_by(Job.queue_position.asc())
                )
                all_jobs = result.scalars().all()
                await self._broadcast_cb("queue_update", [
                    {"id": j.id, "status": j.status, "queue_position": j.queue_position}
                    for j in all_jobs
                ])
        except Exception:
            logger.exception("Failed to broadcast job update")


queue_engine = QueueEngine.__new__(QueueEngine)  # uninitialized singleton — init in lifespan
