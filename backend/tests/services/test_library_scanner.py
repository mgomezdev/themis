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
