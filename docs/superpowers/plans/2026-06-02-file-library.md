# File Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the mock Files screen into a real on-disk model library (filesystem = source of truth) with a DB tag overlay, a Manyfold placeholder tab, unified job uploads, and removal of the Themis-owned filament library.

**Architecture:** Model files live as real `.3mf`/`.stl` files under a configurable library root (`THEMIS_LIBRARY_DIR`, default `<data_dir>/library/`). SQLite holds only a thin index (`uploaded_files`, evolved) + an optional tag overlay (`tags`, `file_tags`). A `LibraryScanner` service reconciles disk ↔ index (detecting moves by content hash so tags follow). Derived thumbnails live outside the library in `<data_dir>/filecache/<id>/`.

**Tech Stack:** FastAPI + async SQLAlchemy 2.0 + aiosqlite (backend), React 18 + Vite + TypeScript (frontend), pytest-asyncio + Vitest (tests).

**Spec:** `docs/superpowers/specs/2026-06-02-file-library-design.md`

**Conventions reminders (this repo):**
- Backend tests: `cd backend && pytest -v` (use the python.org venv, not the Store Python). API tests use the `client` fixture in `backend/tests/conftest.py` (httpx + in-memory SQLite + `get_session` override). Service/unit tests live in `backend/tests/services/`.
- Frontend type-check is `cd frontend && npm run build` (`tsc -b`) — `tsc --noEmit` is a no-op here. Tests: `npx vitest run`.
- `HTTPException(<code>, "msg")` uses **positional** detail.
- No migration tool: new **tables** come from `Base.metadata.create_all`; new **columns on existing tables** need an idempotent guard in `database._migrate`.
- Commit after each task. Branch: `file-library`.

---

## File Structure

**Backend — create:**
- `backend/app/services/library_scanner.py` — disk↔index scan/reconcile, hashing, collision-safe paths, legacy migration.
- `backend/app/api/routes/tags.py` — tags CRUD router.
- `backend/tests/services/test_library_scanner.py`
- `backend/tests/api/test_tags.py`
- `backend/tests/api/test_files_library.py`

**Backend — modify:**
- `backend/app/config.py` — add `get_library_dir()`, `get_filecache_dir()` (robust data-dir resolution).
- `backend/app/models.py` — extend `UploadedFile`; add `Tag`, `FileTag`.
- `backend/app/database.py` — `_migrate` adds `uploaded_files` columns.
- `backend/app/api/routes/files.py` — expand to the full library API.
- `backend/app/main.py` — register `tags_router`; run legacy migration + startup scan in lifespan.

**Frontend — create:**
- `frontend/src/api/tags.ts`
- `frontend/src/api/files.ts`

**Frontend — modify:**
- `frontend/src/data/types.ts` — add `LibraryFile`, `Tag`, `FolderNode`.
- `frontend/src/screens/FilesScreen.tsx` — real data, tabs, detail panel, ops.
- `frontend/src/screens/NewJobScreen.tsx` — pick-from-library + save-to-location.
- `frontend/src/screens/SettingsScreen.tsx` — Tags tab → real API.
- `frontend/src/api/queue.ts` — `uploadFile(file, folder?)` gains optional folder.
- `frontend/src/App.tsx` — remove Filaments route + screenConfig entry.
- `frontend/src/components/Sidebar.tsx` — remove Filaments nav link.
- `frontend/src/data/mock.ts` — remove `FILAMENTS` (and `FILES`/`TAGS` once unused).

**Frontend — delete:**
- `frontend/src/screens/FilamentsScreen.tsx`

---

## Task 1: Config — library + filecache directories

**Files:**
- Modify: `backend/app/config.py`
- Test: `backend/tests/services/test_config_dirs.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_config_dirs.py
import importlib
from pathlib import Path


def test_library_dir_defaults_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("THEMIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("THEMIS_LIBRARY_DIR", raising=False)
    import app.config as config
    importlib.reload(config)
    assert config.get_library_dir() == tmp_path / "library"
    assert config.get_filecache_dir() == tmp_path / "filecache"


def test_library_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("THEMIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("THEMIS_LIBRARY_DIR", str(tmp_path / "models"))
    import app.config as config
    importlib.reload(config)
    assert config.get_library_dir() == tmp_path / "models"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/services/test_config_dirs.py -v`
Expected: FAIL with `AttributeError: module 'app.config' has no attribute 'get_library_dir'`

- [ ] **Step 3: Implement**

Add to `backend/app/config.py` (after `get_data_dir`):

```python
def _resolve_data_dir() -> Path:
    # Match database.py: explicit env, else <repo-root>/data (robust for local dev,
    # unlike get_data_dir()'s Docker-oriented /data default).
    env = os.environ.get("THEMIS_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent / "data"


def get_library_dir() -> Path:
    env = os.environ.get("THEMIS_LIBRARY_DIR")
    path = Path(env) if env else _resolve_data_dir() / "library"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_filecache_dir() -> Path:
    path = _resolve_data_dir() / "filecache"
    path.mkdir(parents=True, exist_ok=True)
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/services/test_config_dirs.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/services/test_config_dirs.py
git commit -m "feat(config): add library + filecache directory helpers"
```

---

## Task 2: Models — extend UploadedFile, add Tag + FileTag

**Files:**
- Modify: `backend/app/models.py:22-29`
- Test: `backend/tests/services/test_models_library.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_models_library.py
import pytest
from sqlalchemy import select
from app.database import Base
from app.models import UploadedFile, Tag, FileTag
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


@pytest.mark.asyncio
async def test_file_tag_relationship():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as s:
        f = UploadedFile(
            original_filename="a.3mf", stored_path="/lib/a.3mf",
            relative_path="a.3mf", folder="/", size_bytes=10,
            content_hash="abc", mtime=1.0, plates=[], uploaded_at="t",
        )
        t = Tag(name="PLA", color="#22c55e", category="Material")
        s.add_all([f, t])
        await s.flush()
        s.add(FileTag(file_id=f.id, tag_id=t.id))
        await s.commit()
        rows = (await s.execute(select(FileTag))).scalars().all()
        assert len(rows) == 1
        assert rows[0].file_id == f.id and rows[0].tag_id == t.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/services/test_models_library.py -v`
Expected: FAIL — `ImportError: cannot import name 'Tag'` (or `TypeError` on unknown `relative_path`).

- [ ] **Step 3: Implement**

Replace `UploadedFile` in `backend/app/models.py:22-29` and add the two new models below it:

```python
class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024))
    plates: Mapped[list] = mapped_column(JSON, default=list)
    uploaded_at: Mapped[str] = mapped_column(String(32))
    # Library index fields (filesystem is the source of truth; these cache it).
    relative_path: Mapped[str] = mapped_column(String(1024), default="")
    folder: Mapped[str] = mapped_column(String(1024), default="/")
    size_bytes: Mapped[int] = mapped_column(default=0)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    mtime: Mapped[float] = mapped_column(Float, default=0.0)
    missing: Mapped[bool] = mapped_column(Boolean, default=False)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    color: Mapped[str] = mapped_column(String(20), default="#64748b")
    category: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class FileTag(Base):
    __tablename__ = "file_tags"

    file_id: Mapped[int] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/services/test_models_library.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/tests/services/test_models_library.py
git commit -m "feat(models): extend UploadedFile + add Tag/FileTag"
```

---

## Task 3: Migration — add uploaded_files columns

**Files:**
- Modify: `backend/app/database.py:33-53`
- Test: `backend/tests/services/test_migrate_library.py` (create)

