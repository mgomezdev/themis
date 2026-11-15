"""v059: print groups. Existing projects get one Default Group holding their items; idempotent; down removes it all."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v059_project_groups as mig
from app.migrations.runner import run_migrations


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    async with engine.begin() as conn:
        await run_migrations(conn)
        await mig.down(conn)                      # the v058 shape, to run up() over existing projects
    yield engine
    await engine.dispose()


async def test_existing_projects_get_a_default_group_with_their_items(db):
    async with db.begin() as conn:      # (no FK pragma on this bare engine: rows need no parents)
        for pid in (1, 2):
            await conn.execute(text("INSERT INTO projects (id, name, customer, order_type, on_hold, created_at, updated_at) "
                                    f"VALUES ({pid}, 'P{pid}', '', 'internal', 0, 'now', 'now')"))
        for iid, fid in ((10, 5), (11, 6)):
            await conn.execute(text("INSERT INTO project_items (id, project_id, file_id, quantity, quantity_completed, quantity_failed, "
                                    f"filament_type, filament_color, color_hex, sort_order) VALUES ({iid}, 1, {fid}, 1, 0, 0, 'any', 'any', '#FFFFFF', 0)"))
        await mig.up(conn)
        await mig.up(conn)
        groups = (await conn.execute(text("SELECT id, project_id, name, is_default FROM project_groups ORDER BY project_id"))).fetchall()
        assert [(g[1], g[2], g[3]) for g in groups] == [(1, "Default Group", 1), (2, "Default Group", 1)]
        items = (await conn.execute(text("SELECT group_id FROM project_items ORDER BY id"))).scalars().all()
        assert items == [groups[0][0], groups[0][0]]
        await mig.down(conn)
        tables = (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).scalars().all()
        assert "project_groups" not in tables and "project_group_targets" not in tables
        assert "group_id" not in {r[1] for r in (await conn.execute(text("PRAGMA table_info(project_items)"))).fetchall()}
