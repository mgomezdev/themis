from __future__ import annotations
import base64
import binascii
import re

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import UploadedFile, Job
from .three_mf_parser import parse_sliced_archive, parse_three_mf, PlateInfo

MODEL_EXTS = {".3mf", ".stl", ".gcode"}
SLICED_ARCHIVE_SUFFIX = ".gcode.3mf"   # Bambu's sliced archive: ends in .3mf but is NOT a sliceable model (BIZ-190)


def file_kind(name: str | None) -> str:
    """`gcode_3mf` (sliced archive), `gcode`, `stl` or `3mf`. The sliced-archive check must come before the
    plain `.3mf` one — it shares the suffix."""
    lower = (name or "").lower()
    if lower.endswith(SLICED_ARCHIVE_SUFFIX):
        return "gcode_3mf"
    if lower.endswith(".gcode"):
        return "gcode"
    if lower.endswith(".stl"):
        return "stl"
    return "3mf"


def is_presliced_name(name: str | None) -> bool:
    return file_kind(name) in ("gcode", "gcode_3mf")


def presliced_suffix(name: str | None) -> str:
    """The full extension of a pre-sliced file (`.gcode.3mf` or `.gcode`), for naming copies of it."""
    return SLICED_ARCHIVE_SUFFIX if file_kind(name) == "gcode_3mf" else ".gcode"


def is_presliced_file(uploaded_file) -> bool:
    """A pre-sliced file (.gcode or a .gcode.3mf sliced archive): jobs on it skip slicing and are printed as-is
    (BIZ-188, BIZ-190)."""
    return bool(uploaded_file) and is_presliced_name(uploaded_file.original_filename)
# Statuses where a job still needs its source file present.
ACTIVE_JOB_STATUSES = {"queued", "slicing", "uploading", "printing", "paused", "blocked"}


def sha256_file(path: Path) -> str:
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def folder_of(relative_path: str) -> str:
    parent = Path(relative_path).parent.as_posix()
    return "/" if parent == "." else "/" + parent


def fresh_content_hash(abs_path: Path, row_hash: str, row_size: int, row_mtime: float):
    """`(hash, size, mtime)` of the file as it is on disk NOW — re-hashed only when its size/mtime differ from the
    indexed row (the index is refreshed by rescans, not a watcher, so a file overwritten in place keeps a stale hash
    until then). None if the file is gone. Blocking: call via asyncio.to_thread."""
    try:
        st = abs_path.stat()
    except OSError:
        return None
    if row_hash and st.st_size == row_size and st.st_mtime == row_mtime:
        return row_hash, row_size, row_mtime
    return sha256_file(abs_path), st.st_size, st.st_mtime


async def refresh_content_hash(row, library_dir: Path) -> bool:
    """Bring `row`'s content_hash/size/mtime up to date with the file on disk (caller commits). True if it changed."""
    import asyncio
    fresh = await asyncio.to_thread(
        fresh_content_hash, library_abs_path(library_dir, row.relative_path), row.content_hash, row.size_bytes,
        row.mtime)
    if fresh is None or fresh == (row.content_hash, row.size_bytes, row.mtime):
        return False
    row.content_hash, row.size_bytes, row.mtime = fresh
    return True
_THUMB_START = re.compile(rb"^; (?:thumbnail|THUMBNAIL_BLOCK_START|thumbnail_PNG) begin (\d+)x(\d+)", re.M)


