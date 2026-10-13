from __future__ import annotations
import base64
import binascii
import asyncio
import re
import tempfile

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import UploadedFile, Job
from .artifact_store import (
    ArtifactError, ArtifactInfo, ArtifactStore, InvalidArtifactKey, LocalDiskArtifactStore,
)
from .providers.slicing import get_format_provider
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


def content_changed(info: ArtifactInfo, row_hash: str, row_size: int, row_mtime: float) -> bool:
    """Has the stored artifact changed since the indexed row was written? The change token is the backend's own: a content
    hash on `info` when it supplies one (a remote listing's checksum/etag, compared to the indexed hash), otherwise size +
    mtime (local disk, where hashing is a full read). An unhashed row (empty `row_hash`) is always changed (BIZ-286)."""
    if not row_hash:
        return True
    if info.sha256 is not None:
        return info.sha256 != row_hash
    return info.size != row_size or info.mtime != row_mtime


def fresh_content_hash(store: ArtifactStore, key: str, row_hash: str, row_size: int, row_mtime: float):
    """`(hash, size, mtime)` of the artifact as it is in the store NOW — re-hashed only when `content_changed` says it moved
    on from the indexed row (the index is refreshed by rescans, not a watcher, so a file overwritten in place keeps a stale
    hash until then). None if the artifact is gone or unreadable. Blocking: call via asyncio.to_thread."""
    try:
        info = store.stat(key)
        if not content_changed(info, row_hash, row_size, row_mtime):
            return row_hash, row_size, row_mtime
        return (info.sha256 or store.sha256(key)), info.size, info.mtime
    except ArtifactError:   # absent, bad key, backend I/O: same as the file being gone
        return None


async def refresh_content_hash(row, store: ArtifactStore) -> bool:
    """Bring `row`'s content_hash/size/mtime up to date with the stored artifact (caller commits). True if it changed."""
    fresh = await asyncio.to_thread(
        fresh_content_hash, store, row.relative_path, row.content_hash, row.size_bytes, row.mtime)
    if fresh is None or fresh == (row.content_hash, row.size_bytes, row.mtime):
        return False
    row.content_hash, row.size_bytes, row.mtime = fresh
    return True


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


def name_candidates(filename: str) -> Iterator[str]:
    """`filename`, then `stem (2).ext`, `stem (3).ext`, ... (endless). The collision policy shared by path- and store-based callers."""
    yield filename
    # A sliced archive's suffix is the whole `.gcode.3mf`: splitting at the last dot would give "x (2).3mf" —
    # a name that reads back as a sliceable model (BIZ-190).
    if file_kind(filename) == "gcode_3mf":
        suffix = filename[-len(SLICED_ARCHIVE_SUFFIX):]
        stem = filename[: -len(SLICED_ARCHIVE_SUFFIX)]
    else:
        stem, suffix = Path(filename).stem, Path(filename).suffix
    n = 2
    while True:
        yield f"{stem} ({n}){suffix}"
        n += 1