Note: `tags` and `file_tags` are **new tables** → created by `create_all`, no `_migrate` needed. Only the new **columns** on the existing `uploaded_files` table need guards.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_migrate_library.py
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_migrate_adds_uploaded_files_columns():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    # Simulate a pre-existing legacy table without the new columns.
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE uploaded_files (id INTEGER PRIMARY KEY, "
            "original_filename VARCHAR, stored_path VARCHAR, plates JSON, uploaded_at VARCHAR)"
        ))
        from app.database import _migrate
        await _migrate(conn)
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(uploaded_files)"))).fetchall()}
    for c in ("relative_path", "folder", "size_bytes", "content_hash", "mtime", "missing"):
        assert c in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/services/test_migrate_library.py -v`
Expected: FAIL — assertion error, `relative_path` not in cols.

- [ ] **Step 3: Implement**

Append to `_migrate` in `backend/app/database.py` (after the `jobs` block, before the function ends):

```python
    uf_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(uploaded_files)"))).fetchall()}
    if "relative_path" not in uf_cols:
        await conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN relative_path VARCHAR(1024) DEFAULT ''"))
    if "folder" not in uf_cols:
        await conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN folder VARCHAR(1024) DEFAULT '/'"))
    if "size_bytes" not in uf_cols:
        await conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN size_bytes INTEGER DEFAULT 0"))
    if "content_hash" not in uf_cols:
        await conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN content_hash VARCHAR(64) DEFAULT ''"))
    if "mtime" not in uf_cols:
        await conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN mtime FLOAT DEFAULT 0"))
    if "missing" not in uf_cols:
        await conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN missing BOOLEAN NOT NULL DEFAULT 0"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/services/test_migrate_library.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/database.py backend/tests/services/test_migrate_library.py
git commit -m "feat(db): migrate uploaded_files with library index columns"
```

---

## Task 4: LibraryScanner service

**Files:**
- Create: `backend/app/services/library_scanner.py`
- Test: `backend/tests/services/test_library_scanner.py`

This service owns all disk reasoning: hashing, collision-safe paths, scan/reconcile, and the helper to (re)parse plate thumbnails into the filecache. It takes an `AsyncSession` and the library/filecache roots as injected paths so tests can use `tmp_path`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_library_scanner.py
import pytest
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.database import Base
from app.models import UploadedFile, Tag, FileTag
from app.services.library_scanner import LibraryScanner


async def _session(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _write(root: Path, rel: str, content: bytes = b"solid\nendsolid\n"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


@pytest.mark.asyncio
async def test_scan_indexes_new_files(tmp_path):
    lib, cache = tmp_path / "library", tmp_path / "filecache"
    lib.mkdir(); cache.mkdir()
    _write(lib, "Customers/Vela/arm.stl")
    Session = await _session(tmp_path)
    async with Session() as s:
        scanner = LibraryScanner(s, lib, cache)
        summary = await scanner.scan()
        assert summary["added"] == 1
        rows = (await s.execute(select(UploadedFile))).scalars().all()
        assert len(rows) == 1
        assert rows[0].relative_path == "Customers/Vela/arm.stl"
        assert rows[0].folder == "/Customers/Vela"
        assert rows[0].content_hash != ""


@pytest.mark.asyncio
async def test_scan_is_idempotent(tmp_path):
    lib, cache = tmp_path / "library", tmp_path / "filecache"
    lib.mkdir(); cache.mkdir()
    _write(lib, "a.stl")
    Session = await _session(tmp_path)
    async with Session() as s:
        scanner = LibraryScanner(s, lib, cache)
        await scanner.scan()
        summary2 = await scanner.scan()
        assert summary2["added"] == 0
        rows = (await s.execute(select(UploadedFile))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_scan_detects_move_and_preserves_tags(tmp_path):
    lib, cache = tmp_path / "library", tmp_path / "filecache"
    lib.mkdir(); cache.mkdir()
    _write(lib, "old/part.stl", b"unique-bytes-123")
    Session = await _session(tmp_path)
    async with Session() as s:
        scanner = LibraryScanner(s, lib, cache)
        await scanner.scan()
        f = (await s.execute(select(UploadedFile))).scalars().one()
        t = Tag(name="structural", color="#fff", category="Purpose")
        s.add(t); await s.flush()
        s.add(FileTag(file_id=f.id, tag_id=t.id)); await s.commit()
        # Move the file on disk (same bytes => same hash).
        (lib / "old" / "part.stl").rename(_write_target(lib, "new/part.stl"))
        summary = await scanner.scan()
        assert summary["moved"] == 1
        f2 = (await s.execute(select(UploadedFile))).scalars().one()
        assert f2.id == f.id
        assert f2.relative_path == "new/part.stl"
        links = (await s.execute(select(FileTag))).scalars().all()
        assert len(links) == 1 and links[0].file_id == f.id


def _write_target(root: Path, rel: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@pytest.mark.asyncio
async def test_scan_marks_missing_when_job_references(tmp_path, monkeypatch):
    lib, cache = tmp_path / "library", tmp_path / "filecache"
    lib.mkdir(); cache.mkdir()
    _write(lib, "x.stl")
    Session = await _session(tmp_path)
    async with Session() as s:
        scanner = LibraryScanner(s, lib, cache)
        await scanner.scan()
        f = (await s.execute(select(UploadedFile))).scalars().one()
        from app.models import Job
        s.add(Job(uploaded_file_id=f.id, plate_number=1, status="printing",
                  created_at="t", updated_at="t"))
        await s.commit()
        (lib / "x.stl").unlink()
        await scanner.scan()
        f2 = await s.get(UploadedFile, f.id)
        assert f2 is not None and f2.missing is True


@pytest.mark.asyncio
async def test_scan_deletes_unreferenced_vanished(tmp_path):
    lib, cache = tmp_path / "library", tmp_path / "filecache"
    lib.mkdir(); cache.mkdir()
    _write(lib, "y.stl")
    Session = await _session(tmp_path)
    async with Session() as s:
        scanner = LibraryScanner(s, lib, cache)
        await scanner.scan()
        (lib / "y.stl").unlink()
        summary = await scanner.scan()
        assert summary["removed"] == 1
        rows = (await s.execute(select(UploadedFile))).scalars().all()
        assert rows == []


def test_unique_path_suffixes(tmp_path):
    (tmp_path / "a.stl").write_bytes(b"x")
    assert LibraryScanner.unique_path(tmp_path, "a.stl").name == "a (2).stl"
    (tmp_path / "a (2).stl").write_bytes(b"x")
    assert LibraryScanner.unique_path(tmp_path, "a.stl").name == "a (3).stl"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/services/test_library_scanner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.library_scanner'`

- [ ] **Step 3: Implement**

```python
# backend/app/services/library_scanner.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/services/test_library_scanner.py -v`
Expected: PASS (7 passed). If `parse_three_mf` import path differs, confirm against `backend/app/api/routes/files.py:13`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/library_scanner.py backend/tests/services/test_library_scanner.py
git commit -m "feat(library): add LibraryScanner disk-index reconciler"
```

---

## Task 5: Tags routes

**Files:**
- Create: `backend/app/api/routes/tags.py`
- Modify: `backend/app/main.py:10-17,65-72` (import + register)
- Test: `backend/tests/api/test_tags.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_tags.py
import pytest


@pytest.mark.asyncio
async def test_tags_crud(client):
    r = await client.post("/api/v1/tags", json={"name": "PLA", "color": "#22c55e", "category": "Material"})
    assert r.status_code == 201, r.text
    tag = r.json()
    assert tag["name"] == "PLA" and tag["usage_count"] == 0

    r = await client.get("/api/v1/tags")
    assert r.status_code == 200
    assert any(t["name"] == "PLA" for t in r.json())

    r = await client.patch(f"/api/v1/tags/{tag['id']}", json={"color": "#000000"})
    assert r.status_code == 200 and r.json()["color"] == "#000000"

    r = await client.delete(f"/api/v1/tags/{tag['id']}")
    assert r.status_code == 200
    r = await client.get("/api/v1/tags")
    assert all(t["name"] != "PLA" for t in r.json())


