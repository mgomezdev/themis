import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.database import Base
from app.migrations.runner import run_migrations


@pytest.mark.asyncio
async def test_migrate_adds_tool_index_idempotently():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)  # idempotent — second run must not raise
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(job_printer_configs)"))).fetchall()}
    assert "tool_index" in cols
    await engine.dispose()


@pytest.mark.asyncio
async def test_migrate_adds_overrides_to_jobs():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)  # idempotent
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    assert "overrides" in cols
    await engine.dispose()


@pytest.mark.asyncio
async def test_migrate_adds_operator_name_to_queue_config():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)  # idempotent — second run must not raise
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(queue_config)"))).fetchall()}
    assert "operator_name" in cols
    await engine.dispose()


@pytest.mark.asyncio
async def test_v008_adds_estimate_columns():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)  # idempotent second run
        job_cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
        qc_cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(queue_config)"))).fetchall()}
    expected_job = {
        "actual_filament_grams", "actual_seconds", "actual_filament_breakdown",
        "deduction_skipped", "estimate_token", "estimate_status", "estimate_seconds",
        "estimate_filament_grams", "estimate_filament_breakdown", "estimate_preset_label",
    }
    assert expected_job <= job_cols
    assert "estimates_enabled" in qc_cols
    await engine.dispose()


@pytest.mark.asyncio
async def test_v011_adds_maintenance_tables_and_printer_counters():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)  # idempotent second run
        printer_cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
        tables = {r[0] for r in (await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )).fetchall()}
    assert {"lifetime_job_count", "lifetime_print_seconds"} <= printer_cols
    assert {"maintenance_items", "maintenance_triggers", "printer_maintenance_state"} <= tables
    await engine.dispose()
