"""`init_db` runs plugin migrations after core ones, and a broken plugin never stops Themis from starting."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import database, plugins
from app.plugins import migrations as plugin_migrations
from tests.plugins.dummy_plugin import make_manifest, migration


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'boot.db'}")
    monkeypatch.setattr(database, "engine", engine)
    yield engine


async def _tables(engine) -> set[str]:
    async with engine.connect() as c:
        return {r[0] for r in (await c.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()}


async def test_init_db_applies_core_then_registered_plugin_migrations(db):
    plugins.register_plugin(make_manifest(migrations=(migration(1, "CREATE TABLE dummy_one_items (id INTEGER)", "select 1"),)))
    await database.init_db()
    tables = await _tables(db)
    assert {"printers", "plugin_configs", "dummy_one_items"} <= tables           # core AND plugin tables, fresh DB
    await database.init_db()                                                      # restart: idempotent
    async with db.connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM plugin_schema_versions WHERE plugin_id = 'dummy_one'"))).scalar_one() == 1


async def test_a_failing_plugin_migration_leaves_core_migrated_and_is_reported(db):
    plugins.register_plugin(make_manifest(migrations=(migration(1, "SELECT * FROM nope", "select 1"),)))
    await database.init_db()                                                      # must not raise
    assert "printers" in await _tables(db)
    assert "dummy_one" in plugin_migrations.failed
    plugin_migrations.failed.clear()


async def test_a_bundled_plugin_that_fails_to_import_does_not_break_init_db(db, monkeypatch):
    monkeypatch.setattr(plugins, "BUNDLED_MODULES", ("no.such.module",))
    await database.init_db()
    assert "printers" in await _tables(db)


async def test_even_an_unexpected_error_in_the_plugin_phase_is_contained(db, monkeypatch):
    async def boom(conn, manifests=None):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(plugin_migrations, "run_plugin_migrations", boom)
    await database.init_db()
    assert "printers" in await _tables(db)