@pytest.mark.asyncio
async def test_duplicate_tag_name_409(client):
    await client.post("/api/v1/tags", json={"name": "PETG", "color": "#fff", "category": ""})
    r = await client.post("/api/v1/tags", json={"name": "PETG", "color": "#000", "category": ""})
    assert r.status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/api/test_tags.py -v`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Implement**

```python
# backend/app/api/routes/tags.py
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Tag, FileTag

router = APIRouter(prefix="/api/v1/tags", tags=["tags"])


class TagCreate(BaseModel):
    name: str
    color: str = "#64748b"
    category: str = ""


class TagPatch(BaseModel):
    name: str | None = None
    color: str | None = None
    category: str | None = None


async def _usage_counts(session: AsyncSession) -> dict[int, int]:
    rows = (await session.execute(
        select(FileTag.tag_id, func.count()).group_by(FileTag.tag_id)
    )).all()
    return {tag_id: n for tag_id, n in rows}


def _to_dict(t: Tag, usage: int) -> dict:
    return {"id": t.id, "name": t.name, "color": t.color,
            "category": t.category, "usage_count": usage}


@router.get("")
async def list_tags(session: AsyncSession = Depends(get_session)) -> list[dict]:
    usage = await _usage_counts(session)
    tags = (await session.execute(select(Tag).order_by(Tag.category, Tag.name))).scalars().all()
    return [_to_dict(t, usage.get(t.id, 0)) for t in tags]


@router.post("", status_code=201)
async def create_tag(body: TagCreate, session: AsyncSession = Depends(get_session)) -> dict:
    existing = (await session.execute(select(Tag).where(Tag.name == body.name))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, f"Tag {body.name!r} already exists")
    tag = Tag(name=body.name, color=body.color, category=body.category,
              created_at=datetime.now(timezone.utc).isoformat())
    session.add(tag)
    await session.commit()
    await session.refresh(tag)
    return _to_dict(tag, 0)


@router.patch("/{tag_id}")
async def update_tag(tag_id: int, body: TagPatch, session: AsyncSession = Depends(get_session)) -> dict:
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(404, f"Tag {tag_id} not found")
    if body.name is not None and body.name != tag.name:
        dup = (await session.execute(select(Tag).where(Tag.name == body.name))).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(409, f"Tag {body.name!r} already exists")
        tag.name = body.name
    if body.color is not None:
        tag.color = body.color
    if body.category is not None:
        tag.category = body.category
    await session.commit()
    usage = (await _usage_counts(session)).get(tag.id, 0)
    return _to_dict(tag, usage)


