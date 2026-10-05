"""v037 adds the deduction tables + `jobs.deduction_note` (BIZ-218): on the phase-0 fixture (jobs mid-print keep working),
idempotent, `down()`, fresh DB."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base
from app.migrations import v037_inventory_deduction
from app.migrations.runner import run_migrations
from tests.v032_fixture import build_v032_fixture_db

NEW_TABLES = ("job_spool_snapshots", "inventory_pending_writes", "inventory_spool_status")


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


async def _tables(conn):
    return {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()}


async def _cols(conn, table):
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def test_creates_the_tables_and_the_job_note_column_without_touching_existing_jobs(migrated):
    assert set(NEW_TABLES) <= await _tables(migrated)
    assert "deduction_note" in await _cols(migrated, "jobs")
    notes = (await migrated.execute(text("SELECT deduction_note FROM jobs"))).fetchall()
    assert notes and all(n[0] is None for n in notes)                       # existing jobs: no note, rows intact


async def test_a_snapshot_is_unique_per_job_provider_and_spool_and_cascades_with_its_job(migrated):
    await migrated.execute(text("PRAGMA foreign_keys=ON"))
    job_id = (await migrated.execute(text(                        # a job with no other dependents, so it can be deleted
        "INSERT INTO jobs (uploaded_file_id, plate_number, queue_position, status, created_at, updated_at) "
        "SELECT uploaded_file_id, plate_number, queue_position, 'queued', created_at, updated_at FROM jobs LIMIT 1 "
        "RETURNING id"))).scalar_one()
    await migrated.commit()                                           # so the rollback below only undoes the duplicate
    ins = text("INSERT INTO job_spool_snapshots (job_id, provider, spool_ref, source, taken_at) "
               "VALUES (:j, 'spoolman', '1', 'live', 'x')")
    await migrated.execute(ins, {"j": job_id})
    with pytest.raises(IntegrityError, match="UNIQUE"):
        await migrated.execute(ins, {"j": job_id})
    await migrated.rollback()
    await migrated.execute(text("PRAGMA foreign_keys=ON"))
    await migrated.execute(ins, {"j": job_id})
    await migrated.execute(text("DELETE FROM jobs WHERE id=:j"), {"j": job_id})
    assert (await migrated.execute(text("SELECT COUNT(*) FROM job_spool_snapshots"))).scalar_one() == 0   # cascaded


async def test_running_twice_is_idempotent_and_down_removes_only_what_up_added(migrated):
    await v037_inventory_deduction.up(migrated)                              # second run: no error, no duplicate column
    await v037_inventory_deduction.down(migrated)
    assert not set(NEW_TABLES) & await _tables(migrated)
    assert "deduction_note" not in await _cols(migrated, "jobs") and "deduction_skipped" in await _cols(migrated, "jobs")
    await v037_inventory_deduction.up(migrated)                              # and it re-applies
    assert set(NEW_TABLES) <= await _tables(migrated)


async def test_a_fresh_install_gets_the_tables(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    async with engine.connect() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)
        assert set(NEW_TABLES) <= await _tables(conn)
    await engine.dispose()
