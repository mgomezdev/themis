from __future__ import annotations
import asyncio
import itertools
import logging
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import (
    GcodeFile,
    Job,
    JobPrinterConfig,
    Printer,
    Project,
    QueueConfig,
    SlicedVersion,
    UploadedFile,
)
from .library_scanner import is_presliced_file
from .printer_manager import PrinterManager
from .providers.slicing import effective_provider_id, get_format_provider, slicing_provider_for
from .slice_resolution import PrinterView, resolve_slicing
from .providers.contract import CancelToken, NothingFits
from .slicer_service import DEFAULT_BED_MM, SliceRequest, SlicerService, slice_side_mapping
from . import gcode_eligibility, model_targets, slicer_profiles, part_waivers
from . import completion_events
from . import filament_matching, pools, scheduling
from .library_store import get_library_store
from .notify import notify
from . import webhook_service
from ..plugins.host import plugin_host

# The engine's method groups live in app/services/queue/ (one mixin per concern; see its README).
from .queue.estimates import EstimatesMixin
from .queue.liveness import LivenessMixin
from .queue.slicing import SlicingMixin
from .queue.printing import PrintingMixin
from .queue.events import EventsMixin

# Collaborators the mixins call as `_qe.<name>` rather than importing themselves: tests swap them out on THIS module
# (`patch("app.services.queue_engine.notify")`), so this is the one place they are looked up.
_PATCH_POINTS = (get_library_store, notify, webhook_service, slicing_provider_for, get_format_provider, resolve_slicing, plugin_host)

logger = logging.getLogger(__name__)
POOL_BACKOFF_S = 60.0            # BIZ-362: after a pool pack error, the pool is skipped this long
_monotonic = time.monotonic


_DEFAULT_CHECK_MINUTES = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slot_for_config(config, loaded: list, conflicts=frozenset(), plan: list | None = None) -> dict | None:
    """The loaded slot this config prints with (its first colour's): the stored claim-time `plan` when given, else the pinned
    tool slot or the first slot satisfying the ask (filament_matching). None when nothing qualifies."""
    if plan is None and isinstance(getattr(config, "slot_plan", None), list):
        plan = config.slot_plan                           # the slots chosen when a printer claimed the job
    return filament_matching.slot_for_config(config, loaded, conflicts, plan)


def _effective_tool_index(config, loaded: list, plan: list) -> int | None:
    if config.filament_map:
        return None
    if config.tool_index is not None:
        return config.tool_index
    if len(loaded) >= 2 and plan and plan[0].slot > 0:      # T0 is where an unrouted model prints anyway
        return plan[0].slot
    return None


def _ams_mapping(plan: list, loaded: list) -> list[int] | None:
    """Printer tray ids indexed by model filament (entry N-1 = filament N), never list indices or spool refs. A filament number the
    plan doesn't use (a plate printing filaments 1 and 3 of a project) is -1, the documented "unused" value of `ams_mapping`."""
    if not plan:
        return None
    if any(not 0 <= u.slot < len(loaded) for u in plan):
        raise ValueError("A mapped filament slot was removed before printing")
    by_filament: dict[int, int | None] = {}
    for u in plan:
        mf = 1 if u.model_filament is None else u.model_filament       # the single-filament ask is filament 1
        if type(mf) is not int or not 1 <= mf <= filament_matching.MAX_MODEL_FILAMENTS or mf in by_filament:
            raise ValueError(f"Filament {mf} is mapped more than once or is not a filament number")
        by_filament[mf] = loaded[u.slot].get("ams_tray_id")
    if all(t is None for t in by_filament.values()):
        return None  # this printer does not use AMS tray identifiers
    if any(type(t) is not int or t < 0 for t in by_filament.values()):
        raise ValueError("Every mapped filament needs a valid AMS tray identifier")
    return [by_filament.get(n, -1) for n in range(1, max(by_filament) + 1)]