@router.delete("/{tag_id}")
async def delete_tag(tag_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(404, f"Tag {tag_id} not found")
    # Explicit cascade (SQLite FK cascade is not enforced by default here).
    for link in (await session.execute(select(FileTag).where(FileTag.tag_id == tag_id))).scalars().all():
        await session.delete(link)
    await session.delete(tag)
    await session.commit()
    return {"deleted": tag_id}
```

In `backend/app/main.py`, add the import next to the other route imports (`:10-17`):

```python
from .api.routes.tags import router as tags_router
```

and register it alongside the others (`:65-72`):

```python
app.include_router(tags_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/api/test_tags.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/tags.py backend/app/main.py backend/tests/api/test_tags.py
git commit -m "feat(api): tags CRUD router"
```

---

## Task 6: Files routes — full library API

**Files:**
- Modify: `backend/app/api/routes/files.py` (expand)
- Test: `backend/tests/api/test_files_library.py`

The `client` fixture's library/filecache dirs must point at a temp location for tests. Add a fixture that monkeypatches `config.get_library_dir`/`get_filecache_dir` to `tmp_path` subdirs (see Step 1). The routes resolve the library dir via `config.get_library_dir()` at call time (never cache it at import), so the monkeypatch takes effect.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_files_library.py
import io
import pytest
from app import config


@pytest.fixture
def lib(tmp_path, monkeypatch):
    library = tmp_path / "library"; library.mkdir()
    cache = tmp_path / "filecache"; cache.mkdir()
    monkeypatch.setattr(config, "get_library_dir", lambda: library)
    monkeypatch.setattr(config, "get_filecache_dir", lambda: cache)
    return library


def _stl(name="part.stl"):
    return {"file": (name, io.BytesIO(b"solid x\nendsolid x\n"), "application/octet-stream")}


@pytest.mark.asyncio
async def test_upload_lands_in_default_folder(client, lib):
    r = await client.post("/api/v1/files/upload", files=_stl())
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["folder"] == "/Job Uploads"
    assert (lib / "Job Uploads" / "part.stl").is_file()


@pytest.mark.asyncio
async def test_upload_to_named_folder_and_list(client, lib):
    await client.post("/api/v1/files/upload", data={"folder": "/Customers/Vela"}, files=_stl("arm.stl"))
    r = await client.get("/api/v1/files", params={"folder": "/Customers/Vela"})
    assert r.status_code == 200
    names = [f["original_filename"] for f in r.json()]
    assert "arm.stl" in names


@pytest.mark.asyncio
async def test_tag_assign_filter(client, lib):
    up = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    tag = (await client.post("/api/v1/tags", json={"name": "PLA", "color": "#fff", "category": "Material"})).json()
    r = await client.post(f"/api/v1/files/{up['id']}/tags", json={"tag_id": tag["id"]})
    assert r.status_code == 200
    r = await client.get("/api/v1/files", params={"tags": ["PLA"]})
    assert any(f["id"] == up["id"] for f in r.json())
    assert up["id"] in [f["id"] for f in r.json()]


@pytest.mark.asyncio
async def test_rename_move_keeps_tags(client, lib):
    up = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    tag = (await client.post("/api/v1/tags", json={"name": "x", "color": "#fff", "category": ""})).json()
    await client.post(f"/api/v1/files/{up['id']}/tags", json={"tag_id": tag["id"]})
    r = await client.patch(f"/api/v1/files/{up['id']}", json={"folder": "/Archive", "name": "renamed.stl"})
    assert r.status_code == 200, r.text
    assert (lib / "Archive" / "renamed.stl").is_file()
    assert not (lib / "Job Uploads" / "a.stl").exists()
    r = await client.get("/api/v1/files", params={"tags": ["x"]})
    assert up["id"] in [f["id"] for f in r.json()]


@pytest.mark.asyncio
async def test_delete_blocked_by_active_job(client, lib):
    up = (await client.post("/api/v1/files/upload", files=_stl("a.stl"))).json()
    # Seed an active job referencing the file via the get_session override.
    from app.main import app
    from app.database import get_session
    from app.models import Job
    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    session.add(Job(uploaded_file_id=up["id"], plate_number=1, status="printing",
                    created_at="t", updated_at="t"))
    await session.commit()
    r = await client.delete(f"/api/v1/files/{up['id']}")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_create_folder_and_tree(client, lib):
    r = await client.post("/api/v1/files/folders", json={"path": "/Customers/New"})
    assert r.status_code == 201
    assert (lib / "Customers" / "New").is_dir()
    r = await client.get("/api/v1/files/tree")
    assert r.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/api/test_files_library.py -v`
Expected: FAIL — upload returns no `folder` key / 404 on `/files` list.

- [ ] **Step 3: Implement**

Replace `backend/app/api/routes/files.py` entirely:

```python
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ... import config
from ...database import get_session
from ...models import UploadedFile, Tag, FileTag, Job
from ...services.library_scanner import (
    LibraryScanner, folder_of, sha256_file, ACTIVE_JOB_STATUSES, MODEL_EXTS,
)

router = APIRouter(prefix="/api/v1/files", tags=["files"])


# ---------- helpers ----------

def _safe_subpath(root: Path, relative: str) -> Path:
    """Resolve `relative` under `root`, rejecting traversal outside it."""
    target = (root / relative.strip("/\\")).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise HTTPException(400, "Path escapes the library root")
    return target


async def _tags_for(session: AsyncSession, file_ids: list[int]) -> dict[int, list[dict]]:
    if not file_ids:
        return {}
    rows = (await session.execute(
        select(FileTag.file_id, Tag).join(Tag, Tag.id == FileTag.tag_id)
        .where(FileTag.file_id.in_(file_ids))
    )).all()
    out: dict[int, list[dict]] = {}
    for file_id, tag in rows:
        out.setdefault(file_id, []).append(
            {"id": tag.id, "name": tag.name, "color": tag.color, "category": tag.category})
    return out


def _to_dict(f: UploadedFile, tags: list[dict]) -> dict:
    return {
        "id": f.id,
        "original_filename": f.original_filename,
        "relative_path": f.relative_path,
        "folder": f.folder,
        "size_bytes": f.size_bytes,
        "plate_count": len(f.plates or []),
        "uploaded_at": f.uploaded_at,
        "missing": f.missing,
        "tags": tags,
        "thumbnail_url": _thumb_url(f),
    }


def _thumb_url(f: UploadedFile) -> str | None:
    for p in (f.plates or []):
        tp = p.get("thumbnail_path")
        if tp:
            return f"/api/v1/files/{f.id}/thumbnails/{Path(tp).name}"
    return None


# ---------- list / tree ----------

@router.get("")
async def list_files(
    folder: str | None = None,
    tags: list[str] | None = None,
    search: str | None = None,
    sort: str = "updated",
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (await session.execute(select(UploadedFile))).scalars().all()
    if folder:
        rows = [r for r in rows if r.folder == folder or r.folder.startswith(folder.rstrip("/") + "/")]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in (r.original_filename or "").lower()]
    tag_map = await _tags_for(session, [r.id for r in rows])
    if tags:
        wanted = set(tags)
        rows = [r for r in rows if wanted.issubset({t["name"] for t in tag_map.get(r.id, [])})]
    if sort == "name":
        rows.sort(key=lambda r: (r.original_filename or "").lower())
    elif sort == "size":
        rows.sort(key=lambda r: r.size_bytes, reverse=True)
    else:
        rows.sort(key=lambda r: r.uploaded_at, reverse=True)
    return [_to_dict(r, tag_map.get(r.id, [])) for r in rows]


@router.get("/tree")
async def folder_tree(session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(select(UploadedFile))).scalars().all()
    root: dict = {"name": "All files", "path": "", "count": 0, "children": {}}
    for r in rows:
        root["count"] += 1
        node = root
        path = ""
        for part in [p for p in r.folder.split("/") if p]:
            path += "/" + part
            node = node["children"].setdefault(
                part, {"name": part, "path": path, "count": 0, "children": {}})
            node["count"] += 1
    return root


# ---------- upload ----------

@router.post("/upload", status_code=201)
async def upload_file(
    file: UploadFile,
    folder: str = Form("/Job Uploads"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    fname = (file.filename or "")
    ext = Path(fname).suffix.lower()
    if ext not in MODEL_EXTS:
        raise HTTPException(422, "Only .3mf and .stl files are accepted")

    library = config.get_library_dir()
    folder_abs = _safe_subpath(library, folder)
    folder_abs.mkdir(parents=True, exist_ok=True)
    dest = LibraryScanner.unique_path(folder_abs, Path(fname).name)
    dest.write_bytes(await file.read())

    rel = dest.relative_to(library).as_posix()
    stat = dest.stat()
    scanner = LibraryScanner(session, library, config.get_filecache_dir())
    record = UploadedFile(
        original_filename=dest.name, stored_path=str(dest), relative_path=rel,
        folder=folder_of(rel), size_bytes=stat.st_size, content_hash=sha256_file(dest),
        mtime=stat.st_mtime, plates=[], missing=False,
        uploaded_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(record)
    await session.flush()
    record.plates = scanner._parse_plates(dest, record.id)
    await session.commit()
    await session.refresh(record)
    return _to_dict(record, [])


# ---------- folders ----------

class FolderCreate(BaseModel):
    path: str


@router.post("/folders", status_code=201)
async def create_folder(body: FolderCreate) -> dict:
    library = config.get_library_dir()
    target = _safe_subpath(library, body.path)
    target.mkdir(parents=True, exist_ok=True)
    return {"path": "/" + target.relative_to(library).as_posix()}


# ---------- rename / move ----------

class FilePatch(BaseModel):
    name: str | None = None
    folder: str | None = None


@router.patch("/{file_id}")
async def update_file(file_id: int, body: FilePatch,
                      session: AsyncSession = Depends(get_session)) -> dict:
    f = await session.get(UploadedFile, file_id)
    if f is None:
        raise HTTPException(404, f"File {file_id} not found")
    library = config.get_library_dir()
    src = Path(f.stored_path)
    new_folder = body.folder if body.folder is not None else f.folder
    new_name = body.name if body.name is not None else f.original_filename
    folder_abs = _safe_subpath(library, new_folder)
    folder_abs.mkdir(parents=True, exist_ok=True)
    dest = LibraryScanner.unique_path(folder_abs, new_name)
    if src.exists():
        src.replace(dest)
    rel = dest.relative_to(library).as_posix()
    f.original_filename = dest.name
    f.stored_path = str(dest)
    f.relative_path = rel
    f.folder = folder_of(rel)
    await session.commit()
    tag_map = await _tags_for(session, [f.id])
    return _to_dict(f, tag_map.get(f.id, []))


# ---------- delete ----------

@router.delete("/{file_id}")
async def delete_file(file_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    f = await session.get(UploadedFile, file_id)
    if f is None:
        raise HTTPException(404, f"File {file_id} not found")
    active = (await session.execute(
        select(Job.id).where(Job.uploaded_file_id == file_id,
                             Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1)
    )).first()
    if active:
        raise HTTPException(409, "File is referenced by an active job")
    p = Path(f.stored_path)
    if p.exists():
        p.unlink()
    cache = config.get_filecache_dir() / str(file_id)
    if cache.exists():
        import shutil
        shutil.rmtree(cache, ignore_errors=True)
    for link in (await session.execute(select(FileTag).where(FileTag.file_id == file_id))).scalars().all():
        await session.delete(link)
    await session.delete(f)
    await session.commit()
    return {"deleted": file_id}


# ---------- tag assign / unassign ----------

class TagAssign(BaseModel):
    tag_id: int


@router.post("/{file_id}/tags")
async def add_file_tag(file_id: int, body: TagAssign,
                       session: AsyncSession = Depends(get_session)) -> dict:
    if await session.get(UploadedFile, file_id) is None:
        raise HTTPException(404, f"File {file_id} not found")
    if await session.get(Tag, body.tag_id) is None:
        raise HTTPException(404, f"Tag {body.tag_id} not found")
    existing = await session.get(FileTag, {"file_id": file_id, "tag_id": body.tag_id})
    if existing is None:
        session.add(FileTag(file_id=file_id, tag_id=body.tag_id))
        await session.commit()
    return {"file_id": file_id, "tag_id": body.tag_id}


@router.delete("/{file_id}/tags/{tag_id}")
async def remove_file_tag(file_id: int, tag_id: int,
                          session: AsyncSession = Depends(get_session)) -> dict:
    link = await session.get(FileTag, {"file_id": file_id, "tag_id": tag_id})
    if link is not None:
        await session.delete(link)
        await session.commit()
    return {"file_id": file_id, "tag_id": tag_id}


# ---------- rescan ----------

@router.post("/rescan")
async def rescan(session: AsyncSession = Depends(get_session)) -> dict:
    scanner = LibraryScanner(session, config.get_library_dir(), config.get_filecache_dir())
    return await scanner.scan()


# ---------- plates / thumbnails ----------

@router.get("/{file_id}/plates")
async def get_plates(file_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    record = await session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File {file_id} not found")
    return record.plates or []


@router.get("/{file_id}/thumbnails/{filename}")
async def get_thumbnail(file_id: int, filename: str,
                        session: AsyncSession = Depends(get_session)) -> FileResponse:
    if await session.get(UploadedFile, file_id) is None:
        raise HTTPException(404, f"File {file_id} not found")
    thumb_dir = (config.get_filecache_dir() / str(file_id) / "thumbnails").resolve()
    try:
        thumb_path = (thumb_dir / filename).resolve()
        thumb_path.relative_to(thumb_dir)
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    if not thumb_path.exists():
        raise HTTPException(404, "Thumbnail not found")
    return FileResponse(str(thumb_path), media_type="image/png")
```

> Note: `FileTag` composite-PK `session.get(FileTag, {"file_id": .., "tag_id": ..})` requires the mapped attribute-name keys — confirm SQLAlchemy accepts the dict form; if not, query with `select(FileTag).where(...)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/api/test_files_library.py -v`
Expected: PASS (6 passed). Then run the full backend suite to catch regressions in the old upload contract (`jobs`, `override_inspector` use `UploadedFile.stored_path`): `cd backend && pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/files.py backend/tests/api/test_files_library.py
git commit -m "feat(api): full file-library routes (list/tree/upload/folders/move/delete/tags/rescan)"
```

---

## Task 7: Startup scan + legacy upload migration

**Files:**
- Modify: `backend/app/services/library_scanner.py` (add `migrate_legacy_uploads`)
- Modify: `backend/app/main.py:28-39` (lifespan: run migration + scan)
- Test: `backend/tests/services/test_legacy_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_legacy_migration.py
import pytest
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.database import Base
from app.models import UploadedFile
from app.services.library_scanner import migrate_legacy_uploads


@pytest.mark.asyncio
async def test_legacy_upload_moved_into_job_uploads(tmp_path):
    data = tmp_path; library = data / "library"; cache = data / "filecache"
    library.mkdir(); cache.mkdir()
    legacy = data / "uploads" / "uuid-1"
    legacy.mkdir(parents=True)
    (legacy / "model.stl").write_bytes(b"solid\nendsolid\n")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as s:
        s.add(UploadedFile(original_filename="model.stl",
                           stored_path=str(legacy / "model.stl"),
                           plates=[], uploaded_at="t"))
        await s.commit()
        moved = await migrate_legacy_uploads(s, data, library, cache)
        assert moved == 1
        assert (library / "Job Uploads" / "model.stl").is_file()
        row = (await s.execute(select(UploadedFile))).scalars().one()
        assert row.relative_path == "Job Uploads/model.stl"
        # Idempotent second run does nothing.
        assert await migrate_legacy_uploads(s, data, library, cache) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/services/test_legacy_migration.py -v`
Expected: FAIL — `ImportError: cannot import name 'migrate_legacy_uploads'`

- [ ] **Step 3: Implement**

Add to `backend/app/services/library_scanner.py`:

```python
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
        row.stored_path = str(dest)
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
```

In `backend/app/main.py`, inside `lifespan` right after `await init_db()` (`:30`), add:

```python
    # File library: migrate legacy uploads, then index the library dir.
    from . import config as _config
    from .services.library_scanner import LibraryScanner, migrate_legacy_uploads
    async with SessionLocal() as _s:
        await migrate_legacy_uploads(
            _s, _config._resolve_data_dir(), _config.get_library_dir(), _config.get_filecache_dir())
        await LibraryScanner(_s, _config.get_library_dir(), _config.get_filecache_dir()).scan()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/services/test_legacy_migration.py -v`
Expected: PASS. Then full suite: `cd backend && pytest -q` (all green).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/library_scanner.py backend/app/main.py backend/tests/services/test_legacy_migration.py
git commit -m "feat(library): legacy-upload migration + startup scan"
```

---

## Task 8: Frontend API client — tags

**Files:**
- Create: `frontend/src/api/tags.ts`
- Test: `frontend/src/api/tags.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/api/tags.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getTags, createTag } from './tags';

beforeEach(() => vi.restoreAllMocks());

describe('tags api', () => {
  it('getTags fetches the list', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify([{ id: 1, name: 'PLA', color: '#fff', category: 'Material', usage_count: 2 }]),
        { status: 200 })));
    const tags = await getTags();
    expect(tags[0].name).toBe('PLA');
    expect(tags[0].usage_count).toBe(2);
  });

  it('createTag posts JSON', async () => {
    const f = vi.fn(async () =>
      new Response(JSON.stringify({ id: 9, name: 'PETG', color: '#0af', category: '', usage_count: 0 }),
        { status: 201 }));
    vi.stubGlobal('fetch', f);
    const t = await createTag({ name: 'PETG', color: '#0af', category: '' });
    expect(t.id).toBe(9);
    expect(f).toHaveBeenCalledWith('/api/v1/tags', expect.objectContaining({ method: 'POST' }));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/api/tags.test.ts`
Expected: FAIL — cannot resolve `./tags`.

- [ ] **Step 3: Implement**

```ts
// frontend/src/api/tags.ts
import { useCallback, useEffect, useState } from 'react';

export interface Tag {
  id: number;
  name: string;
  color: string;
  category: string;
  usage_count: number;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await (init ? fetch(url, init) : fetch(url));
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export const getTags = () => request<Tag[]>('/api/v1/tags');
export const createTag = (b: { name: string; color: string; category: string }) =>
  request<Tag>('/api/v1/tags', jsonInit('POST', b));
export const updateTag = (id: number, b: Partial<Pick<Tag, 'name' | 'color' | 'category'>>) =>
  request<Tag>(`/api/v1/tags/${id}`, jsonInit('PATCH', b));
export const deleteTag = (id: number) =>
  request<{ deleted: number }>(`/api/v1/tags/${id}`, { method: 'DELETE' });

export function useTags(): { tags: Tag[]; refetch: () => void } {
  const [tags, setTags] = useState<Tag[]>([]);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick(t => t + 1), []);
  useEffect(() => {
    let alive = true;
    getTags().then(d => { if (alive) setTags(d); }).catch(console.error);
    return () => { alive = false; };
  }, [tick]);
  return { tags, refetch };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/api/tags.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/tags.ts frontend/src/api/tags.test.ts
git commit -m "feat(fe/api): tags client + useTags hook"
```

---

## Task 9: Frontend API client — files

**Files:**
- Create: `frontend/src/api/files.ts`
- Modify: `frontend/src/data/types.ts` (add `LibraryFile`, `FolderNode`)
- Test: `frontend/src/api/files.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/api/files.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getFiles, uploadLibraryFile } from './files';

beforeEach(() => vi.restoreAllMocks());

describe('files api', () => {
  it('getFiles builds the query string', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify([]), { status: 200 }));
    vi.stubGlobal('fetch', f);
    await getFiles({ folder: '/Customers', tags: ['PLA', 'structural'], sort: 'name' });
    const url = f.mock.calls[0][0] as string;
    expect(url).toContain('folder=%2FCustomers');
    expect(url).toContain('tags=PLA');
    expect(url).toContain('tags=structural');
    expect(url).toContain('sort=name');
  });

  it('uploadLibraryFile posts FormData with folder', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ id: 1 }), { status: 201 }));
    vi.stubGlobal('fetch', f);
    await uploadLibraryFile(new File(['x'], 'a.stl'), '/Customers/Vela');
    const [, init] = f.mock.calls[0];
    expect((init as RequestInit).method).toBe('POST');
    expect((init as RequestInit).body).toBeInstanceOf(FormData);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/api/files.test.ts`
Expected: FAIL — cannot resolve `./files`.

- [ ] **Step 3: Implement**

Add to `frontend/src/data/types.ts`:

```ts
export interface LibraryFile {
  id: number;
  original_filename: string;
  relative_path: string;
  folder: string;
  size_bytes: number;
  plate_count: number;
  uploaded_at: string;
  missing: boolean;
  tags: { id: number; name: string; color: string; category: string }[];
  thumbnail_url: string | null;
}

