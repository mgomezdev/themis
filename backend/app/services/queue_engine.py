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
from typing import Callable

import httpx
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import get_laminus_sidecar_url
from ..models import GcodeFile, Job, JobPrinterConfig, Printer, QueueConfig, UploadedFile, WebhookConfig
from .printer_manager import PrinterManager
from .slicer_service import SliceError, SliceRequest, SlicerService
from . import webhook_service


def _parse_gcode_estimates(path: str) -> tuple[float | None, int | None, list[float] | None]:
    """Extract filament_grams (total), estimated_seconds, per-extruder grams from gcode.

    Returns (total_grams, seconds, extruder_grams_list). extruder_grams_list has one
    entry per comma-separated value in the 'filament used [g]' line. Returns None for
    each field independently if parsing fails.
    """
    try:
        if path.endswith(".3mf"):
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.endswith(".gcode")]
                if not names:
                    return None, None, None
                text = z.read(names[0]).decode("utf-8", errors="replace")[:16000]
        else:
            with open(path, "r", errors="replace") as f:
                text = f.read(16000)
    except Exception:
        return None, None, None

    grams: float | None = None
    extruder_grams: list[float] | None = None
    seconds: int | None = None
    for raw in text.splitlines():
        line = raw.lstrip("; ").strip()
        if "filament used [g]" in line.lower():
            raw_val = line.split("=")[-1].strip()
            parts = [p.strip() for p in raw_val.split(",")]
            try:
                extruder_grams = [float(p) for p in parts if p]
                grams = sum(extruder_grams)
            except ValueError:
                extruder_grams = None
                grams = None
        if "estimated printing time" in line.lower():
            time_str = re.split(r"\s*\(", line.split("=")[-1].strip())[0].strip()
            total = 0
            for num, unit in re.findall(r"(\d+)([hms])", time_str):
                if unit == "h":
                    total += int(num) * 3600
                elif unit == "m":
                    total += int(num) * 60
                else:
                    total += int(num)
            if total > 0:
                seconds = total
        if grams is not None and seconds is not None:
            break
    return grams, seconds, extruder_grams

logger = logging.getLogger(__name__)


_DEFAULT_CHECK_MINUTES = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _deduct_spool(url: str, api_key: str | None, spool_id: int, grams: float) -> None:
    """Fire-and-forget Spoolman deduction. Logs warning on failure; never raises."""
    try:
        from .spoolman_service import record_spool_use
        await record_spool_use(url, api_key, spool_id, grams)
    except Exception:
        logger.warning("Spoolman deduction failed: spool_id=%s grams=%s", spool_id, grams)


def _norm_color(value) -> str:
    return str(value or "").strip().lstrip("#").lower()