def effective_process_profile(config, printer) -> str:
    """The process preset a config slices with: its own, else ("Printer default" = "") the printer's default (BIZ-358)."""
    return ((config.print_profile if config else "") or (printer.default_process_profile if printer else "") or "")


def missing_process_reason(source_file, config, printer) -> str | None:
    """Why `config` can't be sliced on `printer` for want of a process profile, or None. Pre-sliced files need none."""
    if is_presliced_file(source_file) or effective_process_profile(config, printer):
        return None
    return (f"{printer.name} has no default process profile: set one on the printer (Printers → Edit) or choose a "
            "process profile for this job")


def _slice_params(job, config, printer, uploaded_file, machine_preset: str | None = None, plan: list | None = None) -> dict:
    """What a slice of `job` on `printer` is built from, read off the ORM rows so it outlives the session. Dispatch and
    the claim-time cache check (BIZ-201) both build their SliceRequest from this, so they agree on the cache key."""
    loaded = (printer.loaded_filaments if printer else None) or []
    plan = filament_matching.plan_for(config, loaded) if plan is None else plan
    slot = filament_matching.slot_for_config(config, loaded, plan=filament_matching.plan_to_json(plan)) if config and plan else None
    return {
        "source_key": uploaded_file.relative_path if uploaded_file else None,
        "original_filename": uploaded_file.original_filename if uploaded_file else None,
        "machine_preset": machine_preset if printer else None,   # resolved from the model mapping + nozzle variant (BIZ-294)
        "build_plate_type": printer.build_plate_type if printer else None,
        "loaded": loaded,
        "print_profile": effective_process_profile(config, printer) if config else None,
        "filament_profile_override": config.filament_profile if config else None,
        "filament_color": (slot or {}).get("color") or (config.ask_color if config else None),    # preview only: what is actually loaded
        # Filament profile: job-level config takes priority; the matched slot's preset (the operator's per-slot choice) is the fallback.
        "filament_profile": (config.filament_profile if config else None) or (slot or {}).get("filament_profile") or None,
        # A pinned tool, else (tool-changer printers, where the 3MF is routed at slice time) the one slot the ask matched, so what is
        # sliced, printed and deducted is the slot the claim chose.
        "tool_index": _effective_tool_index(config, loaded, plan) if config else None,
        # The slicer's routing: model colour -> slot, resolved at claim time (the asks that produced it stay out of the cache key).
        "filament_map": filament_matching.resolved_filament_map(config, plan) if config else None,
        "slot_plan": plan,
        "overrides": (job.overrides or {}) if job else {},
        "preferred_slicer": printer.preferred_slicer_plugin_id if printer else None,
        "bed_mm": (float(printer.bed_x_mm or DEFAULT_BED_MM[0]), float(printer.bed_y_mm or DEFAULT_BED_MM[1]), DEFAULT_BED_MM[2])
                  if printer else None,
    }


