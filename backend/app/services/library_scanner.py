from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import UploadedFile, Job
from .three_mf_parser import parse_three_mf, PlateInfo

MODEL_EXTS = {".3mf", ".stl"}
# Statuses where a job still needs its source file present.
ACTIVE_JOB_STATUSES = {"queued", "slicing", "uploading", "printing", "paused", "blocked"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def folder_of(relative_path: str) -> str:
    # "Customers/Vela/arm.stl" -> "/Customers/Vela"; root file -> "/".
    parent = str(Path(relative_path).parent).replace("\\", "/")
    if parent in (".", "", "/"):
        return "/"
    return "/" + parent.lstrip("/")


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
        if abs_path.suffix.lower() == ".3mf":
            plates_raw = parse_three_mf(str(abs_path), thumbnail_dir=str(thumb_dir))
        else:
            plates_raw = [PlateInfo(plate_number=1, thumbnail_path=None, estimated_time=0, filament_g=0.0)]
        return [
            {"plate_number": p.plate_number, "thumbnail_path": p.thumbnail_path,
             "estimated_time": p.estimated_time, "filament_g": p.filament_g}
            for p in plates_raw
        ]

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
                    row.content_hash = sha256_file(abs_path)
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
                moved.stored_path = str(abs_path)
                moved.size_bytes = stat.st_size
                moved.mtime = stat.st_mtime
                moved.missing = False
                by_path[rel] = moved
                summary["moved"] += 1
                continue

            # Genuinely new file.
            record = UploadedFile(
                original_filename=abs_path.name,
                stored_path=str(abs_path),
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
            referenced = (await self.session.execute(
                select(Job.id).where(Job.uploaded_file_id == row.id,
                                     Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1)
            )).first()
            any_job = (await self.session.execute(
                select(Job.id).where(Job.uploaded_file_id == row.id).limit(1)
            )).first()
            if referenced or any_job:
                row.missing = True
                summary["missing"] += 1
            else:
                await self.session.delete(row)
                summary["removed"] += 1

        await self.session.commit()
        return summary
