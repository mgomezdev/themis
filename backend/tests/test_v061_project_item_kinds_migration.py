"""v061: project_items.kind/source_ref/pack_rule/source_hash; existing rows are split STLs; idempotent; down removes them."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v061_project_item_kinds as mig
from app.migrations.runner import run_migrations


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    async with engine.begin() as conn:
        await run_migrations(conn)
        await mig.down(conn)
    yield engine
    await engine.dispose()


async def _cols(conn):
    return {r[1] for r in (await conn.execute(text("PRAGMA table_info(project_items)"))).fetchall()}


async def test_existing_items_become_split_stls(db):
    async with db.begin() as conn:      # (no FK pragma on this bare engine: the row needs no parents)
        await conn.execute(text("INSERT INTO project_items (id, project_id, file_id, quantity, quantity_completed, quantity_failed, "
                                "filament_type, filament_color, color_hex, sort_order) VALUES (1, 1, 1, 2, 0, 0, 'any', 'any', '#FFFFFF', 0)"))
        await mig.up(conn)
        await mig.up(conn)
        row = (await conn.execute(text("SELECT kind, source_ref, pack_rule, source_hash FROM project_items"))).one()
        assert tuple(row) == ("stl", None, "split", None)
        await mig.down(conn)
        assert not {"kind", "source_ref", "pack_rule", "source_hash"} & await _cols(conn)