def build_slice_request(job_id: int, printer_id: int, plate_number: int,
                   p: dict, client, provider_id: str | None = None) -> tuple[SliceRequest, list | None]:
    """The production SliceRequest for `p` (see `_slice_params`) plus the RESOLVED filament map (slot indices —
    part of the cache key)."""
    # Meaningful, unique artifact name; the printer decides its output format.
    stem = os.path.splitext(os.path.basename(p["original_filename"] or "model"))[0]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "model"
    file_base = f"{safe}_p{plate_number}_j{job_id}"
    export_args = client.orca_export_args(file_base) if client else []

    loaded = p["loaded"]
    tool_index = p["tool_index"]
    filament_map = p["filament_map"]                                   # already resolved to slots at claim time

    provider_id = provider_id or effective_provider_id(p.get('preferred_slicer'))
    slice_tool, slice_map = slice_side_mapping(client, tool_index, filament_map)

    multi_presets: list = []
    if filament_map:
        indices = [entry.get("model_filament") for entry in filament_map]
        if any(type(index) is not int or index < 1 for index in indices) or len(set(indices)) != len(indices):
            raise ValueError("Filament mapping requires a unique positive model filament number for every entry")
        override = p.get("filament_profile_override")
        for entry in filament_map:
            tool = entry["tool_index"]
            if not override and not loaded[tool].get("filament_profile"):
                raise ValueError(f"Filament {entry['model_filament']} matched T{tool}, but that tool has no filament profile")
        # Presets are indexed by physical tool after remapping. Never compact away an empty tool:
        # that would give T2 the profile of T3. Unused tools may borrow the first matched profile.
        fallback = p["filament_profile"]
        if slice_map:
            multi_presets = [override or s.get("filament_profile") or fallback for s in loaded]
        else:
            # AMS printers do not rewrite the source: the slicer's profiles stay in model-filament
            # order. The physical slot mapping is realized later by start_print's AMS tray list.
            ordered = sorted(filament_map, key=lambda e: e["model_filament"])
            if [e["model_filament"] for e in ordered] != list(range(1, len(ordered) + 1)):
                raise ValueError("Filament mapping must include every model filament in order")
            multi_presets = [override or loaded[e["tool_index"]]["filament_profile"] for e in ordered]
    plate_config = {"curr_bed_type": p["build_plate_type"]} if p["build_plate_type"] else {}
    plate_config.update(p["overrides"])  # job-level overrides win over printer default
    filament_profile, filament_color = p["filament_profile"], p["filament_color"]
    req = SliceRequest(
        job_id=job_id,
        source_3mf="",
        source_key=p["source_key"],
        plate_number=plate_number,
        machine_preset=p["machine_preset"],
        process_preset=p["print_profile"],
        filament_presets=multi_presets if filament_map else ([filament_profile] if filament_profile else []),
        filament_colours=[filament_color] if filament_color else [],
        export_args=export_args,
        extra_config=plate_config,
        provider_id=provider_id,
        tool_index=slice_tool,
        filament_map=slice_map,
        **({"bed_mm": p["bed_mm"]} if p.get("bed_mm") else {}),
    )
    return req, filament_map


def _notification_content(
    event: str, job_id: int, file_name: str, printer_name: str | None, reason: str | None, origin: str | None = None
) -> tuple[str, str]:
    """Build a plain, human-readable (title, message) pair for a job event.
    No templating engine, no config-driven customization — basic per-event
    text, mirroring the fixed shape of the built-in notification channels. A plugin's report is prefixed with its id
    (`bambu: ...`) so the reader knows who said it; core's own events read as before."""
    title, message = _notification_text(event, job_id, file_name, printer_name, reason)
    return title, (f"{origin}: {message}" if origin and origin != "core" else message)


def _notification_text(event: str, job_id: int, file_name: str, printer_name: str | None, reason: str | None) -> tuple[str, str]:
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


# Marks a `blocked` job the unmatched-job sweep owns: it re-checks only these, and releases them when a match appears.
NO_MATCH_PREFIX = "no eligible printer"


