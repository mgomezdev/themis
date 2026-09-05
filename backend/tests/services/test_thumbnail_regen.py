import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models import UploadedFile
from app.services import thumbnail_regen

FILE_ID = 999_999_001


@pytest.mark.asyncio
async def test_regen_file_thumbnails_uses_injected_session_factory(monkeypatch, tmp_path):
    """regen_file_thumbnails must go through the session factory it's told to use,
    not the real app database - otherwise a fresh checkout/worktree/CI with no
    pre-migrated data/themis.db fails here, and a test pointed at an isolated DB
    silently no-ops against the wrong one instead of touching the seeded row."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        session.add(UploadedFile(
            id=FILE_ID,
            original_filename="test.3mf",
            relative_path="test.3mf",
            plates=[{"plate_number": 1}],
            uploaded_at="2026-01-01T00:00:00",
        ))
        await session.commit()

    monkeypatch.setattr(thumbnail_regen, "get_filecache_dir", lambda: tmp_path)
    monkeypatch.setattr(thumbnail_regen, "_regen_sync", lambda *a, **k: {1: "/fake/plate_1.png"})

    original_factory = thumbnail_regen._session_factory
    thumbnail_regen.set_session_factory(factory)
    try:
        await thumbnail_regen.regen_file_thumbnails(FILE_ID)

        async with factory() as session:
            updated = await session.get(UploadedFile, FILE_ID)
            assert updated.plates[0]["thumbnail_path"] == "/fake/plate_1.png"
    finally:
        thumbnail_regen.set_session_factory(original_factory)
        await engine.dispose()
