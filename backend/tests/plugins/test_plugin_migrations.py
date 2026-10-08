"""Plugin-owned migrations (spec §3.8) + the core v034 migration that creates the host tables."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base
from app.migrations import v034_plugin_host
from app.migrations.runner import run_migrations
from app.plugins.migrations import rollback_plugin_migration, run_plugin_migrations
from tests.plugins.dummy_plugin import make_manifest, migration

CREATE_ITEMS = "CREATE TABLE IF NOT EXISTS dummy_one_items (id INTEGER PRIMARY KEY, name TEXT)"
DROP_ITEMS = "DROP TABLE IF EXISTS dummy_one_items"
ADD_COL = "ALTER TABLE dummy_one_items ADD COLUMN color TEXT"


@pytest.fixture
async def conn(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}")
    async with engine.begin() as c:
        yield c
    await engine.dispose()


async def _tables(conn) -> set[str]:
    return {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()}


async def _versions(conn, plugin_id="dummy_one") -> list[int]:
    rows = await conn.execute(text("SELECT version FROM plugin_schema_versions WHERE plugin_id=:p ORDER BY version"), {"p": plugin_id})
    return [r[0] for r in rows.fetchall()]


def _two_step_failure(table: str):
    """A migration that creates `table` and THEN fails — only a real rollback leaves no trace of it."""
    import types
    mod = types.ModuleType("two_step")
    mod.version, mod.name = 1, "two_step"

    async def up(conn):
        await conn.execute(text(f"CREATE TABLE {table} (id INTEGER)"))
        await conn.execute(text("SELECT * FROM nope"))
    mod.up, mod.down = up, (lambda conn: None)
    return mod


async def test_pending_migrations_run_in_version_order_and_are_recorded(conn):
    m = make_manifest(migrations=(migration(2, ADD_COL, "select 1"), migration(1, CREATE_ITEMS, DROP_ITEMS)))   # listed out of order
    assert await run_plugin_migrations(conn, [m]) == {}
    assert "dummy_one_items" in await _tables(conn)
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(dummy_one_items)"))).fetchall()}
    assert "color" in cols and await _versions(conn) == [1, 2]


async def test_running_twice_is_idempotent(conn):
    m = make_manifest(migrations=(migration(1, CREATE_ITEMS, DROP_ITEMS), migration(2, ADD_COL, "select 1")))
    await run_plugin_migrations(conn, [m])
    assert await run_plugin_migrations(conn, [m]) == {}          # v2's ALTER would fail if it re-ran
    assert await _versions(conn) == [1, 2]


async def test_a_new_migration_runs_when_a_plugin_ships_an_update(conn):
    await run_plugin_migrations(conn, [make_manifest(migrations=(migration(1, CREATE_ITEMS, DROP_ITEMS),))])
    await run_plugin_migrations(conn, [make_manifest(migrations=(migration(1, CREATE_ITEMS, DROP_ITEMS), migration(2, ADD_COL, "select 1")))])
    assert await _versions(conn) == [1, 2]


async def test_down_undoes_the_newest_migration_and_forgets_it(conn):
    m = make_manifest(migrations=(migration(1, CREATE_ITEMS, DROP_ITEMS),))
    await run_plugin_migrations(conn, [m])
    assert await rollback_plugin_migration(conn, m) == 1
    assert "dummy_one_items" not in await _tables(conn) and await _versions(conn) == []
    assert await rollback_plugin_migration(conn, m) is None
    assert await run_plugin_migrations(conn, [m]) == {}          # and it can be re-applied
    assert "dummy_one_items" in await _tables(conn)


async def test_a_failing_migration_is_rolled_back_reported_and_does_not_block_other_plugins(conn):
    bad = make_manifest("bad_plugin", migrations=(_two_step_failure("bad_plugin_t"),))
    good = make_manifest("good_plugin", migrations=(migration(1, "CREATE TABLE good_plugin_t (id INTEGER)", "select 1"),))

    errors = await run_plugin_migrations(conn, [bad, good])

    assert list(errors) == ["bad_plugin"] and "v1" in errors["bad_plugin"]
    tables = await _tables(conn)
    assert "good_plugin_t" in tables and "bad_plugin_t" not in tables           # the half-applied migration was undone
    assert await _versions(conn, "bad_plugin") == [] and await _versions(conn, "good_plugin") == [1]


async def test_tables_outside_the_plugins_prefix_are_refused_and_rolled_back(conn):
    m = make_manifest(migrations=(migration(1, "CREATE TABLE printers_hijack (id INTEGER)", "select 1"),))
    errors = await run_plugin_migrations(conn, [m])
    assert "outside its prefix" in errors["dummy_one"]
    assert "printers_hijack" not in await _tables(conn) and await _versions(conn) == []


async def test_a_custom_table_prefix_is_honoured(conn):
    m = make_manifest("local_inventory", table_prefix="local_inv_",
                      migrations=(migration(1, "CREATE TABLE local_inv_spools (id INTEGER)", "select 1"),))
    assert await run_plugin_migrations(conn, [m]) == {}


async def test_a_failure_stops_that_plugins_later_versions(conn):
    m = make_manifest(migrations=(_two_step_failure("dummy_one_half"), migration(2, CREATE_ITEMS, DROP_ITEMS)))
    assert "dummy_one" in await run_plugin_migrations(conn, [m])
    assert "dummy_one_items" not in await _tables(conn)


# --- core v034 ---------------------------------------------------------------------------------------------------

HOST_TABLES = {"plugin_configs", "capability_selections", "plugin_schema_versions"}
V034_TABLES = {"plugin_configs", "plugin_schema_versions"}      # (v034 also made extension_slots; v040 replaced it)


async def test_v034_is_idempotent_on_a_fresh_create_all_db_and_on_a_migrated_one_and_has_a_down(conn):
    await conn.run_sync(Base.metadata.create_all)               # a fresh install: v001 builds every model
    await run_migrations(conn)
    await run_migrations(conn)
    assert HOST_TABLES <= await _tables(conn) and "extension_slots" not in await _tables(conn)

    await v034_plugin_host.down(conn)
    assert not V034_TABLES & await _tables(conn)
    await v034_plugin_host.up(conn)                              # re-applies cleanly after a downgrade
    await v034_plugin_host.up(conn)
    assert V034_TABLES <= await _tables(conn)


async def test_the_models_and_the_migrations_agree_on_columns(conn):
    from app.migrations import v040_capability_selections
    await conn.run_sync(Base.metadata.create_all)
    from_models = {t: [r[1] for r in (await conn.execute(text(f"PRAGMA table_info({t})"))).fetchall()] for t in HOST_TABLES}
    for t in HOST_TABLES:
        await conn.execute(text(f"DROP TABLE {t}"))
    await v034_plugin_host.up(conn)
    await v040_capability_selections.up(conn)
    from_migration = {t: [r[1] for r in (await conn.execute(text(f"PRAGMA table_info({t})"))).fetchall()] for t in HOST_TABLES}
    assert from_models == from_migration


async def test_a_failed_run_is_published_for_the_host_and_cleared_by_the_next_good_run(conn):
    from app.plugins import migrations as plugin_migrations
    bad = make_manifest(migrations=(_two_step_failure("dummy_one_half"),))
    await run_plugin_migrations(conn, [bad])
    assert "dummy_one" in plugin_migrations.failed
    await run_plugin_migrations(conn, [make_manifest(migrations=(migration(1, CREATE_ITEMS, DROP_ITEMS),))])
    assert plugin_migrations.failed == {}


async def test_a_plugin_whose_migration_list_is_broken_is_reported_without_raising(conn):
    broken = make_manifest("broken_plugin")
    object.__setattr__(broken, "migrations", None)                 # sorted(None) blows up
    good = make_manifest("good_plugin", migrations=(migration(1, "CREATE TABLE good_plugin_t (id INTEGER)", "select 1"),))
    errors = await run_plugin_migrations(conn, [broken, good])
    assert list(errors) == ["broken_plugin"] and "good_plugin_t" in await _tables(conn)


async def test_a_failing_down_is_rolled_back_and_leaves_the_version_recorded(conn):
    import types
    mod = types.ModuleType("badown")
    mod.version, mod.name = 1, "badown"

    async def up(conn):
        await conn.execute(text(CREATE_ITEMS))

    async def down(conn):
        await conn.execute(text(DROP_ITEMS))
        raise RuntimeError("down failed halfway")
    mod.up, mod.down = up, down
    m = make_manifest(migrations=(mod,))
    await run_plugin_migrations(conn, [m])
    with pytest.raises(RuntimeError):
        await rollback_plugin_migration(conn, m)
    assert "dummy_one_items" in await _tables(conn) and await _versions(conn) == [1]


async def test_an_autoincrement_table_is_not_a_stray_table_because_sqlite_adds_its_own_bookkeeping_table(conn):
    """The first AUTOINCREMENT table makes SQLite create `sqlite_sequence`; that must not read as the plugin escaping its prefix."""
    m = make_manifest(migrations=(migration(1, "CREATE TABLE dummy_one_items (id INTEGER PRIMARY KEY AUTOINCREMENT)", DROP_ITEMS),))
    assert await run_plugin_migrations(conn, [m]) == {}
    assert "dummy_one_items" in await _tables(conn)