class QueueEngine(EstimatesMixin, LivenessMixin, SlicingMixin, PrintingMixin, EventsMixin):
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
        # In-flight production slices: job id -> (printer id, token). Job cancel and printer stop set the token (BIZ-295).
        self._slice_cancels: dict[int, tuple[int, CancelToken]] = {}
        self._slice_worker_task: asyncio.Task | None = None
        self._estimate_tasks: set[asyncio.Task] = set()
        # BIZ-362 pools: group id -> printer it is packing for (one pack per pool at a time), group id -> monotonic time its
        # error backoff ends, (group id, printer id) -> the parts that printer was offered when nothing fitted its bed.
        self._pool_busy: dict[int, int] = {}
        self._pool_backoff: dict[int, float] = {}
        self._pool_nofit: dict[tuple[int, int], tuple] = {}
        self._pool_tasks: set[asyncio.Task] = set()
        # Slice-and-print, upload-and-print and notification tasks in flight (see _spawn). stop() cancels and awaits them.
        self._job_tasks: set[asyncio.Task] = set()
        completion_events.bind_engine(self)      # the `job_complete.notices` subscriber sends through the engine that was last built

    def _spawn(self, coro, *, name: str | None = None) -> asyncio.Task:
        """Start a fire-and-forget task and keep it referenced until it is done. The event loop holds tasks only weakly,
        so one nobody else references can be garbage-collected mid-run: a print that silently never starts."""
        task = asyncio.create_task(coro, name=name)
        self._job_tasks.add(task)
        task.add_done_callback(self._job_tasks.discard)
        return task

    async def cancel_job_tasks(self) -> None:
        """Cancel every task _spawn started and wait until each has unwound (shutdown, and test teardown: a task left
        pending on the engine outlives its event loop and is torn down later, inside whatever is running then)."""
        tasks = [t for t in self._job_tasks if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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


    def cancel_slice(self, *, job_id: int | None = None, printer_id: int | None = None) -> list[int]:
        """Ask the in-flight production slice(s) of `job_id`, or running for `printer_id`, to stop (BIZ-295: job cancel and printer
        stop). The provider abandons the slice and the dispatch ends without failing the job. Returns the affected job ids."""
        hits = [jid for jid, (pid, _) in getattr(self, "_slice_cancels", {}).items()
                if (job_id is not None and jid == job_id) or (printer_id is not None and pid == printer_id)]
        for jid in hits:
            self._slice_cancels[jid][1].cancel()
        return hits

    def wake(self) -> None:
        event = getattr(self, "_event", None)          # the module singleton is built uninitialised until the app starts
        if event is not None:
            event.set()

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
        for t in list(self._estimate_tasks) + list(self._pool_tasks):
            t.cancel()
        if self._pool_tasks:
            await asyncio.gather(*self._pool_tasks, return_exceptions=True)
        if self._estimate_tasks:
            await asyncio.gather(*self._estimate_tasks, return_exceptions=True)
        await self.cancel_job_tasks()
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
            dead_wait = self._mgr.seconds_until_next_dead(await self._dead_after_minutes())
            if isinstance(dead_wait, (int, float)):
                interval = min(interval, dead_wait)           # a printer crossing its offline cutoff is acted on when it happens
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
        await self._liveness_pass()
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
        try:
            await self._sweep_unmatched_jobs()
        except Exception:
            logger.exception("Queue engine error sweeping unmatched jobs")


    async def _try_claim_for_printer(self, session: AsyncSession, printer_id: int, slice_only: bool = False) -> None:
        printer = await session.get(Printer, printer_id)
        if printer is None or not printer.queue_on or not printer.configured:
            return                   # BIZ-369: only a printer a connection has confirmed takes work
        if printer_id in self._pool_busy.values():
            return                   # BIZ-362: its pool plate is being packed; it claims that plate next

        # "Any printer of this model" jobs: keep this printer's materialized configs current before looking.
        await model_targets.sync_targets_for_printer(session, printer)
        await session.commit()

        # The FIRST queue item that lists this printer as compatible AND whose filament this printer can supply — head of line.
        # A job whose filament this printer can't supply is skipped (it stays queued, never blocked or failed for that: loading
        # the right filament makes it claimable on the next cycle, and the job detail says why it doesn't match). Blocked jobs
        # are re-evaluated (fixing the cause unblocks them).
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
        )
        loaded = list(printer.loaded_filaments or [])
        conflicts = await filament_matching.conflicts_for(loaded)
        # BIZ-365: a waived printer skips jobs carrying a part it keeps failing; at its stop threshold, or while a test print
        # is pending, it claims nothing but that test print.
        waived = await part_waivers.waived_keys(session, printer_id)
        if printer.test_print_job_id is not None:
            await part_waivers.end_stale_test_print(session, printer)
            await session.commit()
        only_job = printer.test_print_job_id
        if only_job is None and part_waivers.stopped(printer, len(waived)):
            return
        job = config = fit = None
        rows = (await session.execute(stmt)).all()
        # BIZ-362: pools take their place in the queue by position; a pool this printer can use packs its next plate (in the
        # background) and the printer waits for it; any pool problem is a skip, never a block.
        pools_left = [] if only_job is not None or slice_only else [     # an offline printer never reserves pool parts
            g for g in await pools.active_pools(session) if await pools.eligible_on(session, g, printer)]
        for cand_job, cand_config in rows + [(None, None)]:
            while pools_left and (cand_job is None or pools_left[0].queue_position <= cand_job.queue_position):
                if await self._pool_turn(session, pools_left.pop(0), printer, conflicts):
                    return
            if cand_job is None:
                break
            if only_job is not None and cand_job.id != only_job:
                continue
            if waived and cand_job.id != only_job and await part_waivers.keys_of_job(session, cand_job) & waived:
                continue
            # If this job can't run on this printer, block it and STOP — do not look
            # further down the queue for this printer (head-of-line blocking).
            if cand_config.slice_failed:
                await self._block_job(session, cand_job, cand_config.slice_error or "slicing failed")
                return
            cand_fit = filament_matching.evaluate_for_printer(cand_config, printer, conflicts)
            # A "Printer default" config on a printer with no default can't be sliced HERE: skip it like a filament mismatch
            # (another printer may run it); the unmatched sweep blocks it when no printer can (BIZ-358).
            if cand_fit.eligible and not missing_process_reason(
                    await session.get(UploadedFile, cand_job.uploaded_file_id), cand_config, printer):
                job, config, fit = cand_job, cand_config, cand_fit
                break
        if job is None:
            return
        job_id = job.id

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
            if version is not None and version.machine_preset != await slicer_profiles.machine_profile_for(session, printer):
                await self._block_job(session, job, f"{printer.name} is no longer a {version.machine_preset} — this "
                                                    "cached version was sliced for that")
                return

        # Pre-flight: resolve the slicing provider for THIS printer (its preferred `gcode.produce` plugin, else the capability
        # default) and make sure it is usable before claiming this job (BIZ-291). Block (not fail) so the job auto-retries when
        # the provider comes back. Pre-sliced gcode jobs never touch the slicer, so they don't need one.
        # With the provider down, a job that allows cached slices may still go to THIS printer when a usable cached version
        # matches this printer's slice of it (BIZ-201) — dispatch then prints that version and never slices. Only a provider that
        # is down qualifies; a missing, disabled or ineligible one is for the user to fix (never a silent fallback, BIZ-250).
        cache_only_version: int | None = None
        provider_id: str | None = None
        if not is_presliced_file(source_file):
            resolution = await slicer_profiles.resolve_for_printer(session, printer)
            decision = await resolve_slicing(PrinterView.of(printer, resolution), source_file.original_filename if source_file else "")
            provider_id = decision.provider_id
            if decision.blocked:
                if decision.recoverable and job.allow_cached_slice:
                    cache_only_version = await self._cached_version_for_claim(
                        session, job, config, printer, source_file, provider_id, fit.plan)
                if cache_only_version is None:
                    await self._block_job(session, job, decision.message)
                    return

        # Claim → slice. Conditional UPDATE guards against a status change (e.g. a
        # user cancel) that committed while we awaited the Laminus health probe above.
        plate_number = job.plate_number
        # The slots this print uses (slicing and deduction follow it), with what each holds now: dispatch re-checks that.
        config.slot_plan = filament_matching.plan_to_json(filament_matching.pin_identities(fit.plan, printer.loaded_filaments))
        result = await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status.in_(["queued", "blocked"]))
            .values(status="slicing", assigned_printer_id=printer_id, block_reason=None, unmatched_filaments=None, updated_at=_now())
        )
        if result.rowcount == 0:
            return  # no longer claimable — bail without starting the slice/print
        await session.commit()

        self._spawn(
            self._run_slice_and_print(job_id, printer_id, plate_number, slice_only=slice_only,
                                      cache_only_version=cache_only_version, provider_id=provider_id),
            name=f"slice-{job_id}-{printer_id}",
        )
        await self._broadcast_job(job_id)

    async def _pool_turn(self, session: AsyncSession, group, printer: Printer, conflicts) -> bool:
        """Start packing `printer`'s next plate from pool `group`, unless it is busy, backing off, has nothing this printer can
        take, or already found nothing fits this printer's bed for the same parts. True = started (the claim stops here)."""
        gid = group.id
        if gid in self._pool_busy or self._pool_backoff.get(gid, 0) > _monotonic():
            return False
        take = await pools.takeable(session, group, printer, conflicts)
        snapshot = tuple(sorted(take.items()))
        if not take or self._pool_nofit.get((gid, printer.id)) == snapshot:
            return False
        from .providers import construction
        constructor = construction.get_project_constructor()
        if constructor is None:
            return False
        self._pool_busy[gid] = printer.id
        task = asyncio.create_task(self._pack_pool_plate(gid, printer.id, constructor, snapshot), name=f"pool-{gid}-{printer.id}")
        self._pool_tasks.add(task)
        task.add_done_callback(self._pool_tasks.discard)
        return True

    async def _pack_pool_plate(self, gid: int, printer_id: int, constructor, snapshot: tuple) -> None:
        job_id = None
        try:
            job_id = await pools.pack_plate(self._factory, gid, printer_id, constructor)
        except NothingFits:
            self._pool_nofit[(gid, printer_id)] = snapshot   # the printer claims the next job now
        except Exception:                              # noqa: BLE001 - a pack problem is a skip with a backoff, never a failure
            logger.warning("pool %s: packing a plate for printer %s failed; retrying in %ss", gid, printer_id,
                           POOL_BACKOFF_S, exc_info=True)
            self._pool_backoff[gid] = _monotonic() + POOL_BACKOFF_S
        else:
            self._pool_backoff.pop(gid, None)
        finally:
            self._pool_busy.pop(gid, None)
        self.wake()
        if job_id is not None:
            try:
                await self._broadcast_job(job_id)
            except Exception:                          # noqa: BLE001 - a broadcast never fails the pack
                logger.warning("pool %s: broadcasting job %s failed", gid, job_id, exc_info=True)

    async def settle_pool_packs(self) -> None:
        """Wait for the in-flight pool packs (tests, shutdown)."""
        while self._pool_tasks:
            await asyncio.gather(*list(self._pool_tasks), return_exceptions=True)


    async def _block_job(self, session: AsyncSession, job: Job, reason: str, *, notify: bool = False, unmatched_filaments: dict | None = None) -> None:
        job_id = job.id
        already = job.status == "blocked" and job.block_reason == reason
        # Conditional UPDATE, mirroring the claim guard above: some call sites reach
        # here after an await (the Laminus health probe), so a concurrent status
        # change (e.g. a user cancel) may have committed in the gap. Only overwrite
        # if the job is still in a blockable state.
        result = await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status.in_(["queued", "blocked"]))
            # BIZ-362: a pool plate is bound to one printer; it is cancelled instead, so its parts go back into the pool.
            .values(status="cancelled" if job.pool_group_id is not None else "blocked", block_reason=reason,
                    unmatched_filaments=unmatched_filaments, assigned_printer_id=None, updated_at=_now())
        )
        if result.rowcount == 0:
            return  # no longer blockable — don't resurrect a cancelled job
        await session.commit()
        if not already:  # avoid broadcast spam when re-blocking with the same reason
            await self._broadcast_job(job_id)
            await self._publish_job_event("job.blocked", job_id, None, "core", {"reason": reason, "phase": "queue"},
                                          dedup=f"job.blocked:{job_id}:{_now()}")
            if notify:                                      # a block that needs the user (fleet-wide mismatch) is also announced
                await self._fire_webhooks(job_id, "job.blocked")
                await self._fire_notifications(job_id, "job.blocked", reason=reason, origin="core")


queue_engine = QueueEngine.__new__(QueueEngine)  # uninitialized singleton — init in lifespan
