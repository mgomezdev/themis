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
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
from .artifact_store import ArtifactStore
from .library_store import folder_key, key_name, get_filecache_store, get_library_store, put_file_unique
from ..models import Job, JobPrinterConfig, Printer, SlicedVersion, UploadedFile
from . import gcode_eligibility, slice_cache
from .providers.slicing import get_format_provider, get_slicing_provider
from .library_scanner import LibraryScanner, folder_of

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
    library = get_library_store()
    filecache = get_filecache_store()
    dest_key: str | None = None   # the library artifact this save created (removed again on failure)
    file_id: int | None = None    # its row id once flushed: names its filecache thumbnails
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
        # The artifact is the slicer's local output (scratch, intentionally local); it is published into the source model's
        # folder through the store, under the first free name (exclusive create: concurrent saves can't clobber each other).
        publish = asyncio.ensure_future(asyncio.to_thread(
            put_file_unique, library, artifact_path, folder_key(source.relative_path), f"{display}{suffix}"))
        try:
            published = await asyncio.shield(publish)
        except asyncio.CancelledError:
            # Cancelled (shutdown) mid-copy: the worker thread still finishes and publishes. Wait for it so the key is known
            # and the `finally` below removes it, instead of leaving an unregistered file in the library.
            try:
                dest_key = (await publish).key
            except Exception:
                logger.debug("slice copy failed while the save was being cancelled", exc_info=True)
            raise
        dest_key = published.key

        fingerprint = await asyncio.to_thread(   # uncached: this is what the version will record as "sliced with"
            slice_cache.current_fingerprint, inputs.machine_preset, inputs.process_preset,
            list(inputs.filament_presets), get_slicing_provider())
        existing = await _find_duplicate(session, source.id, key, fingerprint)
        if existing is not None:   # an up-to-date same-key version is already there: drop the copy
            _cleanup(library, filecache, dest_key, None)
            dest_key = None
            done = True
            slice_cache.log_event("save_duplicate_skipped", **fields, sliced_version_id=existing.id,
                                  cached_file_id=existing.file_id)
            await _record(session, job_id, "duplicate", cache_key=key, sliced_version_id=existing.id,
                          file_id=existing.file_id)
            return existing
        digest = published.sha256   # computed while the bytes streamed in: no second read
        est = await asyncio.to_thread(
            _parse_estimates, library, dest_key, inputs.plate_number)
        grams, secs = est.grams, est.seconds
        rel = dest_key
        record = UploadedFile(
            original_filename=key_name(rel), relative_path=rel, folder=folder_of(rel), size_bytes=published.size,
            content_hash=digest, mtime=published.mtime, plates=[], missing=False, uploaded_at=slice_cache.now_iso(),
        )
        session.add(record)
        await session.flush()
        file_id = record.id
        scanner = LibraryScanner(session, config.get_library_dir(), config.get_filecache_dir(),
                                 library_store=library, filecache_store=filecache)
        record.plates = await asyncio.to_thread(scanner.parse_plates, rel, record.id)
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
        _cleanup(library, filecache, dest_key, file_id)
        dest_key = None
        done = True
        try:
            await session.rollback()
        except Exception:
            logger.exception("rollback after a failed slice save (job %s) failed", job_id)
        await _record(session, job_id, "failed", cache_key=key, error=str(exc) or type(exc).__name__)
        return None
    finally:
        if not done:   # cancelled (shutdown) mid-save: leave no unregistered file behind
            _cleanup(library, filecache, dest_key, file_id)


def _parse_estimates(library: ArtifactStore, key: str, plate_number: int):
    """Grams / seconds of a library artifact: the format provider reads a path, so the artifact is materialized."""
    with library.materialize(key) as local:
        return get_format_provider().parse_estimates(str(local), plate_number)


def _cleanup(library: ArtifactStore, filecache: ArtifactStore, dest_key: str | None, file_id: int | None) -> None:
    """Undo a save that did not complete: drop the library artifact it created and the thumbnails published for its row.
    Best effort (a failure here must not mask the save's own outcome)."""
    if dest_key is not None:
        try:
            library.delete(dest_key, missing_ok=True)
        except Exception:
            logger.warning("could not remove the unregistered slice copy %s", dest_key, exc_info=True)
    if file_id is not None:
        try:
            filecache.delete_prefix(str(file_id))
        except Exception:
            logger.warning("could not remove the thumbnails of the abandoned slice save (file %s)", file_id, exc_info=True)
