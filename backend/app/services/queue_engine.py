from __future__ import annotations
import asyncio
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import GcodeFile, Job, JobPrinterConfig, Printer, QueueConfig, UploadedFile
from .printer_manager import PrinterManager
from .slicer_service import SliceError, SliceRequest, SlicerService

logger = logging.getLogger(__name__)


_DEFAULT_CHECK_MINUTES = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _filament_mismatch(config: JobPrinterConfig, loaded: list) -> str | None:
    """Return a reason string if the job's required filament (type AND color) is
    not present in the printer's loaded filaments, else None. A job with no
    declared filament requirement is treated as matching."""
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

    def wake(self) -> None:
        self._event.set()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="queue_engine")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
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
        ready_ids = sorted([
            pid for pid in self._mgr.get_all_printer_ids()
            if self._mgr.is_printer_ready(pid)
        ])
        for printer_id in ready_ids:
            async with self._factory() as session:
                await self._try_claim_for_printer(session, printer_id)

    async def _try_claim_for_printer(self, session: AsyncSession, printer_id: int) -> None:
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

        # Claim → slice.
        job.status = "slicing"
        job.assigned_printer_id = printer_id
        job.block_reason = None
        job.updated_at = _now()
        plate_number = job.plate_number
        await session.commit()

        asyncio.create_task(
            self._run_slice_and_print(job_id, printer_id, plate_number),
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

    async def _run_slice_and_print(self, job_id: int, printer_id: int, plate_number: int) -> None:
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
            # Filament profile is a printer-level setting: resolve the OrcaSlicer
            # filament preset from the loaded slot that satisfies the job's ask.
            loaded = (printer.loaded_filaments if printer else None) or []
            slot = _matching_loaded_filament(config, loaded) if config else None
            filament_profile = (slot or {}).get("filament_profile") or None
            stored_path = uploaded_file.stored_path if uploaded_file else None
            original_filename = uploaded_file.original_filename if uploaded_file else None
            machine_preset = printer.current_orca_printer_profile if printer else None

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

        loop = asyncio.get_running_loop()
        req = SliceRequest(
            job_id=job_id,
            source_3mf=stored_path,
            plate_number=plate_number,
            machine_preset=machine_preset,
            process_preset=print_profile,
            filament_presets=[filament_profile] if filament_profile else [],
            filament_colours=[filament_color] if filament_color else [],
            export_args=export_args,
        )
        try:
            gcode_path: str = await loop.run_in_executor(self._executor, self._slicer.slice, req)
        except SliceError as exc:
            await self._handle_slice_failure(job_id, printer_id, str(exc))
            return
        except Exception as exc:
            logger.exception("Unexpected slice error for job %s on printer %s", job_id, printer_id)
            await self._handle_slice_failure(job_id, printer_id, f"Unexpected error: {exc}")
            return

        # Store gcode record and transition to uploading
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status == "cancelled":
                return
            gcode_rec = GcodeFile(job_id=job_id, printer_id=printer_id, path=gcode_path)
            session.add(gcode_rec)
            job.status = "uploading"
            job.updated_at = _now()
            await session.commit()
            plate_number = job.plate_number

        await self._broadcast_job(job_id)

        # Upload and start print
        client = self._mgr.get_client(printer_id)
        gcode_filename = os.path.basename(gcode_path)

        if client.file_upload_supported:
            try:
                with open(gcode_path, "rb") as fh:
                    data = fh.read()
                upload_ok = await loop.run_in_executor(
                    self._executor, client.upload_file, data, gcode_filename
                )
            except Exception:
                logger.exception("Gcode upload failed for job %s on printer %s", job_id, printer_id)
                upload_ok = False
            if not upload_ok:
                logger.warning("Upload of %s to printer %s reported failure for job %s",
                               gcode_filename, printer_id, job_id)
                await self._fail_job_post_slice(job_id, printer_id)
                return

        from .abstract_printer_client import StartPrintOptions
        opts = StartPrintOptions(plate_id=plate_number, gcode_path=gcode_filename)
        try:
            start_ok = await loop.run_in_executor(
                self._executor, client.start_print, gcode_filename, opts
            )
        except Exception:
            logger.exception("start_print failed for job %s on printer %s", job_id, printer_id)
            start_ok = False
        if not start_ok:
            logger.warning("start_print of %s on printer %s reported failure for job %s",
                           gcode_filename, printer_id, job_id)
            await self._fail_job_post_slice(job_id, printer_id)
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

    async def _fail_job_post_slice(self, job_id: int, printer_id: int) -> None:
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            if job:
                job.status = "failed"
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
            job.status = "complete"
            job.updated_at = _now()

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

        await self._broadcast_job(job_id)

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
                    })
                # Full queue broadcast (active jobs only)
                result = await session.execute(
                    select(Job)
                    .where(Job.status.not_in(["complete", "failed", "cancelled"]))
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