export interface FolderNode {
  name: string;
  path: string;
  count: number;
  children: Record<string, FolderNode>;
}
```

Create `frontend/src/api/files.ts`:

```ts
import { useCallback, useEffect, useState } from 'react';
import type { LibraryFile, FolderNode } from '../data/types';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await (init ? fetch(url, init) : fetch(url));
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export interface FileFilter { folder?: string; tags?: string[]; search?: string; sort?: string; }

export function getFiles(filter: FileFilter = {}): Promise<LibraryFile[]> {
  const q = new URLSearchParams();
  if (filter.folder) q.set('folder', filter.folder);
  if (filter.search) q.set('search', filter.search);
  if (filter.sort) q.set('sort', filter.sort);
  for (const t of filter.tags ?? []) q.append('tags', t);
  const qs = q.toString();
  return request<LibraryFile[]>(`/api/v1/files${qs ? `?${qs}` : ''}`);
}

export const getFolderTree = () => request<FolderNode>('/api/v1/files/tree');

export async function uploadLibraryFile(file: File, folder?: string): Promise<LibraryFile> {
  const body = new FormData();
  body.append('file', file);
  if (folder) body.append('folder', folder);
  return request<LibraryFile>('/api/v1/files/upload', { method: 'POST', body });
}

export const createFolder = (path: string) =>
  request<{ path: string }>('/api/v1/files/folders', jsonInit('POST', { path }));
