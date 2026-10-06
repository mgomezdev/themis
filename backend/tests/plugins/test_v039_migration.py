"""v039 adds `installed_plugins` and `audit_log` (BIZ-223): on the phase-0 fixture, idempotent, `down()`, columns the ORM uses."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v039_plugin_install
from app.migrations.runner import run_migrations
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


async def test_creates_both_tables_and_the_plugin_id_is_unique(migrated):
    assert {"installed_plugins", "audit_log"} <= await _tables(migrated)
    ins = text("INSERT INTO installed_plugins (plugin_id, version, name, kind, source, archive_sha256, installed_at, status) "
               "VALUES ('acme_inv', '1.0.0', 'A', 'k', 'upload', 'x', 't', 'pending_restart')")
    await migrated.execute(ins)
    with pytest.raises(IntegrityError, match="UNIQUE"):
        await migrated.execute(ins)
    await migrated.rollback()
    await migrated.execute(text("INSERT INTO audit_log (at, actor, action, detail) VALUES ('t', 'local-admin', 'system.restart', '{}')"))
    assert (await migrated.execute(text("SELECT action FROM audit_log"))).scalar() == "system.restart"


async def test_idempotent_down_removes_them_and_up_reapplies(migrated):
    await v039_plugin_install.up(migrated)
    await v039_plugin_install.down(migrated)
    assert not {"installed_plugins", "audit_log"} & await _tables(migrated)
    await v039_plugin_install.up(migrated)
    assert {"installed_plugins", "audit_log"} <= await _tables(migrated)