class LibraryScanner:
    """Indexes the library into `uploaded_files`. Reads bytes only through the library `ArtifactStore` (list/stat/sha256) and
    its parsers through `materialize`; thumbnails are published to the filecache store (BIZ-286). By default the stores are
    the local-disk ones under `library_dir` / `filecache_dir`; tests (and a future backend) may inject others."""

    def __init__(self, session: AsyncSession, library_dir: Path, filecache_dir: Path, *,
                 library_store: ArtifactStore | None = None, filecache_store: ArtifactStore | None = None):
        self.session = session
        self.library_dir = Path(library_dir)
        self.filecache_dir = Path(filecache_dir)
        self.library_store = library_store or LocalDiskArtifactStore(self.library_dir, prune_empty=False)
        self.filecache_store = filecache_store or LocalDiskArtifactStore(self.filecache_dir)

    # ---- path helpers ----
    @staticmethod
    def unique_path(folder_abs: Path, filename: str) -> Path:
        for name in name_candidates(filename):
            candidate = folder_abs / name
            if not candidate.exists():
                return candidate
        raise AssertionError("unreachable")   # name_candidates never ends

    # ---- plate/thumbnail caching ----
    def parse_plates(self, key: str, file_id: int) -> list[dict]:
        """Plates of the library artifact `key`. The parsers read a path, so the artifact is materialized; the thumbnails they
        extract land in a scratch dir and are published to the filecache as `<file_id>/thumbnails/<name>`, and each plate's
        `thumbnail_path` is that filecache **key** (None when the plate has no PNG). Blocking: call from a worker thread."""
        with tempfile.TemporaryDirectory(prefix="themis-thumbs-") as scratch, self.library_store.materialize(key) as local:
            plates_raw = self._parse_local(Path(local), Path(scratch))
            plates = []
            for p in plates_raw:
                thumb = None
                if p.thumbnail_path:
                    thumb = f"{file_id}/thumbnails/{Path(p.thumbnail_path).name}"
                    self.filecache_store.put_file(thumb, p.thumbnail_path, overwrite=True)
                plates.append({"plate_number": p.plate_number, "thumbnail_path": thumb,
                               "estimated_time": p.estimated_time, "filament_g": p.filament_g})
            return plates

    @staticmethod
    def _parse_local(abs_path: Path, thumb_dir: Path) -> list[PlateInfo]:
        kind = file_kind(abs_path.name)
        if kind == "gcode_3mf":
            return parse_sliced_archive(str(abs_path), thumbnail_dir=str(thumb_dir))
        if kind == "3mf":
            return parse_three_mf(str(abs_path), thumbnail_dir=str(thumb_dir))
        if kind == "gcode":
            est = get_format_provider().parse_estimates(str(abs_path))
            grams, secs = est.grams, est.seconds
            thumb = extract_gcode_thumbnail(abs_path, thumb_dir / "plate_1.png")
            return [PlateInfo(plate_number=1, thumbnail_path=str(thumb) if thumb else None,
                              estimated_time=secs or 0, filament_g=grams or 0.0)]
        return [PlateInfo(plate_number=1, thumbnail_path=None, estimated_time=0, filament_g=0.0)]

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

    def _unlisted_but_present(self, rel: str) -> bool:
        """True for a row whose file exists although the store did not list it. `list()` leaves out names the store refuses
        (backslash keys, symlinks leaving the root), and those must not be deleted or flagged missing just because the
        store cannot serve them. Only local disk has such files; a backend that cannot stat the key reports absent."""
        try:
            return self.library_store.exists(rel)
        except InvalidArtifactKey:
            return (self.library_dir / rel).is_file()

    # ---- the scan ----
    async def scan(self) -> dict:
        summary = {"added": 0, "moved": 0, "removed": 0, "missing": 0}
        rows = (await self.session.execute(select(UploadedFile))).scalars().all()
        by_path = {r.relative_path: r for r in rows}
        by_hash = {r.content_hash: r for r in rows if r.content_hash}
        store = self.library_store

        seen_paths: set[str] = set()
        listing = await asyncio.to_thread(lambda: [i for i in store.list() if Path(i.key).suffix.lower() in MODEL_EXTS])
        for info in listing:
            rel = info.key
            seen_paths.add(rel)
            row = by_path.get(rel)

            if row is not None:
                # Known path. Re-hash + re-parse only if it changed in the store.
                if content_changed(info, row.content_hash, row.size_bytes, row.mtime):
                    new_hash = info.sha256 or await asyncio.to_thread(store.sha256, rel)
                    if row.content_hash and new_hash != row.content_hash:
                        await self._detach_edited_version(row, new_hash)
                    row.content_hash = new_hash
                    row.size_bytes = info.size
                    row.mtime = info.mtime
                    row.plates = await asyncio.to_thread(self.parse_plates, rel, row.id)
                row.missing = False
                continue

            # New path — could be a move (same hash at a different path).
            digest = info.sha256 or await asyncio.to_thread(store.sha256, rel)
            moved = by_hash.get(digest)
            if moved is not None and moved.relative_path not in seen_paths \
                    and not await asyncio.to_thread(store.exists, moved.relative_path):
                by_path.pop(moved.relative_path, None)  # drop stale key so reconcile won't delete the moved row
                moved.relative_path = rel
                moved.folder = folder_of(rel)
                moved.size_bytes = info.size
                moved.mtime = info.mtime
                moved.missing = False
                by_path[rel] = moved
                summary["moved"] += 1
                continue

            # Genuinely new file.
            record = UploadedFile(
                original_filename=Path(rel).name,
                relative_path=rel,
                folder=folder_of(rel),
                size_bytes=info.size,
                content_hash=digest,
                mtime=info.mtime,
                plates=[],
                missing=False,
                uploaded_at=datetime.now(timezone.utc).isoformat(),
            )
            self.session.add(record)
            await self.session.flush()  # assign id for thumbnail dir
            record.plates = await asyncio.to_thread(self.parse_plates, rel, record.id)
            by_path[rel] = record
            by_hash[digest] = record
            summary["added"] += 1

        # Reconcile vanished files.
        for rel, row in list(by_path.items()):
            if rel in seen_paths:
                continue
            if await asyncio.to_thread(self._unlisted_but_present, rel):
                continue   # readable on disk but not a listable key (e.g. a symlink out of the root): keep the row as it is
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
