from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import runner, v076_library_source_revisions as migration


async def test_revision_migration_reversible_idempotent_and_preserves_local_files(
    tmp_path,
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            await conn.execute(
                text("CREATE TABLE uploaded_files (id INTEGER PRIMARY KEY)")
            )
            await conn.execute(
                text(
                    "CREATE TABLE library_catalog_entries (id VARCHAR(36) PRIMARY KEY)"
                )
            )
            await conn.execute(text("INSERT INTO uploaded_files VALUES (1)"))
            await conn.execute(
                text("INSERT INTO library_catalog_entries VALUES ('entry')")
            )
            await migration.up(conn)
            await conn.execute(
                text(
                    "INSERT INTO library_source_revisions VALUES (1, 'entry', 'v1', '{}', 'now')"
                )
            )
            await migration.up(conn)
            assert (
                await conn.execute(
                    text("SELECT revision FROM library_source_revisions")
                )
            ).scalar() == "v1"
            await migration.down(conn)
            await migration.down(conn)
            assert (
                await conn.execute(text("SELECT id FROM uploaded_files"))
            ).scalar() == 1
    finally:
        await engine.dispose()
    assert migration in runner._MIGRATIONS
