"""v072: queue_config.waiver_flag_at / waiver_stop_at — fleet-wide waiver thresholds, off (0) for an existing install."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v072_fleet_waiver_thresholds as migration


async def test_v072_adds_the_thresholds_off_and_keeps_a_value_already_set(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE queue_config (id INTEGER PRIMARY KEY, waive_after INTEGER)"))
            await conn.execute(text("INSERT INTO queue_config VALUES (1, 2)"))
            await migration.up(conn)
            assert (await conn.execute(text("SELECT waive_after, waiver_flag_at, waiver_stop_at FROM queue_config"))).one() == (2, 0, 0)
            await conn.execute(text("UPDATE queue_config SET waiver_stop_at = 3"))
            await migration.up(conn)                                   # idempotent: a second run changes nothing
            assert (await conn.execute(text("SELECT waiver_flag_at, waiver_stop_at FROM queue_config"))).one() == (0, 3)
            await migration.down(conn)
            await migration.down(conn)
            assert (await conn.execute(text("SELECT * FROM queue_config"))).all() == [(1, 2)]
    finally:
        await engine.dispose()
