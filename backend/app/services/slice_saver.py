"""Save a production slice into the library as a cached version (BIZ-192).

The slicer's output lives in the job's private `<data>/gcode/<job_id>/` dir and is deleted when the job finishes, so a
version is a **copy** of it, written next to the source model as an ordinary library file (`uploaded_files` row) plus a
`sliced_versions` row recording what it was sliced from and with. Saving is best-effort: any failure is logged
(`slice_cache event=save_failed`) and recorded on the job, never raised — a job must never fail or block because its
gcode couldn't be kept.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
from ..models import Job, JobPrinterConfig, SlicedVersion, UploadedFile
from . import slice_cache
from .library_scanner import LibraryScanner, folder_of, library_abs_path, sha256_file

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')   # path separators + characters Windows refuses in a filename
_MAX_NAME = 180


def safe_display_name(name: str) -> str:
    """A user/derived display name made safe as a filename stem (the version's display name IS its filename)."""
    cleaned = _UNSAFE.sub(" ", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:_MAX_NAME].rstrip(" .") or "sliced"


def _model_stem(filename: str) -> str:
    return Path(filename or "model").stem or "model"


def default_display_name(source: UploadedFile, inputs: slice_cache.CacheKeyInputs, filament_type: str | None) -> str:
    """`<model> [- plate N] - <filament type> - <process> - <machine>` — enough to tell versions apart at a glance."""
    parts = [_model_stem(source.original_filename)]
    if len(source.plates or []) > 1:
        parts.append(f"plate {inputs.plate_number}")
    if filament_type and filament_type.lower() != "any":
        parts.append(filament_type)
    parts += [inputs.process_preset, inputs.machine_preset]
    return " - ".join(p for p in parts if p)


def inputs_from_dict(d: dict) -> slice_cache.CacheKeyInputs:
    return slice_cache.CacheKeyInputs(**{**d, "filament_presets": tuple(d.get("filament_presets") or ())})


async def _find_duplicate(session: AsyncSession, source_id: int, key: str) -> SlicedVersion | None:
    return (await session.execute(
        select(SlicedVersion)
        .join(UploadedFile, UploadedFile.id == SlicedVersion.file_id)
        .where(SlicedVersion.source_file_id == source_id, SlicedVersion.cache_key == key,
               UploadedFile.missing.is_(False))
        .order_by(SlicedVersion.id.desc()).limit(1)
    )).scalar_one_or_none()


async def _record(session: AsyncSession, job_id: int, outcome: str, **kw) -> None:
    job = await session.get(Job, job_id)
    if job is not None:
        job.slice_cache_info = slice_cache.with_save_outcome(job.slice_cache_info, outcome, **kw)
        await session.commit()


async def save_slice_version(
    session: AsyncSession, *, job_id: int, printer_id: int, artifact_path: str,
    inputs: slice_cache.CacheKeyInputs | None, uncacheable_reason: str = "the source file has no content hash",
) -> SlicedVersion | None:
    """Copy `artifact_path` next to the job's source model and register it as a cached version. Returns the new (or
    already existing, same-key) version, or None on failure. Never raises. `inputs` None = uncacheable source."""
    if inputs is None:
        slice_cache.log_event("save_failed", job_id=job_id, printer_id=printer_id, cache_key=None,
                              error=uncacheable_reason)
        await _record(session, job_id, "failed", cache_key=None, error=uncacheable_reason)
        return None
    key = slice_cache.cache_key(inputs)
    fields = {"job_id": job_id, "printer_id": printer_id, "cache_key": key, **slice_cache.key_fields(inputs)}
    dest: Path | None = None
    try:
        job = await session.get(Job, job_id)
        source = await session.get(UploadedFile, job.uploaded_file_id) if job else None
        if job is None or source is None:
            raise LookupError("the job or its source file no longer exists")

        existing = await _find_duplicate(session, source.id, key)
        if existing is not None:
            slice_cache.log_event("save_duplicate_skipped", **fields, sliced_version_id=existing.id,
                                  cached_file_id=existing.file_id)
            await _record(session, job_id, "duplicate", cache_key=key, sliced_version_id=existing.id,
                          file_id=existing.file_id)
            return existing

        cfg = (await session.execute(select(JobPrinterConfig).where(
            JobPrinterConfig.job_id == job_id, JobPrinterConfig.printer_id == printer_id))).scalar_one_or_none()
        filament_type = cfg.filament_type if cfg else "any"
        filament_color = cfg.filament_color if cfg else "any"
        display = safe_display_name(job.save_slice_name or default_display_name(source, inputs, filament_type))
        suffix = ".gcode.3mf" if inputs.artifact_kind == "gcode_3mf" else ".gcode"

        fingerprint = await asyncio.to_thread(
            slice_cache.current_fingerprint, inputs.machine_preset, inputs.process_preset,
            list(inputs.filament_presets), config.get_laminus_sidecar_url())

        library = config.get_library_dir()
        folder_abs = library_abs_path(library, source.relative_path).parent
        folder_abs.mkdir(parents=True, exist_ok=True)
        dest = LibraryScanner.unique_path(folder_abs, f"{display}{suffix}")
        await asyncio.to_thread(shutil.copyfile, artifact_path, dest)
        digest = await asyncio.to_thread(sha256_file, dest)
        stat = dest.stat()

        from .queue_engine import _parse_gcode_estimates   # lazy: queue_engine imports this module
        grams, secs, _ = await asyncio.to_thread(_parse_gcode_estimates, str(dest), inputs.plate_number)
        rel = dest.relative_to(library).as_posix()
        record = UploadedFile(
            original_filename=dest.name, relative_path=rel, folder=folder_of(rel), size_bytes=stat.st_size,
            content_hash=digest, mtime=stat.st_mtime, plates=[], missing=False, uploaded_at=slice_cache.now_iso(),
        )
        session.add(record)
        await session.flush()
        scanner = LibraryScanner(session, library, config.get_filecache_dir())
        record.plates = await asyncio.to_thread(scanner._parse_plates, dest, record.id)
        version = SlicedVersion(
            file_id=record.id, source_file_id=source.id, source_content_hash=inputs.source_content_hash,
            plate_number=inputs.plate_number, machine_preset=inputs.machine_preset,
            process_preset=inputs.process_preset, filament_presets=list(inputs.filament_presets),
            extra_config=inputs.extra_config, tool_index=inputs.tool_index, filament_map=inputs.filament_map,
            artifact_kind=inputs.artifact_kind, cache_key=key,
            preset_content_hash=fingerprint.preset_content_hash, slicer_version=fingerprint.slicer_version,
            filament_type=filament_type, filament_color=filament_color,
            estimated_seconds=secs, filament_grams=grams, filament_breakdown=job.actual_filament_breakdown,
            created_from_job_id=job_id, created_at=slice_cache.now_iso(),
        )
        session.add(version)
        await session.flush()
        job.slice_cache_info = slice_cache.with_save_outcome(
            job.slice_cache_info, "saved", cache_key=key, sliced_version_id=version.id, file_id=record.id)
        await session.commit()
        slice_cache.log_event("saved", **fields, sliced_version_id=version.id, cached_file_id=record.id,
                              cached_file_hash=digest, path=rel,
                              preset_content_hash_stored=fingerprint.preset_content_hash,
                              slicer_version_stored=fingerprint.slicer_version)
        return version
    except Exception as exc:
        await session.rollback()
        if dest is not None:
            try:
                os.remove(dest)
            except OSError:
                pass
        slice_cache.log_event("save_failed", **fields, error=f"{type(exc).__name__}: {exc}")
        logger.debug("slice save failed for job %s", job_id, exc_info=True)
        try:
            await _record(session, job_id, "failed", cache_key=key, error=str(exc) or type(exc).__name__)
        except Exception:
            logger.exception("could not record the failed slice save on job %s", job_id)
        return None
