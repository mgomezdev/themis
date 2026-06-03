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
