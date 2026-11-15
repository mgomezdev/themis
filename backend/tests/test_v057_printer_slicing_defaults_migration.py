"""v057: printers.default_process_profile (NULL) + printers.single_colour_only (0) on existing rows; idempotent, removable."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v057_printer_slicing_defaults as mig
from tests.v032_fixture import build_v032_fixture_db


async def _cols(conn):
    return {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{await build_v032_fixture_db(tmp_path / 'v032.db')}")
    yield engine
    await engine.dispose()


async def test_existing_printers_get_defaults_and_up_is_idempotent_and_down_removes(db):
    async with db.begin() as conn:
        assert (await conn.execute(text("SELECT COUNT(*) FROM printers"))).scalar_one() > 0
        await mig.up(conn)
        await mig.up(conn)
        rows = (await conn.execute(text("SELECT default_process_profile, single_colour_only FROM printers"))).fetchall()
        assert rows and all(r == (None, 0) for r in rows)
        await mig.down(conn)
        assert not {"default_process_profile", "single_colour_only"} & await _cols(conn)
        await mig.down(conn)