export const updateFile = (id: number, b: { name?: string; folder?: string }) =>
  request<LibraryFile>(`/api/v1/files/${id}`, jsonInit('PATCH', b));
export const deleteFile = (id: number) =>
  request<{ deleted: number }>(`/api/v1/files/${id}`, { method: 'DELETE' });
export const addFileTag = (id: number, tagId: number) =>
  request<unknown>(`/api/v1/files/${id}/tags`, jsonInit('POST', { tag_id: tagId }));
export const removeFileTag = (id: number, tagId: number) =>
  request<unknown>(`/api/v1/files/${id}/tags/${tagId}`, { method: 'DELETE' });
export const rescanLibrary = () =>
  request<{ added: number; moved: number; removed: number; missing: number }>(
    '/api/v1/files/rescan', { method: 'POST' });
export const fileThumbnailUrl = (f: LibraryFile) => f.thumbnail_url ?? undefined;

export function useFiles(filter: FileFilter): { files: LibraryFile[]; refetch: () => void } {
  const [files, setFiles] = useState<LibraryFile[]>([]);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick(t => t + 1), []);
  const key = JSON.stringify(filter);
  useEffect(() => {
    let alive = true;
    getFiles(JSON.parse(key)).then(d => { if (alive) setFiles(d); }).catch(console.error);
    return () => { alive = false; };
  }, [key, tick]);
  return { files, refetch };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/api/files.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/files.ts frontend/src/data/types.ts frontend/src/api/files.test.ts
git commit -m "feat(fe/api): file-library client + useFiles hook + types"
```

---

## Task 10: FilesScreen — real data, tabs, detail panel, operations

**Files:**
- Modify: `frontend/src/screens/FilesScreen.tsx` (replace mock with API; add tabs + detail panel + ops)
- Test: `frontend/src/screens/FilesScreen.test.tsx` (create)

The existing `FilesScreen.tsx` already contains the presentational pieces to reuse: `buildFolderTree`, `FolderTreeNode`, `FolderCard`, `FilterCard`, and the file-grid markup. Keep them, but:
- Source files from `useFiles(filter)`; source tag facets from `useTags()` (group facet chips by tag `category`, replacing the hard-coded `TAG_GROUPS`).
- The folder tree may still be built client-side from `files[].folder` via the existing `buildFolderTree` (feed real `LibraryFile[]` — note field is `folder`, and the grid uses `original_filename`, `size_bytes`, `plate_count`, `thumbnail_url`, `tags[].name`). Remove `data/mock` + `FileEntry` imports.
- Add a tab bar `[Library] [Manyfold]`. Manyfold tab renders a placeholder card.
- Add a file **detail panel** (right side or modal) opened on card click: thumbnail, name, folder, size, plate count, **tag chips with add/remove** (from `useTags`), **Rename**, **Move** (folder text/select), **Delete** (confirm), and **Use in new job** → `navigate('/queue/new', { state: { libraryFileId: f.id } })`.
- Toolbar: **Upload** (file input → `uploadLibraryFile(file, currentFolder)` then `refetch`), **New folder** (`createFolder`), **Rescan** (`rescanLibrary` then `refetch`), plus the existing sort select.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/screens/FilesScreen.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { FilesScreen } from './FilesScreen';

const FILES = [
  { id: 1, original_filename: 'arm.3mf', relative_path: 'Customers/Vela/arm.3mf',
    folder: '/Customers/Vela', size_bytes: 4200000, plate_count: 1, uploaded_at: '2026-06-01',
    missing: false, tags: [{ id: 1, name: 'PLA', color: '#fff', category: 'Material' }], thumbnail_url: null },
];
const TAGS = [{ id: 1, name: 'PLA', color: '#fff', category: 'Material', usage_count: 1 }];

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.startsWith('/api/v1/tags')) return new Response(JSON.stringify(TAGS), { status: 200 });
    if (url.startsWith('/api/v1/files/tree')) return new Response(JSON.stringify(
      { name: 'All files', path: '', count: 1, children: {} }), { status: 200 });
    if (url.startsWith('/api/v1/files')) return new Response(JSON.stringify(FILES), { status: 200 });
    return new Response('[]', { status: 200 });
  }));
});

describe('FilesScreen', () => {
  it('renders files from the API', async () => {
    render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('arm.3mf')).toBeInTheDocument());
  });

  it('shows the Manyfold placeholder when that tab is selected', async () => {
    render(<MemoryRouter><FilesScreen /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: /Manyfold/i }));
    await waitFor(() => expect(screen.getByText(/coming soon/i)).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/screens/FilesScreen.test.tsx`
Expected: FAIL — current screen reads mock `FILES`, has no Manyfold tab; `arm.3mf` may render from mock but the Manyfold test fails, and the API is not called.

- [ ] **Step 3: Implement**

Rewrite `frontend/src/screens/FilesScreen.tsx`. Replace the mock import and `FilesScreen` component; keep `FolderIcon`, `FolderTreeNode`, `FolderCard`, `FilterCard` (adapt `FilterCard` to take grouped facets from tags). Key wiring (full component; reuse existing sub-components above it):