def _matching_loaded_filament(config: JobPrinterConfig, loaded: list) -> dict | None:
    """The printer's loaded filament slot that satisfies the job's ask (type AND
    color), or None. A job with no declared requirement matches the first slot.

    The OrcaSlicer filament *profile* used for slicing is a printer-level setting
    that lives on the matched slot (the "provide"); the job only declares the
    desired type/color (the "ask")."""
    req_type = (config.filament_type or "").strip().lower()
    req_color = _norm_color(config.filament_color)
    if not req_type and not req_color:
        return (loaded[0] if loaded else None)
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
        return loaded[ti] if 0 <= ti < len(loaded) else None
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
        0 <= e["tool_index"] < len(loaded)
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
    req_type = (config.filament_type or "").strip().lower()
    req_color = _norm_color(config.filament_color)
    if not req_type and not req_color:
        return None
    if _matching_loaded_filament(config, loaded) is not None:
        return None
    return (f"loaded filament doesn't match required "
            f"{config.filament_type or '?'} {config.filament_color or '?'}")


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

    def spawn_estimate(self, job_id: int) -> None:
        """Create and track a background estimate task for job_id."""
        task = asyncio.create_task(
            self.run_estimate(job_id), name=f"estimate-{job_id}"
        )
        self._estimate_tasks.add(task)
        task.add_done_callback(self._estimate_tasks.discard)

    async def run_estimate(self, job_id: int) -> None:
        """Background test slice to populate estimate_* fields on the Job row."""
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
            if config is None:
                return

            printer = await session.get(Printer, config.printer_id)
            if printer is None:
                return
            uploaded_file = await session.get(UploadedFile, job.uploaded_file_id)

            # Capture all scalars before session closes
            machine_preset = printer.current_orca_printer_profile or ""
            print_profile = config.print_profile or ""
            stored_path = uploaded_file.stored_path if uploaded_file else None
            printer_name = printer.name
            loaded = printer.loaded_filaments or []

            # Resolve filament profiles
            fmap = config.filament_map
            if fmap:
                for entry in sorted(fmap, key=lambda e: e.get("tool_index", 0) or 0):
                    ti = entry.get("tool_index")
                    ep = entry.get("filament_profile")
                    if ep:
                        filament_profiles.append(ep)
                    elif ti is not None and ti < len(loaded):
                        filament_profiles.append(loaded[ti].get("filament_profile", ""))
            else:
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
        grams, secs, extruder_grams = _parse_gcode_estimates(gcode_path)
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
            try:
                await asyncio.wait_for(self._event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass  # periodic availability re-check

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
        for printer_id in sorted(ready_set):
            async with self._factory() as session:
                if not await self._try_resume_sliced_job(session, printer_id):
                    await self._try_claim_for_printer(session, printer_id)
        # Offline printers: run the slice step now so gcode is ready when they come online.
        for printer_id in sorted(pid for pid in all_ids if pid not in ready_set):
            async with self._factory() as session:
                if not await self._has_pending_sliced_job(session, printer_id):
                    await self._try_claim_for_printer(session, printer_id, slice_only=True)

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

        # The FIRST queue item that lists this printer as compatible — head of line.
        # Blocked jobs are re-evaluated (loading the right filament unblocks them).
        stmt = (
            select(Job, JobPrinterConfig)
            .join(
                JobPrinterConfig,
                and_(JobPrinterConfig.job_id == Job.id, JobPrinterConfig.printer_id == printer_id),
            )
            .where(Job.status.in_(["queued", "blocked"]))
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

        # Pre-flight: ensure Laminus is reachable before claiming this job.
        # Block (not fail) so the job auto-retries when Laminus comes back.
        sidecar_url = get_laminus_sidecar_url()
        if not sidecar_url:
            await self._block_job(session, job, "Laminus sidecar not configured — slicing paused")
            return
        try:
            r = await asyncio.to_thread(
                lambda: httpx.get(f"{sidecar_url}/api/health", timeout=2)
            )
            if not r.is_success:
                await self._block_job(session, job, "Laminus is not ready — slicing paused")
                return
        except Exception:
            await self._block_job(session, job, "Laminus is unreachable — slicing paused")
            return

        # Claim → slice.
        job.status = "slicing"
        job.assigned_printer_id = printer_id
        job.block_reason = None
        job.updated_at = _now()
        plate_number = job.plate_number
        await session.commit()

        asyncio.create_task(
            self._run_slice_and_print(job_id, printer_id, plate_number, slice_only=slice_only),
            name=f"slice-{job_id}-{printer_id}",
        )
        await self._broadcast_job(job_id)

    async def _block_job(self, session: AsyncSession, job: Job, reason: str) -> None:
        job_id = job.id
        already = job.status == "blocked" and job.block_reason == reason
        job.status = "blocked"
        job.block_reason = reason
        job.assigned_printer_id = None
        job.updated_at = _now()
        await session.commit()
        if not already:  # avoid broadcast spam when re-blocking with the same reason
            await self._broadcast_job(job_id)

    async def _run_slice_and_print(self, job_id: int, printer_id: int, plate_number: int, slice_only: bool = False) -> None:
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
            print_profile = config.print_profile if config else None
            filament_color = config.filament_color if config else None
            # Filament profile: job-level config takes priority; slot's preset is the fallback.
            loaded = (printer.loaded_filaments if printer else None) or []
            slot = _slot_for_config(config, loaded) if config else None
            cfg_tool_index = config.tool_index if config else None
            cfg_filament_map = config.filament_map if config else None
            filament_profile = (config.filament_profile if config else None) or (slot or {}).get("filament_profile") or None
            # AMS printers (Bambu) map the print's filament to the matched tray.
            ams_tray_id = (slot or {}).get("ams_tray_id")
            stored_path = uploaded_file.stored_path if uploaded_file else None
            original_filename = uploaded_file.original_filename if uploaded_file else None
            machine_preset = printer.current_orca_printer_profile if printer else None
            build_plate_type = printer.build_plate_type if printer else None
            job_overrides = job.overrides or {} if job else {}  # capture before session closes

        if config is None or uploaded_file is None:
            await self._fail_job_post_slice(job_id, printer_id)
            return
        if not machine_preset:
            await self._handle_slice_failure(
                job_id, printer_id, "printer has no OrcaSlicer machine preset selected"
            )
            return

        # Meaningful, unique artifact name; the printer decides its output format.
        stem = os.path.splitext(os.path.basename(original_filename or "model"))[0]
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "model"
        file_base = f"{safe}_p{plate_number}_j{job_id}"
        client = self._mgr.get_client(printer_id)
        export_args = client.orca_export_args(file_base) if client else []

        # Resolve any catalog filament entries to slot indices before slicing.
        if cfg_filament_map:
            try:
                cfg_filament_map = _resolve_filament_map(cfg_filament_map, loaded)
            except ValueError as exc:
                await self._handle_slice_failure(job_id, printer_id, f"printer {printer_id}: {exc}")
                return

        prepare_hook = None
        if client is not None and (cfg_tool_index is not None or cfg_filament_map):
            prepare_hook = (lambda p, c=client, ti=cfg_tool_index, fm=cfg_filament_map:
                            c.remap_sliceable_3mf(p, tool_index=ti, filament_map=fm))

        loop = asyncio.get_running_loop()
        multi_presets: list = []
        if cfg_filament_map:
            ordered = sorted(loaded or [], key=lambda s: s.get("slot", 0))
            multi_presets = [s.get("filament_profile") for s in ordered if s.get("filament_profile")]
        plate_config = {"curr_bed_type": build_plate_type} if build_plate_type else {}
        plate_config.update(job_overrides)  # job-level overrides win over printer default
        req = SliceRequest(
            job_id=job_id,
            source_3mf=stored_path,
            plate_number=plate_number,
            machine_preset=machine_preset,
            process_preset=print_profile,
            filament_presets=multi_presets if cfg_filament_map else ([filament_profile] if filament_profile else []),
            filament_colours=[filament_color] if filament_color else [],
            export_args=export_args,
            prepare_hook=prepare_hook,
            extra_config=plate_config,
        )
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

        # Store gcode record; park as "sliced" if the printer isn't ready to receive.
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status == "cancelled":
                return
            grams, secs, extruder_grams = _parse_gcode_estimates(gcode_path)
            gcode_rec = GcodeFile(
                job_id=job_id, printer_id=printer_id, path=gcode_path,
                filament_grams=grams, estimated_seconds=secs,
            )
            session.add(gcode_rec)
            # Persist actuals on Job NOW — before GcodeFile is ever deleted.
            job.actual_filament_grams = grams
            job.actual_seconds = secs
            if extruder_grams is not None:
                job.actual_filament_breakdown = [
                    {
                        "extruder_index": i,
                        "filament_profile": req.filament_presets[i] if i < len(req.filament_presets) else None,
                        "grams": g,
                    }
                    for i, g in enumerate(extruder_grams)
                ]
            if slice_only or not self._mgr.is_printer_ready(printer_id):
                job.status = "sliced"
                job.assigned_printer_id = None
                job.updated_at = _now()
                await session.commit()
                await self._broadcast_job(job_id)
                self.wake()
                return
            job.status = "uploading"
            job.updated_at = _now()
            await session.commit()

        await self._broadcast_job(job_id)
        await self._do_upload_and_print(job_id, printer_id, gcode_path, plate_number, ams_tray_id)

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

        job.status = "uploading"
        job.assigned_printer_id = printer_id
        job.updated_at = _now()
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
            if job is None or job.status == "cancelled":
                return
            job.status = "printing"
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
            await session.commit()

        await self._broadcast_job(job_id)

    async def handle_print_complete(self, printer_id: int) -> None:
        """Called by PrinterManager when the printer's vendor client signals print done."""
        from ..models import SpoolmanConfig
        job_id = None
        spoolman_url: str | None = None
        spoolman_key: str | None = None
        spool_id: int | None = None
        grams_to_deduct: float | None = None

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
            job.status = "complete"
            job.completed_at = _now()
            job.updated_at = _now()

            # Collect Spoolman deduction data before session closes
            actual_grams = job.actual_filament_grams
            if actual_grams is not None:
                spoolman_cfg = await session.get(SpoolmanConfig, 1)
                if spoolman_cfg and spoolman_cfg.enabled and spoolman_cfg.url:
                    spoolman_url = spoolman_cfg.url
                    spoolman_key = spoolman_cfg.api_key
                    printer = await session.get(Printer, printer_id)
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
                            raw_spool_id = slot.get("spoolman_spool_id")
                            if raw_spool_id is not None:
                                try:
                                    spool_id = int(raw_spool_id)
                                    grams_to_deduct = actual_grams
                                    job.deduction_skipped = False
                                except (TypeError, ValueError):
                                    logger.warning(
                                        "Invalid spoolman_spool_id %r for job %s — deduction skipped",
                                        raw_spool_id, job_id,
                                    )

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
            await session.commit()

        if spool_id is not None and spoolman_url and grams_to_deduct is not None:
            task = asyncio.create_task(
                _deduct_spool(spoolman_url, spoolman_key, spool_id, grams_to_deduct)
            )
            self._estimate_tasks.add(task)
            task.add_done_callback(self._estimate_tasks.discard)

        await self._broadcast_job(job_id)
        await self._fire_webhooks(job_id, "job.complete")

    async def _fire_webhooks(self, job_id: int, event: str) -> None:
        try:
            async with self._factory() as session:
                cfg = await session.get(WebhookConfig, 1)
            if cfg and cfg.url and (not cfg.events or event in cfg.events):
                webhook_service.schedule(cfg.url, cfg.secret, event, job_id)
        except Exception:
            logger.exception("Failed to load webhook config for job %s", job_id)

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
