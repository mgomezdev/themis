"""v067 (BIZ-369): printers.configured + config_error; rows that existed before the column are back-filled configured."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v067_printer_configured as migration


async def test_v067_backfills_existing_printers_as_configured(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE printers (id INTEGER PRIMARY KEY, name TEXT)"))
            await conn.execute(text("INSERT INTO printers VALUES (1, 'old')"))
            await migration.up(conn)
            assert (await conn.execute(text("SELECT id, configured, config_error FROM printers"))).one() == (1, 1, None)
            await conn.execute(text("INSERT INTO printers (id, name) VALUES (2, 'new')"))
            await migration.up(conn)                                   # idempotent: back-fill only when the column is added
            assert (await conn.execute(text("SELECT configured FROM printers WHERE id = 2"))).scalar_one() == 0
            assert (await conn.execute(text("SELECT configured FROM printers WHERE id = 1"))).scalar_one() == 1
            await migration.down(conn)
            await migration.down(conn)
            assert (await conn.execute(text("SELECT * FROM printers ORDER BY id"))).all() == [(1, "old"), (2, "new")]
    finally:
        await engine.dispose()
