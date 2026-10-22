"""v038 adds `inventory_cache` (BIZ-219): on the phase-0 fixture, idempotent, `down()`, fresh DB, one row per provider+kind."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base
from app.migrations import v038_inventory_cache
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


async def _has(conn) -> bool:
    return (await conn.execute(text("SELECT 1 FROM sqlite_master WHERE name='inventory_cache'"))).first() is not None


async def test_creates_the_table_with_a_primary_key_per_provider_and_kind(migrated):
    assert await _has(migrated)
    ins = text("INSERT INTO inventory_cache (provider, kind, payload, fetched_at) VALUES ('spoolman', :k, '[]', 'x')")
    await migrated.execute(ins, {"k": "spools"})
    await migrated.execute(ins, {"k": "materials"})                      # same provider, other kind: fine
    with pytest.raises(IntegrityError, match="UNIQUE"):
        await migrated.execute(ins, {"k": "spools"})
    await migrated.rollback()


async def test_idempotent_down_removes_it_and_up_reapplies(migrated):
    await v038_inventory_cache.up(migrated)
    await v038_inventory_cache.down(migrated)
    assert not await _has(migrated)
    await v038_inventory_cache.up(migrated)
    assert await _has(migrated)


async def test_a_fresh_install_gets_the_table(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    async with engine.connect() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)
        assert await _has(conn)
    await engine.dispose()
