"""v040: capability_selections replaces extension_slots; installed_plugins.kind is dropped."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v040_capability_selections
from app.migrations.runner import run_migrations
from tests.plugins.legacy_shape import restore_pre_040_shape
from tests.v032_fixture import build_v032_fixture_db


@pytest.fixture
async def migrated(tmp_path):
    path = await build_v032_fixture_db(tmp_path / "v032.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await run_migrations(conn)
    conn = await engine.connect()
    yield conn
    await conn.close()
    await engine.dispose()


async def _tables(conn) -> set[str]:
    return {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()}


async def _cols(conn, table: str) -> set[str]:
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def test_creates_table_drops_slots_and_installed_plugins_kind(migrated):
    assert "capability_selections" in await _tables(migrated) and "extension_slots" not in await _tables(migrated)
    assert "kind" not in await _cols(migrated, "installed_plugins")


async def test_up_maps_legacy_slots_as_explicit_choices(migrated):
    await restore_pre_040_shape(migrated)                         # reproduce the pre-040 shape on the fully migrated fixture
    await migrated.execute(text("DELETE FROM extension_slots"))   # (the fixture may already carry a slot from v035)
    await migrated.execute(text("INSERT INTO extension_slots (kind, plugin_id) VALUES ('filament_inventory', 'spoolman'), ('mystery_kind', NULL)"))
    await v040_capability_selections.up(migrated)
    rows = (await migrated.execute(text("SELECT capability, plugin_id, explicit FROM capability_selections ORDER BY capability"))).fetchall()
    assert [tuple(r) for r in rows] == [("inventory.filament", "spoolman", 1), ("mystery_kind", None, 1)]
    assert "extension_slots" not in await _tables(migrated) and "kind" not in await _cols(migrated, "installed_plugins")


async def test_up_twice_is_a_noop(migrated):
    await migrated.execute(text("DELETE FROM capability_selections"))
    await migrated.execute(text("INSERT INTO capability_selections (capability, plugin_id, explicit) VALUES ('inventory.filament', 'local_inventory', 1)"))
    await v040_capability_selections.up(migrated)
    await v040_capability_selections.up(migrated)
    assert [tuple(r) for r in (await migrated.execute(text("SELECT * FROM capability_selections"))).fetchall()] == [("inventory.filament", "local_inventory", 1)]


async def test_down_only_removes_the_selection_table_and_up_restores_it(migrated):
    await v040_capability_selections.down(migrated)
    assert "capability_selections" not in await _tables(migrated) and "extension_slots" not in await _tables(migrated)
    await v040_capability_selections.up(migrated)
    assert "capability_selections" in await _tables(migrated)


async def test_rolling_back_past_v035_and_migrating_up_again_boots(tmp_path):
    from app.migrations.runner import rollback_last
    path = await build_v032_fixture_db(tmp_path / "v032.db")          # carries a Spoolman URL, so v035 selects spoolman
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await run_migrations(conn)
        for _ in range(8):                                              # v042 .. v035
            await rollback_last(conn)
        await run_migrations(conn)                                      # must not crash on the missing slot / selection tables
        assert (await conn.execute(text("SELECT plugin_id FROM capability_selections WHERE capability='inventory.filament'"))).scalar() == "spoolman"
    await engine.dispose()
