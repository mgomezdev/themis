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