```tsx
import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { shade } from '../data/helpers';
import { Icons } from '../components/icons';
import { Empty } from '../components/ui';
import type { LibraryFile, FolderNode } from '../data/types';
import { useFiles, uploadLibraryFile, createFolder, updateFile, deleteFile,
         addFileTag, removeFileTag, rescanLibrary } from '../api/files';
import { useTags } from '../api/tags';

// (keep buildFolderTree but typed to LibraryFile; FolderIcon/FolderTreeNode/FolderCard unchanged)

export function FilesScreen() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<'library' | 'manyfold'>('library');
  const [currentFolder, setCurrentFolder] = useState('');
  const [activeTags, setActiveTags] = useState<string[]>([]);
  const [sort, setSort] = useState('updated');
  const [selected, setSelected] = useState<LibraryFile | null>(null);
  const [openFolders, setOpenFolders] = useState<Set<string>>(new Set());
  const [folderExpanded, setFolderExpanded] = useState(true);
  const [filterExpanded, setFilterExpanded] = useState(true);

  const filter = useMemo(() => ({
    folder: currentFolder || undefined,
    tags: activeTags.length ? activeTags : undefined,
    sort,
  }), [currentFolder, activeTags, sort]);
  const { files, refetch } = useFiles(filter);
  const { tags } = useTags();

  const tree = useMemo(() => buildFolderTree(files), [files]);
  const tagCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const f of files) for (const t of f.tags) c[t.name] = (c[t.name] ?? 0) + 1;
    return c;
  }, [files]);
  // facet groups derived from the real tag catalog, grouped by category
  const facetGroups = useMemo(() => {
    const g: Record<string, string[]> = {};
    for (const t of tags) (g[t.category || 'Other'] ||= []).push(t.name);
    return Object.entries(g).map(([label, names]) => ({ label, tags: names }));
  }, [tags]);

  const toggleTag = (t: string) =>
    setActiveTags(activeTags.includes(t) ? activeTags.filter(x => x !== t) : [...activeTags, t]);
  const toggleFolder = (p: string) => {
    const n = new Set(openFolders); n.has(p) ? n.delete(p) : n.add(p); setOpenFolders(n);
  };

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]; if (!file) return;
    await uploadLibraryFile(file, currentFolder || '/Job Uploads'); refetch();
    e.target.value = '';
  }
  async function handleNewFolder() {
    const name = window.prompt('New folder path', `${currentFolder || ''}/New folder`);
    if (name) { await createFolder(name); refetch(); }
  }
  async function handleDelete(f: LibraryFile) {
    if (!window.confirm(`Delete ${f.original_filename}?`)) return;
    try { await deleteFile(f.id); setSelected(null); refetch(); }
    catch (err) { window.alert(String(err)); }
  }

  if (tab === 'manyfold') {
    return (
      <div>
        <FilesTabBar tab={tab} setTab={setTab} />
        <div className="card" style={{ padding: 40, textAlign: 'center' }}>
          <div style={{ fontSize: 18, fontWeight: 600 }}>Manyfold integration</div>
          <div className="muted" style={{ marginTop: 8 }}>
            Coming soon — sync this library with a Manyfold server.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <FilesTabBar tab={tab} setTab={setTab} />
      {/* existing two-column grid: <FolderCard …/> on the left; right column header
          gains Upload / New folder / Rescan buttons + sort select; file grid maps over
          `files` using original_filename, size_bytes, plate_count, thumbnail_url, tags[].name;
          card onClick => setSelected(f). When `selected`, render <FileDetailPanel/> as a
          right drawer or modal. */}
      {/* ...reuse the existing FolderCard + FilterCard(facetGroups) + grid markup... */}
    </div>
  );
}

function FilesTabBar({ tab, setTab }: { tab: string; setTab: (t: 'library' | 'manyfold') => void }) {
  return (
    <div className="row gap-2" style={{ marginBottom: 14 }}>
      <button className={`btn sm ${tab === 'library' ? 'primary' : 'ghost'}`} onClick={() => setTab('library')}>Library</button>
      <button className={`btn sm ${tab === 'manyfold' ? 'primary' : 'ghost'}`} onClick={() => setTab('manyfold')}>Manyfold</button>
    </div>
  );
}
```

`FileDetailPanel` (new component in the same file): shows thumbnail (`f.thumbnail_url`), metadata, tag chips with add/remove via `addFileTag`/`removeFileTag` (+ a `<select>` of `tags` not yet on the file), **Rename** (prompt → `updateFile(id,{name})`), **Move** (prompt → `updateFile(id,{folder})`), **Delete** (`handleDelete`), and **Use in new job** (`navigate('/queue/new', { state: { libraryFileId: f.id } })`). Call `refetch()` after each mutation.

> The full JSX for the grid/detail panel mirrors the existing mock markup (already in this file) with field renames (`name`→`original_filename`, `size`→formatted `size_bytes`, `parts`→`plate_count`, `tags`→`tags[].name`, `thumbColor` gradient kept as a fallback when `thumbnail_url` is null). Add a `fmtSize(bytes)` helper (`(bytes/1e6).toFixed(1)+' MB'`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/screens/FilesScreen.test.tsx`
Then type-check: `cd frontend && npm run build`
Expected: tests PASS; build succeeds (no unused `data/mock`/`FileEntry` imports left).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/screens/FilesScreen.tsx frontend/src/screens/FilesScreen.test.tsx
git commit -m "feat(fe): real file library — tabs, detail panel, upload/move/delete/tag ops"
```

---

## Task 11: NewJob — pick from library + save-to-location

**Files:**
- Modify: `frontend/src/api/queue.ts:88-97` (`uploadFile` gains optional `folder`)
- Modify: `frontend/src/screens/NewJobScreen.tsx`
- Test: `frontend/src/screens/NewJobScreen.library.test.tsx` (create)

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/screens/NewJobScreen.library.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { NewJobScreen } from './NewJobScreen';

const FILES = [{ id: 7, original_filename: 'lib_part.3mf', relative_path: 'Job Uploads/lib_part.3mf',
  folder: '/Job Uploads', size_bytes: 1000, plate_count: 1, uploaded_at: 't', missing: false, tags: [], thumbnail_url: null }];

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.startsWith('/api/v1/files/7/plates')) return new Response(JSON.stringify(
      [{ plate_number: 1, thumbnail_path: null, estimated_time: 0, filament_g: 0 }]), { status: 200 });
    if (url.startsWith('/api/v1/files')) return new Response(JSON.stringify(FILES), { status: 200 });
    if (url.startsWith('/api/v1/printers')) return new Response('[]', { status: 200 });
    return new Response('[]', { status: 200 });
  }));
});

describe('NewJob library picker', () => {
  it('offers a Pick from library option that lists library files', async () => {
    render(<MemoryRouter><NewJobScreen /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: /Pick from library/i }));
    await waitFor(() => expect(screen.getByText('lib_part.3mf')).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/screens/NewJobScreen.library.test.tsx`
Expected: FAIL — no "Pick from library" control.

- [ ] **Step 3: Implement**

In `frontend/src/api/queue.ts` update `uploadFile` (`:88`):

```ts
export async function uploadFile(file: File, folder?: string): Promise<ApiUploadedFile> {
  const body = new FormData();
  body.append('file', file);
  if (folder) body.append('folder', folder);
  const resp = await fetch('/api/v1/files/upload', { method: 'POST', body });
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}
```

In `NewJobScreen.tsx`:
- Import `useFiles` + `getFiles` from `../api/files`, `useLocation` from `react-router-dom`.
- Add a source toggle near the upload control: **Upload** (existing drop/file input) vs **Pick from library** (button that opens a library list — render `useFiles({})` results as a selectable list of `original_filename` + folder; selecting one sets the working `uploadedFile` to `{ id, original_filename }` and loads its plates via the existing plate-fetch path, skipping upload).
- Honor `useLocation().state?.libraryFileId` on mount (from FilesScreen "Use in new job"): preselect that library file.
- Add an optional **"Save uploaded file to"** folder text input (default `/Job Uploads`) passed as the second arg to `uploadFile(file, folder)`.

Minimum to satisfy the test + wire behavior (sketch — integrate with the screen's existing state):

```tsx
// near other hooks
const location = useLocation();
const [source, setSource] = useState<'upload' | 'library'>('upload');
const { files: libraryFiles } = useFiles({});
const [saveFolder, setSaveFolder] = useState('/Job Uploads');

// preselect when navigated from the library
useEffect(() => {
  const id = (location.state as { libraryFileId?: number } | null)?.libraryFileId;
  if (id) { setSource('library'); selectLibraryFile(id); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, []);

// in JSX, above the existing upload control:
<div className="row gap-2">
  <button className={`btn sm ${source === 'upload' ? 'primary' : 'ghost'}`} onClick={() => setSource('upload')}>Upload</button>
  <button className={`btn sm ${source === 'library' ? 'primary' : 'ghost'}`} onClick={() => setSource('library')}>Pick from library</button>
</div>
{source === 'library' && (
  <div className="col gap-2">
    {libraryFiles.map(f => (
      <button key={f.id} className="btn ghost" style={{ justifyContent: 'space-between' }}
              onClick={() => selectLibraryFile(f.id)}>
        <span>{f.original_filename}</span><span className="tiny muted">{f.folder}</span>
      </button>
    ))}
  </div>
)}
```

`selectLibraryFile(id)` sets the screen's uploaded-file state to the chosen library file (id + name) and triggers the existing plate-load + per-plate config init (same code path the upload result feeds). When `source === 'upload'`, the existing upload call becomes `uploadFile(file, saveFolder)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/screens/NewJobScreen.library.test.tsx`
Then: `cd frontend && npx vitest run src/screens/` and `npm run build`.
Expected: PASS; existing NewJob tests still green; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/queue.ts frontend/src/screens/NewJobScreen.tsx frontend/src/screens/NewJobScreen.library.test.tsx
git commit -m "feat(fe): New Job — pick from library + save-to-location"
```

---

## Task 12: Settings → Tags wired to the real API

**Files:**
- Modify: `frontend/src/screens/SettingsScreen.tsx` (Tags tab)
- Test: `frontend/src/screens/SettingsScreen.tags.test.tsx` (create)

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/screens/SettingsScreen.tags.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SettingsScreen } from './SettingsScreen';

const TAGS = [{ id: 1, name: 'PLA', color: '#22c55e', category: 'Material', usage_count: 3 }];

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.startsWith('/api/v1/tags')) return new Response(JSON.stringify(TAGS), { status: 200 });
    return new Response('{}', { status: 200 });
  }));
});

