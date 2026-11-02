from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v057_unmatched_filaments as migration


async def test_unmatched_filaments_migration_preserves_existing_jobs_and_is_idempotent(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE jobs (id INTEGER PRIMARY KEY, block_reason TEXT)"))
            await conn.execute(text("INSERT INTO jobs VALUES (1, 'old block')"))
            await migration.up(conn)
            await migration.up(conn)
            assert (await conn.execute(text("SELECT id, block_reason, unmatched_filaments FROM jobs"))).one() == (1, "old block", None)
            await migration.down(conn)
            await migration.down(conn)
            assert (await conn.execute(text("SELECT * FROM jobs"))).one() == (1, "old block")
    finally:
        await engine.dispose()