def extract_gcode_thumbnail(path: Path, dest: Path, head_bytes: int = 2_000_000) -> Path | None:
    """The largest PNG preview OrcaSlicer/PrusaSlicer embed in a gcode header (`; thumbnail begin WxH LEN` … base64 …
    `; thumbnail end`), written to `dest`; None if the file has none."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(head_bytes)
    except OSError:
        return None
    best: tuple[int, bytes] | None = None
    for m in re.finditer(rb"^; thumbnail begin (\d+)x(\d+) \d+\s*$(.*?)^; thumbnail end", head, re.M | re.S):
        data = b"".join(line.lstrip(b"; ").strip() for line in m.group(3).splitlines())
        try:
            png = base64.b64decode(data, validate=False)
        except (ValueError, binascii.Error):
            continue
        area = int(m.group(1)) * int(m.group(2))
        if png.startswith(b"\x89PNG") and (best is None or area > best[0]):
            best = (area, png)
    if best is None:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(best[1])
    return dest


def library_abs_path(library_dir: Path, relative_path: str) -> Path:
    """Resolve a library-relative path to an absolute one under the *current* library
    root. Absolute paths are never persisted for library files — `library_dir` differs
    between local dev (repo `data/`) and the container (`/data`), so a path written by
    one would silently 404 read back in the other. `relative_path` is the only portable
    pointer; this is where it becomes a real filesystem path, fresh, every time."""
    return library_dir / relative_path


class LibraryScanner:
    def __init__(self, session: AsyncSession, library_dir: Path, filecache_dir: Path):
        self.session = session
        self.library_dir = Path(library_dir)
        self.filecache_dir = Path(filecache_dir)

    # ---- path helpers ----
    @staticmethod
    def unique_path(folder_abs: Path, filename: str) -> Path:
        candidate = folder_abs / filename
        if not candidate.exists():
            return candidate
        # A sliced archive's suffix is the whole `.gcode.3mf`: splitting at the last dot would give "x (2).3mf" —
        # a name that reads back as a sliceable model (BIZ-190).
        if file_kind(filename) == "gcode_3mf":
            suffix = filename[-len(SLICED_ARCHIVE_SUFFIX):]
            stem = filename[: -len(SLICED_ARCHIVE_SUFFIX)]
        else:
            stem, suffix = Path(filename).stem, Path(filename).suffix
        n = 2
        while True:
            candidate = folder_abs / f"{stem} ({n}){suffix}"
            if not candidate.exists():
                return candidate
            n += 1

    def _rel(self, abs_path: Path) -> str:
        return abs_path.relative_to(self.library_dir).as_posix()

    # ---- plate/thumbnail caching ----
    def _parse_plates(self, abs_path: Path, file_id: int) -> list[dict]:
        thumb_dir = self.filecache_dir / str(file_id) / "thumbnails"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        kind = file_kind(abs_path.name)
        if kind == "gcode_3mf":
            plates_raw = parse_sliced_archive(str(abs_path), thumbnail_dir=str(thumb_dir))
        elif kind == "3mf":
            plates_raw = parse_three_mf(str(abs_path), thumbnail_dir=str(thumb_dir))
        elif kind == "gcode":
            from .queue_engine import _parse_gcode_estimates   # lazy: queue_engine imports this module
            grams, secs, _ = _parse_gcode_estimates(str(abs_path))
            thumb = extract_gcode_thumbnail(abs_path, thumb_dir / "plate_1.png")
            plates_raw = [PlateInfo(plate_number=1, thumbnail_path=str(thumb) if thumb else None,
                                    estimated_time=secs or 0, filament_g=grams or 0.0)]
        else:
            plates_raw = [PlateInfo(plate_number=1, thumbnail_path=None, estimated_time=0, filament_g=0.0)]
        return [
            {"plate_number": p.plate_number, "thumbnail_path": p.thumbnail_path,
             "estimated_time": p.estimated_time, "filament_g": p.filament_g}
            for p in plates_raw
        ]

    async def _detach_edited_version(self, row, new_hash: str) -> None:
        """A cached gcode edited on disk is no longer what was sliced: drop its link to the model (it stays a plain
        library file) so it is never offered or reused as that model's version (BIZ-195)."""
        from ..models import SlicedVersion   # local: keep the module's import surface small
        from . import slice_cache
        version = (await self.session.execute(
            select(SlicedVersion).where(SlicedVersion.file_id == row.id))).scalar_one_or_none()
        if version is not None and version.source_file_id is not None:
            slice_cache.log_event("version_detached", sliced_version_id=version.id, cached_file_id=row.id,
                                  source_file_id=version.source_file_id, cached_file_hash=row.content_hash,
                                  new_file_hash=new_hash, reason="file_edited")
            version.source_file_id = None

    # ---- the scan ----
    async def scan(self) -> dict:
        summary = {"added": 0, "moved": 0, "removed": 0, "missing": 0}
        rows = (await self.session.execute(select(UploadedFile))).scalars().all()
        by_path = {r.relative_path: r for r in rows}
        by_hash = {r.content_hash: r for r in rows if r.content_hash}

        seen_paths: set[str] = set()
        for abs_path in sorted(self.library_dir.rglob("*")):
            if not abs_path.is_file() or abs_path.suffix.lower() not in MODEL_EXTS:
                continue
            rel = self._rel(abs_path)
            seen_paths.add(rel)
            stat = abs_path.stat()
            row = by_path.get(rel)

            if row is not None:
                # Known path. Re-hash + re-parse only if it changed on disk.
                if row.mtime != stat.st_mtime or row.size_bytes != stat.st_size:
                    new_hash = sha256_file(abs_path)
                    if row.content_hash and new_hash != row.content_hash:
                        await self._detach_edited_version(row, new_hash)
                    row.content_hash = new_hash
                    row.size_bytes = stat.st_size
                    row.mtime = stat.st_mtime
                    row.plates = self._parse_plates(abs_path, row.id)
                row.missing = False
                continue

            # New path — could be a move (same hash at a different path).
            digest = sha256_file(abs_path)
            moved = by_hash.get(digest)
            if moved is not None and moved.relative_path not in seen_paths \
                    and not (self.library_dir / moved.relative_path).exists():
                by_path.pop(moved.relative_path, None)  # drop stale key so reconcile won't delete the moved row
                moved.relative_path = rel
                moved.folder = folder_of(rel)
                moved.size_bytes = stat.st_size
                moved.mtime = stat.st_mtime
                moved.missing = False
                by_path[rel] = moved
                summary["moved"] += 1
                continue

            # Genuinely new file.
            record = UploadedFile(
                original_filename=abs_path.name,
                relative_path=rel,
                folder=folder_of(rel),
                size_bytes=stat.st_size,
                content_hash=digest,
                mtime=stat.st_mtime,
                plates=[],
                missing=False,
                uploaded_at=datetime.now(timezone.utc).isoformat(),
            )
            self.session.add(record)
            await self.session.flush()  # assign id for thumbnail dir
            record.plates = self._parse_plates(abs_path, record.id)
            by_path[rel] = record
            by_hash[digest] = record
            summary["added"] += 1

        # Reconcile vanished files.
        for rel, row in list(by_path.items()):
            if rel in seen_paths:
                continue
            any_job = (await self.session.execute(
                select(Job.id).where(Job.uploaded_file_id == row.id).limit(1)
            )).first()
            if any_job:
                row.missing = True
                summary["missing"] += 1
            else:
                await self.session.delete(row)
                summary["removed"] += 1

        await self.session.commit()
        return summary


