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
from ..models import Job, JobPrinterConfig, Printer, SlicedVersion, UploadedFile
from . import gcode_eligibility, slice_cache
from .providers.slicing import get_format_provider, get_slicing_provider
from .library_scanner import LibraryScanner, folder_of, library_abs_path, sha256_file

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')   # path separators + characters Windows refuses in a filename
_MAX_NAME_BYTES = 180   # leaves room for " (NN).gcode.3mf" under the usual 255-byte filename limit
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_display_name(name: str) -> str:
    """A user/derived display name made safe as a filename stem (the version's display name IS its filename)."""
    cleaned = _UNSAFE.sub(" ", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    encoded = cleaned.encode("utf-8")[:_MAX_NAME_BYTES]
    cleaned = encoded.decode("utf-8", errors="ignore").rstrip(" .")
    if cleaned.split(".")[0].lower() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned or "sliced"


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


async def _find_duplicate(
    session: AsyncSession, source_id: int, key: str, current: slice_cache.SlicerFingerprint,
) -> SlicedVersion | None:
    """A present version of this model with the same key that is NOT stale against what the slicer produces now. A
    stale one isn't a duplicate: the fresh slice is saved alongside it, and the newest same-key version wins lookups."""
    rows = (await session.execute(
        select(SlicedVersion)
        .join(UploadedFile, UploadedFile.id == SlicedVersion.file_id)
        .where(SlicedVersion.source_file_id == source_id, SlicedVersion.cache_key == key,
               UploadedFile.missing.is_(False))
        .order_by(SlicedVersion.id.desc())
    )).scalars().all()
    return next((v for v in rows
                 if slice_cache.staleness(v.preset_content_hash, v.slicer_version, current)[0] is not True), None)


async def _record(session: AsyncSession, job_id: int, outcome: str, **kw) -> None:
    """Store the save outcome on the job. Best-effort: a DB error here is logged, never raised."""
    try:
        job = await session.get(Job, job_id)
        if job is not None:
            job.slice_cache_info = slice_cache.with_save_outcome(job.slice_cache_info, outcome, **kw)
            await session.commit()
    except Exception:
        logger.exception("could not record the slice save outcome on job %s", job_id)


def _copy_exclusive(src: str, folder: Path, filename: str) -> Path:
    """Copy `src` into `folder` under `filename` (or "name (2)…" on collision), never overwriting: the destination is
    created exclusively, so two concurrent saves can't pick — and clobber — the same file."""
    for _ in range(1000):
        dest = LibraryScanner.unique_path(folder, filename)
        try:
            with open(dest, "xb") as out, open(src, "rb") as inp:
                shutil.copyfileobj(inp, out)
            return dest
        except FileExistsError:
            continue
    raise FileExistsError(f"no free name for {filename!r} in {folder}")


# One save at a time: the duplicate check, the copy and the insert must not interleave between the engine's background
# save and a save-slice request, or two same-key versions (or a version over another's bytes) could result.
_save_lock = asyncio.Lock()


async def save_slice_version(
    session: AsyncSession, *, job_id: int, printer_id: int, artifact_path: str,
    inputs: slice_cache.CacheKeyInputs | None, uncacheable_reason: str = "the source file has no content hash",
    require_flag: bool = False,
) -> SlicedVersion | None:
    """Copy `artifact_path` next to the job's source model and register it as a cached version. Returns the new (or
    already existing, same-key) version, or None on failure. Never raises. `inputs` None = uncacheable source.
    `require_flag`: skip quietly if the job's save flag was turned off meanwhile (the engine's background save)."""
    if inputs is None:
        slice_cache.log_event("save_failed", job_id=job_id, printer_id=printer_id, cache_key=None,
                              error=uncacheable_reason)
        await _record(session, job_id, "failed", cache_key=None, error=uncacheable_reason)
        return None
    key = slice_cache.cache_key(inputs)
    fields = {"job_id": job_id, "printer_id": printer_id, "cache_key": key, **slice_cache.key_fields(inputs)}
    async with _save_lock:
        return await _save_locked(session, job_id, printer_id, artifact_path, inputs, key, fields, require_flag)


async def _save_locked(session, job_id, printer_id, artifact_path, inputs, key, fields, require_flag):
    dest: Path | None = None
    thumb_dir: Path | None = None
    done = False
    try:
        job = await session.get(Job, job_id)
        source = await session.get(UploadedFile, job.uploaded_file_id) if job else None
        if job is None or source is None:
            raise LookupError("the job or its source file no longer exists")
        if require_flag and not job.save_slice:
            done = True   # turned off after slicing: the pending save is cancelled, nothing to record
            return None

        cfg = (await session.execute(select(JobPrinterConfig).where(
            JobPrinterConfig.job_id == job_id, JobPrinterConfig.printer_id == printer_id))).scalar_one_or_none()
        filament_type = cfg.filament_type if cfg else "any"
        filament_color = cfg.filament_color if cfg else "any"
        display = safe_display_name(job.save_slice_name or default_display_name(source, inputs, filament_type))
        suffix = ".gcode.3mf" if inputs.artifact_kind == "gcode_3mf" else ".gcode"

        # Copy FIRST: the job's artifact is short-lived (a failed upload or a cancel deletes it), and everything below
        # (sidecar fingerprint, hashing, parsing) can take seconds.
        library = config.get_library_dir()
        folder_abs = library_abs_path(library, source.relative_path).parent
        folder_abs.mkdir(parents=True, exist_ok=True)
        dest = await asyncio.to_thread(_copy_exclusive, artifact_path, folder_abs, f"{display}{suffix}")

        fingerprint = await asyncio.to_thread(   # uncached: this is what the version will record as "sliced with"
            slice_cache.current_fingerprint, inputs.machine_preset, inputs.process_preset,
            list(inputs.filament_presets), get_slicing_provider())
        existing = await _find_duplicate(session, source.id, key, fingerprint)
        if existing is not None:   # an up-to-date same-key version is already there: drop the copy
            _cleanup(dest, None)
            dest = None
            done = True
            slice_cache.log_event("save_duplicate_skipped", **fields, sliced_version_id=existing.id,
                                  cached_file_id=existing.file_id)
            await _record(session, job_id, "duplicate", cache_key=key, sliced_version_id=existing.id,
                          file_id=existing.file_id)
            return existing
        digest = await asyncio.to_thread(sha256_file, dest)
        stat = dest.stat()

        grams, secs, _ = await asyncio.to_thread(
            get_format_provider().parse_estimates, str(dest), inputs.plate_number)
        rel = dest.relative_to(library).as_posix()
        record = UploadedFile(
            original_filename=dest.name, relative_path=rel, folder=folder_of(rel), size_bytes=stat.st_size,
            content_hash=digest, mtime=stat.st_mtime, plates=[], missing=False, uploaded_at=slice_cache.now_iso(),
        )
        session.add(record)
        await session.flush()
        scanner = LibraryScanner(session, library, config.get_filecache_dir())
        thumb_dir = Path(config.get_filecache_dir()) / str(record.id)
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
        # The saved slice is eligible for the model it was sliced for plus that model's registry-declared equivalents (BIZ-263);
        # a printer with no registered model leaves it unknown rather than guessed.
        target = await session.get(Printer, printer_id)
        await gcode_eligibility.record_slice_target(session, record, target.model_uuid if target else None)
        job.slice_cache_info = slice_cache.with_save_outcome(
            job.slice_cache_info, "saved", cache_key=key, sliced_version_id=version.id, file_id=record.id)
        await session.commit()
        done = True
        slice_cache.log_event("saved", **fields, sliced_version_id=version.id, cached_file_id=record.id,
                              cached_file_hash=digest, path=rel,
                              preset_content_hash_stored=fingerprint.preset_content_hash,
                              slicer_version_stored=fingerprint.slicer_version)
        return version
    except Exception as exc:
        slice_cache.log_event("save_failed", **fields, error=f"{type(exc).__name__}: {exc}")
        logger.debug("slice save failed for job %s", job_id, exc_info=True)
        _cleanup(dest, thumb_dir)
        dest = None
        done = True
        try:
            await session.rollback()
        except Exception:
            logger.exception("rollback after a failed slice save (job %s) failed", job_id)
        await _record(session, job_id, "failed", cache_key=key, error=str(exc) or type(exc).__name__)
        return None
    finally:
        if not done:   # cancelled (shutdown) mid-save: leave no unregistered file behind
            _cleanup(dest, thumb_dir)


def _cleanup(dest: Path | None, thumb_dir: Path | None) -> None:
    if dest is not None:
        try:
            os.remove(dest)
        except OSError:
            pass
    if thumb_dir is not None:
        shutil.rmtree(thumb_dir, ignore_errors=True)
