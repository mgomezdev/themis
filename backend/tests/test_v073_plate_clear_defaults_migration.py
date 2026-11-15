"""v073: queue_config.plate_clear_complete / plate_clear_failed / pause_after_failed_prints (defaults for an existing install) and
printers.queue_paused_reason (NULL)."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v073_plate_clear_defaults as migration

_QUEUE = "SELECT waive_after, plate_clear_complete, plate_clear_failed, pause_after_failed_prints FROM queue_config"


async def test_v073_adds_the_defaults_keeps_existing_values_is_idempotent_and_reverses(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE queue_config (id INTEGER PRIMARY KEY, waive_after INTEGER)"))
            await conn.execute(text("INSERT INTO queue_config VALUES (1, 4)"))
            await conn.execute(text("CREATE TABLE printers (id INTEGER PRIMARY KEY, name TEXT)"))
            await conn.execute(text("INSERT INTO printers VALUES (1, 'A'), (2, 'B')"))
            await migration.up(conn)
            assert (await conn.execute(text(_QUEUE))).one() == (4, "assume_good", "assume_failed", 3)      # existing value kept
            assert (await conn.execute(text("SELECT id, name, queue_paused_reason FROM printers ORDER BY id"))).all() == [
                (1, "A", None), (2, "B", None)]

            await conn.execute(text("UPDATE queue_config SET plate_clear_complete = 'ask', plate_clear_failed = 'ask', "
                                    "pause_after_failed_prints = 0"))
            await conn.execute(text("UPDATE printers SET queue_paused_reason = '3 prints failed in a row' WHERE id = 1"))
            await migration.up(conn)                                   # idempotent: a second run changes nothing
            assert (await conn.execute(text(_QUEUE))).one() == (4, "ask", "ask", 0)
            assert (await conn.execute(text("SELECT queue_paused_reason FROM printers ORDER BY id"))).all() == [
                ("3 prints failed in a row",), (None,)]

            await migration.down(conn)
            await migration.down(conn)
            assert (await conn.execute(text("SELECT * FROM queue_config"))).all() == [(1, 4)]
            assert (await conn.execute(text("SELECT * FROM printers ORDER BY id"))).all() == [(1, "A"), (2, "B")]
    finally:
        await engine.dispose()


def test_v073_is_registered_with_the_runner():
    from app.migrations import runner
    assert (migration.version, migration.name) == (73, "plate_clear_defaults")
    assert runner.v073_plate_clear_defaults is migration