describe('Settings Tags tab', () => {
  it('lists tags from the API', async () => {
    render(<MemoryRouter initialEntries={['/settings/tags']}><SettingsScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('PLA')).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/screens/SettingsScreen.tags.test.tsx`
Expected: FAIL — current Tags tab reads the mock `TAGS`; no fetch to `/api/v1/tags`.

- [ ] **Step 3: Implement**

In `SettingsScreen.tsx`, Tags section: replace `import { TAGS } from '../data/mock'` usage with `useTags()` from `../api/tags`. Wire create/edit/delete to `createTag`/`updateTag`/`deleteTag`, calling `refetch()` after each. Derive the "in use" / "orphan" counts from `usage_count` (in use = `usage_count > 0`; orphan = `usage_count === 0`). Keep the existing inline editor markup; on save call the API instead of mutating local state. Map the API `Tag` to the existing row markup (`name`, `color`, `category`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/screens/SettingsScreen.tags.test.tsx`
Then: `cd frontend && npm run build`.
Expected: PASS; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/screens/SettingsScreen.tsx frontend/src/screens/SettingsScreen.tags.test.tsx
git commit -m "feat(fe): Settings Tags tab wired to real tags API"
```

---

## Task 13: Scrub the Themis filament library

**Files:**
- Delete: `frontend/src/screens/FilamentsScreen.tsx`
- Modify: `frontend/src/App.tsx:17,47-49,80` (remove import, screenConfig entry, route)
- Modify: `frontend/src/components/Sidebar.tsx:43` (remove the Filaments nav link)
- Modify: `frontend/src/data/mock.ts` (remove `FILAMENTS`, and `FILES`/`TAGS` + `FileEntry`/`Tag` usages now unused; keep `PRINTERS`/`ORDERS`/`JOBS` still referenced by Fleet/ui)
- Test: `frontend/src/App.test.tsx` (create) — assert no Filaments link and `/filaments` redirects.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/App.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import App from './App';

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async () => new Response('[]', { status: 200 })));
  class FakeWS { onmessage = null; onopen = null; onclose = null; close() {} send() {} }
  vi.stubGlobal('WebSocket', FakeWS as unknown as typeof WebSocket);
});

describe('App nav', () => {
  it('has no Filaments link', () => {
    render(<App />);
    expect(screen.queryByRole('link', { name: /Filaments/i })).toBeNull();
    expect(screen.queryByText(/Filament library/i)).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/App.test.tsx`
Expected: FAIL — a Filaments link is present.

- [ ] **Step 3: Implement**

- `App.tsx`: remove `import { FilamentsScreen } …` (`:17`), the `'/filaments'` entry in `screenConfig` (`:47-49`), and the `<Route path="/filaments" …/>` (`:80`). (`/filaments` now falls through to the `path="*"` → `Navigate to="/queue"`.)
- `Sidebar.tsx`: remove the `{ to: '/filaments', label: 'Filaments', icon: Icons.spool }` entry (`:43`).
- Delete `frontend/src/screens/FilamentsScreen.tsx`.
- `data/mock.ts`: remove the `FILAMENTS` export (and `FILES`, `TAGS` if no longer imported anywhere — confirm with a grep). Leave `PRINTERS`/`ORDERS`/`JOBS`/`PROCESS_PRESETS` and helpers still used by Fleet/ui. Remove now-unused `Filament`/`FileEntry`/`Tag` type imports in `mock.ts`.

Run a guard grep before building:
```bash
cd frontend && grep -rn "FilamentsScreen\|FILAMENTS\|from '../data/mock'" src | grep -v ".test."
```
Resolve any remaining importers of the removed exports (FilesScreen/Settings already migrated in Tasks 10/12).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/App.test.tsx`
Then full check: `cd frontend && npx vitest run && npm run build`
Expected: PASS; build succeeds with no unused-import / missing-export errors.

- [ ] **Step 5: Commit**

```bash
git add -A frontend/src
git commit -m "refactor(fe): remove Themis filament library (Spoolman is the source)"
```

---

## Task 14: Update agent reference docs

**Files:**
- Modify: `docs/agent/*` (if present on this branch) via the `themis-docs-sync` skill.

> Note: the `docs/agent/` set currently lives on the `bambu-printer` branch, not `file-library`. If it isn't present here, skip the doc edits and instead leave a note in the PR description to run `themis-docs-sync` after the branches converge. If it is present, run the skill to update: `data-model.md` (uploaded_files new columns, `tags`/`file_tags`), `backend.md` (files routes expansion, `tags.py`, `LibraryScanner`, startup scan), `frontend.md` (Files screen real + tabs, `api/files.ts`/`api/tags.ts`, Filaments removal), `recipes.md`/`conventions.md` (library dir + filecache, legacy migration sentinel), and `README.md` counts.

- [ ] **Step 1:** Determine presence: `ls docs/agent 2>/dev/null`.
- [ ] **Step 2:** If present, invoke the `themis-docs-sync` skill against the diff of this branch.
- [ ] **Step 3:** Commit: `git add docs/agent && git commit -m "docs(agent): sync for file library"` (or note deferral in the PR).

---

## Final review

After all tasks: dispatch a final code review over the whole branch diff, then run the full suites:
```bash
cd backend && pytest -q
cd frontend && npx vitest run && npm run build
```
Then use **superpowers:finishing-a-development-branch** to wrap up (PR off `file-library`).

---

## Self-review notes (author)

- **Spec coverage:** storage model (T1), index+tag schema (T2–T3), scanner incl. move/missing/hash-relink (T4), tags API (T5), full files API incl. tree/upload-to-folder/move/delete-guard/tag-filter (T6), startup scan + legacy migration (T7), api clients (T8–T9), Files screen w/ tabs+detail+ops+Manyfold placeholder (T10), New Job pick-from-library + save-to-location (T11), Settings Tags real (T12), filament scrub (T13), docs (T14). All spec sections mapped.
- **Type consistency:** backend serialized keys (`original_filename, relative_path, folder, size_bytes, plate_count, missing, tags[], thumbnail_url`) match the FE `LibraryFile` type and the screen field renames. `folder_of`, `sha256_file`, `ACTIVE_JOB_STATUSES`, `MODEL_EXTS`, `LibraryScanner.unique_path`, `migrate_legacy_uploads` referenced consistently across T4/T6/T7.
- **Known soft spots flagged inline:** `session.get(FileTag, {composite})` dict form (T6 note — fall back to `select` if unsupported); `parse_three_mf` import path verify (T4); `data/mock.ts` residual-import grep guard (T13).
