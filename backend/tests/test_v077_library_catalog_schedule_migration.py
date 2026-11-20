from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.migrations import runner, v077_library_catalog_schedule as migration


async def test_schedule_migration_is_idempotent_and_reversible(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE uploaded_files (id INTEGER PRIMARY KEY)")
            )
            await conn.execute(text("INSERT INTO uploaded_files VALUES (1)"))
            await migration.up(conn)
            await conn.execute(
                text("INSERT INTO library_catalog_schedules VALUES ('source', 1234)")
            )
            await migration.up(conn)
            assert (
                await conn.execute(
                    text("SELECT next_due_at FROM library_catalog_schedules")
                )
            ).scalar() == 1234
            await migration.down(conn)
            await migration.down(conn)
            assert (
                await conn.execute(text("SELECT id FROM uploaded_files"))
            ).scalar() == 1
    finally:
        await engine.dispose()
    assert migration in runner._MIGRATIONS
