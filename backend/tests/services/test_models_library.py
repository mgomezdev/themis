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
