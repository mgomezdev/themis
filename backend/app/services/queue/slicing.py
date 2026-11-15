"""A claimed job's slice: the slice-and-print task, the slicing cache, pre-sliced staging, and what a slice or upload failure does to the job.

Methods of `QueueEngine` (app/services/queue_engine.py), which inherits this mixin: `self` is the engine. Names that
module defines, and the collaborators tests replace there, are read as `_qe.<name>` at call time. See README.md here.
"""
from __future__ import annotations
import asyncio
import os
import re
import shutil
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ...models import (
    GcodeFile,
    Job,
    JobPrinterConfig,
    Printer,
    QueueConfig,
    SlicedVersion,
    UploadedFile,
)
from ..artifact_store import ArtifactError
from ..library_scanner import (
    fresh_content_hash, is_presliced_file, presliced_suffix, refresh_content_hash,
)
from ..providers.slicing import effective_provider_id
from ..slice_resolution import PrinterView
from ..providers.contract import CancelToken
from ..slicer_service import SliceCancelled, SliceError, SliceRequest
from .. import slice_cache, slicer_profiles
from .. import filament_matching

import app.services.queue_engine as _qe


class SlicingMixin:
    async def _cached_version_for_claim(
        self, session: AsyncSession, job: Job, config: JobPrinterConfig, printer: Printer, source_file: UploadedFile,
        provider_id: str | None = None,
        plan: list | None = None,
    ) -> int | None:
        """The usable cached version matching `printer`'s slice of `job` — the key dispatch would look up — or None.
        Only asked while Laminus is down, so staleness can't be checked (unknown counts as usable, as at dispatch).
        Never raises: any doubt means no version, and the job waits for Laminus as before."""
        try:
            machine_preset = await slicer_profiles.machine_profile_for(session, printer)
            if source_file is None or not machine_preset:
                return None
            if await refresh_content_hash(source_file, _qe.get_library_store()):   # the key hashes the model's bytes
                await session.commit()
            params = _qe._slice_params(job, config, printer, source_file, machine_preset, plan)
            try:
                req, fmap = self._slice_request(job.id, printer.id, job.plate_number, params, provider_id)
            except ValueError:   # a mapped filament isn't loaded: dispatch couldn't build this slice either
                return None
            inputs = slice_cache.key_inputs(req, source_file.content_hash, params["tool_index"], fmap)
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
                list(inputs.filament_presets), _qe.slicing_provider_for(inputs.provider))
            stale, _ = slice_cache.staleness(version.preset_content_hash, version.slicer_version, current)
            if stale and use_latest:
                return None
            slice_cache.log_event("gate_bypass", job_id=job.id, printer_id=printer.id, source_file_id=source_file.id,
                                  cache_key=key, sliced_version_id=version.id, cached_file_id=cached_file.id)
            return version.id
        except Exception:
            _qe.logger.exception("slicing-cache claim check failed for job %s on printer %s", job.id, printer.id)
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
        for v, f in rows:
            if f.missing:
                continue
            fresh = await asyncio.to_thread(
                fresh_content_hash, _qe.get_library_store(), f.relative_path, f.content_hash, f.size_bytes, f.mtime)
            if fresh is not None and fresh[0] == f.content_hash:
                return (v, f), True
        return None, bool(rows)

    async def _run_slice_and_print(self, job_id: int, printer_id: int, plate_number: int, slice_only: bool = False,
                                   cache_only_version: int | None = None, provider_id: str | None = None) -> None:
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
            plan = filament_matching.plan_for(config, loaded) if config else []
            plan_gone = filament_matching.still_loaded(plan, loaded) if config else None
            cfg_tool_index = None   # set from the slice params below
            # AMS printers (Bambu) map the print's filament to the matched tray.
            try:
                ams_tray_id, ams_error = _qe._ams_mapping(plan, loaded), None
            except ValueError as exc:
                ams_tray_id, ams_error = None, str(exc)
            source_key = uploaded_file.relative_path if uploaded_file else None
            original_filename = uploaded_file.original_filename if uploaded_file else None
            resolution = (await slicer_profiles.resolve_for_printer(session, printer, provider_id or effective_provider_id(
                printer.preferred_slicer_plugin_id))) if printer else None
            machine_preset = resolution.profile if resolution else None
            printer_view = PrinterView.of(printer, resolution) if printer else None
            params = _qe._slice_params(job, config, printer, uploaded_file, machine_preset, plan)
            cfg_tool_index = params["tool_index"]
            is_gcode = is_presliced_file(uploaded_file)
            allow_cached = bool(job.allow_cached_slice) if job else False
            if uploaded_file is not None and not is_gcode and (allow_cached or (job is not None and job.save_slice)):
                # The slicing cache keys on the model's bytes: never trust an index entry the file has outgrown.
                if await refresh_content_hash(uploaded_file, _qe.get_library_store()):
                    await session.commit()
            source_content_hash = uploaded_file.content_hash if uploaded_file else ""
            source_file_id = uploaded_file.id if uploaded_file else None

        if config is None or uploaded_file is None or printer_view is None:
            await self._fail_job_post_slice(job_id, printer_id)
            return
        if ams_error:
            await self._handle_slice_failure(job_id, printer_id, ams_error)
            return
        if plan_gone:
            # The filament was there when the printer claimed the job and was removed since: a slice failure (blocked, recoverable
            # with unblock), not a mismatch (a mismatch keeps the job queued).
            await self._handle_slice_failure(job_id, printer_id, plan_gone)
            return
        slice_presets: list = []
        slice_inputs: slice_cache.CacheKeyInputs | None = None   # what this slice was made from (slicing cache)
        cache_hit = False   # printing a cached version instead of a fresh slice
        if is_gcode:
            # Pre-sliced upload: nothing to slice. Stage a private copy (finished jobs delete their gcode file,
            # which must never be the library's own copy) and carry on to upload + print.
            try:
                gcode_path = await asyncio.to_thread(
                    self._stage_gcode, job_id, source_key, original_filename or "model.gcode"
                )
            except OSError as exc:
                await self._handle_slice_failure(job_id, printer_id, f"could not read the uploaded gcode: {exc}")
                return
        else:
            if not machine_preset:
                await self._handle_slice_failure(
                    job_id, printer_id, (resolution.reason if resolution and resolution.reason else "printer has no slicer machine profile")
                )
                return

            try:
                req, cfg_filament_map = self._slice_request(job_id, printer_id, plate_number, params, provider_id)
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
                # Back up since the claim? Then just slice as usual (resolved again: the provider may still be down, or gone).
                again = await _qe.resolve_slicing(printer_view, original_filename or "")
                down = again.message if again.blocked else None
            if cached is not None:
                gcode_path = cached
                cache_hit = True
            elif down:
                await self._release_cache_only_claim(job_id, printer_id, down)
                return
            else:
                fut: asyncio.Future = loop.create_future()
                token = CancelToken()
                self._slice_cancels[job_id] = (printer_id, token)

                async def _do_prod_slice():
                    try:
                        # Skip the slice if the job was cancelled while waiting in the queue.
                        async with self._factory() as s:
                            j = await s.get(Job, job_id)
                            if j is None or j.status == "cancelled" or token.cancelled:
                                if not fut.cancelled():
                                    fut.cancel()
                                return
                        result = await asyncio.to_thread(self._slicer.slice, req, None, token)
                        if not fut.cancelled():
                            fut.set_result(result)
                    except Exception as exc:
                        if not fut.cancelled():
                            fut.set_exception(exc)

                await self._slice_queue.put((0, next(self._slice_seq), _do_prod_slice()))
                try:
                    gcode_path = await fut
                except asyncio.CancelledError:
                    token.cancel()                 # the dispatch is going away: the worker thread must not slice on
                    fut.cancel()
                    raise
                except SliceCancelled:
                    _qe.logger.info("Slice of job %s on printer %s cancelled", job_id, printer_id)
                    return                         # the job was cancelled / the printer stopped: not a slice failure
                except SliceError as exc:
                    await self._handle_slice_failure(job_id, printer_id, str(exc))
                    return
                except Exception as exc:
                    _qe.logger.exception("Unexpected slice error for job %s on printer %s", job_id, printer_id)
                    await self._handle_slice_failure(job_id, printer_id, f"Unexpected error: {exc}")
                    return
                finally:
                    if self._slice_cancels.get(job_id, (None, None))[1] is token:
                        self._slice_cancels.pop(job_id, None)

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
            est = _qe.get_format_provider().parse_estimates(gcode_path, plate_number)
            grams, secs, extruder_grams = est.grams, est.seconds, est.per_extruder_grams
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
                job.updated_at = _qe._now()
                await session.commit()
                if want_save:
                    self.spawn_save_slice(job_id, printer_id, gcode_path, slice_inputs)
                await self._broadcast_job(job_id)
                self.wake()
                return
            job.status = "uploading"
            job.updated_at = _qe._now()
            await session.commit()
        if want_save:
            self.spawn_save_slice(job_id, printer_id, gcode_path, slice_inputs)

        await self._broadcast_job(job_id)
        await self._do_upload_and_print(job_id, printer_id, gcode_path, plate_number, ams_tray_id)

    def _slice_request(self, job_id: int, printer_id: int, plate_number: int,
                       p: dict, provider_id: str | None = None) -> tuple[SliceRequest, list | None]:
        return _qe.build_slice_request(job_id, printer_id, plate_number, p, self._mgr.get_client(printer_id), provider_id)

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
            _qe.logger.exception("slicing-cache lookup failed for job %s", job_id)
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
            list(inputs.filament_presets), _qe.slicing_provider_for(inputs.provider))
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
                self._stage_gcode, job_id, cached_file.relative_path, cached_file.original_filename)
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
                .values(status="blocked", block_reason=reason, assigned_printer_id=None, updated_at=_qe._now())
            )
            await session.commit()
        slice_cache.log_event("gate_bypass_released", job_id=job_id, printer_id=printer_id, reason=reason)
        if result.rowcount:
            await self._broadcast_job(job_id)
            await self._publish_job_event("job.blocked", job_id, printer_id, "core", {"reason": reason, "phase": "queue"},
                                          dedup=f"job.blocked:{job_id}:{_qe._now()}")

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

    def _stage_gcode(self, job_id: int, source_key: str, original_filename: str) -> str:
        """Copy a pre-sliced upload (.gcode or .gcode.3mf), read from the library store by `source_key`, into the job's
        gcode dir (where slicer output would have gone), keeping its full extension — the printer tells the two apart by it.
        The staging dir is the job's local working copy (intentionally local, docs/architecture/artifact-storage.md G1/C3).
        A missing/unreadable source raises OSError, which the callers already treat as "could not read the gcode"."""
        out_dir = self._slicer._data_dir / "gcode" / str(job_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = presliced_suffix(original_filename)
        base = os.path.basename(original_filename)
        stem = base[: -len(suffix)] if base.lower().endswith(suffix) else os.path.splitext(base)[0]
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "model"
        dest = out_dir / f"{safe}_j{job_id}{suffix}"
        try:
            with _qe.get_library_store().open(source_key) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
        except ArtifactError as exc:
            dest.unlink(missing_ok=True)
            raise OSError(f"could not read {source_key!r} from the library: {exc}") from exc
        return str(dest)

    async def _handle_slice_failure(self, job_id: int, printer_id: int, error: str) -> None:
        # A slicing issue blocks the job (per queue policy). This printer's config
        # is marked failed so it won't retry; another compatible printer can still
        # rescue it on a later check (its config isn't failed, so it re-evaluates).
        # A job cancelled meanwhile stays cancelled and its config unmarked (the cancel won the race).
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            if job is not None and job.status == "cancelled":
                return
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

            if job:
                job.status = "cancelled" if job.pool_group_id is not None else "blocked"   # pool plate: parts back to the pool
                job.block_reason = f"slicing failed: {error}"
                job.assigned_printer_id = None
                job.updated_at = _qe._now()
            await session.commit()

        await self._broadcast_job(job_id)
        await self._fire_webhooks(job_id, "job.blocked")
        await self._fire_notifications(job_id, "job.blocked", printer_id=printer_id, reason=error)
        await self._publish_job_event("job.blocked", job_id, printer_id, "core", {"reason": f"slicing failed: {error}", "phase": "queue"},
                                      dedup=f"job.blocked:{job_id}:{_qe._now()}")

    async def _fail_job_post_slice(self, job_id: int, printer_id: int, reason: str | None = None) -> None:
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            if job:
                # Nothing printed: a pool plate is cancelled instead, so its parts go back into the pool (BIZ-362).
                job.status = "cancelled" if job.pool_group_id is not None else "failed"
                job.completed_at = _qe._now()
                job.block_reason = reason
                job.assigned_printer_id = None
                job.updated_at = _qe._now()
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
