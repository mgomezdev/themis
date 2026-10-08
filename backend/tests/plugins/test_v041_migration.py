"""v041 adds the conflict-guard columns to `inventory_pending_writes` (BIZ-198): idempotent, reversible, ORM-compatible."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v041_pending_write_conflict
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


async def _cols(conn) -> set[str]:
    return {r[1] for r in (await conn.execute(text("PRAGMA table_info(inventory_pending_writes)"))).fetchall()}


async def test_adds_the_columns_and_existing_rows_keep_null_so_they_skip_the_check(migrated):
    assert {"pre_weight_g", "conflict_current_g"} <= await _cols(migrated)
    await migrated.execute(text("INSERT INTO inventory_pending_writes (provider, spool_ref, target_g, source, created_at, status) "
                                "VALUES ('spoolman', '1', 5, 'queue', 't', 'pending')"))
    row = (await migrated.execute(text("SELECT pre_weight_g, conflict_current_g FROM inventory_pending_writes"))).one()
    assert tuple(row) == (None, None)


async def test_idempotent_down_removes_them_and_up_reapplies(migrated):
    await v041_pending_write_conflict.up(migrated)
    await v041_pending_write_conflict.down(migrated)
    assert not {"pre_weight_g", "conflict_current_g"} & await _cols(migrated)
    await v041_pending_write_conflict.up(migrated)
    assert {"pre_weight_g", "conflict_current_g"} <= await _cols(migrated)
