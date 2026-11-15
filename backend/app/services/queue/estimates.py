"""Background test-slices that fill in a job's estimate, verify-slice, and saving a finished slice to the library.

Methods of `QueueEngine` (app/services/queue_engine.py), which inherits this mixin: `self` is the engine. Names that
module defines, and the collaborators tests replace there, are read as `_qe.<name>` at call time. See README.md here.
"""
from __future__ import annotations
import asyncio
import shutil
from pathlib import Path
from sqlalchemy import select
from ...models import (
    Job,
    JobPrinterConfig,
    Printer,
    UploadedFile,
)
from ..slicer_service import SliceRequest
from .. import slice_cache, slice_saver, slicer_profiles
from .. import filament_matching

import app.services.queue_engine as _qe


class EstimatesMixin:
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
            _qe.logger.exception("Unexpected error in run_estimate for job %s", job_id)
            async with self._factory() as session:
                job = await session.get(Job, job_id)
                token = job.estimate_token if job else None
            await self._fail_estimate(job_id, token, f"Unexpected error: {exc}")

    async def _do_run_estimate(self, job_id: int) -> None:
        import json as _json

        # Step 1 — Load job and resolve config
        token: int | None = None
        process_missing: str | None = None
        machine_preset: str | None = None
        source_key: str | None = None
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
            process_missing = (_qe.missing_process_reason(uploaded_file, config, printer)
                               if config is not None and printer is not None and uploaded_file is not None else None)

            # Capture all scalars before session closes
            machine_preset = ((await slicer_profiles.machine_profile_for(session, printer)) or "") if printer else ""
            print_profile = _qe.effective_process_profile(config, printer) if config else ""
            source_key = uploaded_file.relative_path if uploaded_file else None
            printer_name = printer.name if printer else None
            loaded = (printer.loaded_filaments or []) if printer else []

            # Resolve filament profiles
            fmap = config.filament_map if config else None
            if fmap:
                plan = filament_matching.evaluate(config, loaded).plan       # empty when the printer does not satisfy every colour
                by_colour = {u.model_filament: u.slot for u in plan}
                for entry in sorted(fmap, key=lambda e: by_colour.get(e.get("model_filament"), e.get("tool_index", 0)) or 0):
                    ti = by_colour.get(entry.get("model_filament"), entry.get("tool_index"))
                    ep = entry.get("filament_profile")
                    if ep:
                        filament_profiles.append(ep)
                    elif ti is not None and ti < len(loaded):
                        filament_profiles.append(loaded[ti].get("filament_profile", ""))
            elif config is not None:
                slot = _qe._slot_for_config(config, loaded)
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
        if process_missing:
            await self._fail_estimate(job_id, token, process_missing)
            return
        if not machine_preset or not source_key or not filament_profiles:
            await self._fail_estimate(job_id, token, "missing machine preset, file, or filament profile")
            return

        # Step 3 — Enqueue slice
        output_dir = self._slicer._data_dir / "gcode_estimates" / str(job_id)
        req = SliceRequest(
            job_id=job_id,
            source_3mf="",
            source_key=source_key,
            plate_number=1,
            machine_preset=machine_preset,
            process_preset=print_profile,
            filament_presets=filament_profiles,
            filament_colours=[],
            export_args=[],
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
            _qe.logger.warning("Estimate slice failed for job %s: %s", job_id, exc)
            await self._fail_estimate(job_id, token, str(exc))
            return

        # Step 4 — Parse, discard gcode, write results
        est = _qe.get_format_provider().parse_estimates(gcode_path)
        grams, secs, extruder_grams = est.grams, est.seconds, est.per_extruder_grams
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
                    "now": _qe._now(),
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
                {"now": _qe._now(), "id": job_id, "token": token}
            )
            if result.rowcount > 0:
                await session.commit()
        _qe.logger.warning("Estimate failed for job %s: %s", job_id, reason)
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
