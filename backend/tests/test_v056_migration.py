"""v056: queue_config.printer_dead_after_minutes, default 30, idempotent, removable."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v056_printer_dead_after as mig
from tests.v032_fixture import build_v032_fixture_db


async def _cols(conn):
    return {r[1] for r in (await conn.execute(text("PRAGMA table_info(queue_config)"))).fetchall()}


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{await build_v032_fixture_db(tmp_path / 'v032.db')}")
    async with engine.begin() as conn:
        if "printer_dead_after_minutes" in await _cols(conn):
            await conn.execute(text("ALTER TABLE queue_config DROP COLUMN printer_dead_after_minutes"))
        await conn.execute(text("INSERT INTO queue_config (id, check_interval_minutes, snapshot_interval_seconds, estimates_enabled) VALUES (1, 5, 2, 0)"))
    yield engine
    await engine.dispose()


async def test_existing_rows_get_30_and_up_is_idempotent_and_down_removes_the_column(db):
    async with db.begin() as conn:
        await mig.up(conn)
        await mig.up(conn)
        assert (await conn.execute(text("SELECT printer_dead_after_minutes FROM queue_config WHERE id=1"))).scalar_one() == 30
        await mig.down(conn)
        assert "printer_dead_after_minutes" not in await _cols(conn)
        await mig.down(conn)
