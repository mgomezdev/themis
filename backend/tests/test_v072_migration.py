"""v072: printers.slice_overrides, NULL for existing rows, idempotent, removable."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v072_printer_slice_overrides as mig
from tests.v032_fixture import build_v032_fixture_db


async def _cols(conn):
    return {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{await build_v032_fixture_db(tmp_path / 'v032.db')}")
    async with engine.begin() as conn:
        if "slice_overrides" in await _cols(conn):
            await conn.execute(text("ALTER TABLE printers DROP COLUMN slice_overrides"))
    yield engine
    await engine.dispose()


async def test_existing_printers_get_null_and_up_is_idempotent_and_down_removes_the_column(db):
    async with db.begin() as conn:
        before = (await conn.execute(text("SELECT count(*) FROM printers"))).scalar_one()
        await mig.up(conn)
        await mig.up(conn)
        assert "slice_overrides" in await _cols(conn)
        assert (await conn.execute(text("SELECT count(*) FROM printers WHERE slice_overrides IS NULL"))).scalar_one() == before
        await mig.down(conn)
        assert "slice_overrides" not in await _cols(conn)
        await mig.down(conn)