async def migrate_legacy_uploads(session, data_dir, library_dir, filecache_dir) -> int:
    """One-time, idempotent: move pre-library uploads under <data>/uploads/<uuid>/
    into <library>/Job Uploads/ and backfill index columns. Returns count moved."""
    data_dir, library_dir = Path(data_dir), Path(library_dir)
    sentinel = library_dir / ".legacy_migrated"
    if sentinel.exists():
        return 0
    job_uploads = library_dir / "Job Uploads"
    job_uploads.mkdir(parents=True, exist_ok=True)
    moved = 0
    rows = (await session.execute(select(UploadedFile))).scalars().all()
    uploads_root = (data_dir / "uploads").resolve()
    for row in rows:
        if row.relative_path:  # already indexed/migrated
            continue
        src = Path(row.stored_path)
        try:
            inside_uploads = uploads_root in src.resolve().parents
        except OSError:
            inside_uploads = False
        if not (src.exists() and inside_uploads):
            continue
        dest = LibraryScanner.unique_path(job_uploads, row.original_filename or src.name)
        try:
            src.replace(dest)
        except OSError:
            continue
        rel = dest.relative_to(library_dir).as_posix()
        stat = dest.stat()
        row.stored_path = ""  # migrated: relative_path is now authoritative
        row.relative_path = rel
        row.folder = folder_of(rel)
        row.size_bytes = stat.st_size
        row.content_hash = sha256_file(dest)
        row.mtime = stat.st_mtime
        # Relocate any existing thumbnails into the filecache for this id.
        old_thumbs = src.parent / "thumbnails"
        if old_thumbs.is_dir():
            new_thumbs = Path(filecache_dir) / str(row.id) / "thumbnails"
            new_thumbs.mkdir(parents=True, exist_ok=True)
            for t in old_thumbs.glob("*"):
                try:
                    t.replace(new_thumbs / t.name)
                except OSError:
                    pass
        moved += 1
    await session.commit()
    sentinel.write_text("done")
    return moved
