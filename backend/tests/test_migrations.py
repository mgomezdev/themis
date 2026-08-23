import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.exc import IntegrityError
from app.database import Base
from app.migrations import v012_api_keys, v013_filament_any_keyword, v014_api_key_expiration, v015_bootstrap_sentinel
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


@pytest.mark.asyncio
async def test_v012_adds_api_keys_table():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)  # idempotent second run
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(api_keys)"))).fetchall()}
        tables = {r[0] for r in (await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )).fetchall()}
    assert "api_keys" in tables
    assert {"name", "key_prefix", "key_hash", "scopes", "enabled", "created_at", "last_used_at"} <= cols
    await engine.dispose()


@pytest.mark.asyncio
async def test_v013_backfills_and_locks_filament_any_keyword():
    """Legacy NULL/blank filament_type/filament_color get backfilled to 'any', and
    the columns become NOT NULL. Built against a hand-rolled pre-v012 (nullable)
    table since Base.metadata already reflects the post-migration schema."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE job_printer_configs (
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL,
                printer_id INTEGER NOT NULL,
                print_profile VARCHAR(512) NOT NULL,
                filament_profile VARCHAR(512),
                filament_id INTEGER,
                filament_type VARCHAR(100),
                filament_color VARCHAR(20),
                tool_index INTEGER,
                filament_map JSON,
                slice_failed BOOLEAN NOT NULL DEFAULT 0,
                slice_error TEXT
            )
        """))
        await conn.execute(text("""
            INSERT INTO job_printer_configs
                (id, job_id, printer_id, print_profile, filament_type, filament_color)
            VALUES
                (1, 1, 1, 'p1', NULL, NULL),
                (2, 1, 1, 'p1', '', 'blue'),
                (3, 1, 1, 'p1', 'PLA', 'any')
        """))

        await v013_filament_any_keyword.up(conn)

        rows = (await conn.execute(text(
            "SELECT id, filament_type, filament_color FROM job_printer_configs ORDER BY id"
        ))).fetchall()
        info = (await conn.execute(text("PRAGMA table_info(job_printer_configs)"))).fetchall()
    notnull = {r[1]: r[3] for r in info}
    assert notnull["filament_type"] == 1
    assert notnull["filament_color"] == 1
    assert [tuple(r[1:]) for r in rows] == [("any", "any"), ("any", "blue"), ("PLA", "any")]
    await engine.dispose()


@pytest.mark.asyncio
async def test_v012_creates_api_keys_table_with_unique_prefix_index():
    """v012 creates api_keys table with unique key_prefix index. Verify the table
    and index exist, and that the unique constraint is enforced."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await v012_api_keys.up(conn)

        tables = {r[0] for r in (await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )).fetchall()}
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(api_keys)"))).fetchall()}

        assert "api_keys" in tables
        assert {"id", "name", "key_prefix", "key_hash", "scopes", "enabled", "created_at", "last_used_at", "revoked_at"} <= cols

        # Verify unique index enforces uniqueness on key_prefix
        await conn.execute(text("""
            INSERT INTO api_keys (name, key_prefix, key_hash, scopes, enabled, created_at)
            VALUES ('key1', 'prefix1', 'hash1', '[]', 1, '2024-01-01T00:00:00')
        """))

        with pytest.raises(IntegrityError):
            await conn.execute(text("""
                INSERT INTO api_keys (name, key_prefix, key_hash, scopes, enabled, created_at)
                VALUES ('key2', 'prefix1', 'hash2', '[]', 1, '2024-01-01T00:00:00')
            """))
    await engine.dispose()


@pytest.mark.asyncio
async def test_v014_adds_nullable_expires_at_to_api_keys():
    """v014 adds nullable expires_at column to api_keys. Start from pre-v014 schema
    (run v012 first to get realistic prior state), then verify expires_at exists
    and accepts NULL."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # Build pre-v014 schema
        await v012_api_keys.up(conn)

        # Insert a row to test NULL insert
        await conn.execute(text("""
            INSERT INTO api_keys (name, key_prefix, key_hash, scopes, enabled, created_at)
            VALUES ('test_key', 'test_prefix', 'hash1', '[]', 1, '2024-01-01T00:00:00')
        """))

        # Run v014
        await v014_api_key_expiration.up(conn)

        # Verify expires_at column exists
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(api_keys)"))).fetchall()}
        assert "expires_at" in cols

        # Verify NULL is accepted (row inserted before migration should still exist)
        rows = (await conn.execute(text("SELECT expires_at FROM api_keys WHERE name = 'test_key'"))).fetchall()
        assert len(rows) == 1
        assert rows[0][0] is None

        # Verify we can insert a new row with expires_at as NULL
        await conn.execute(text("""
            INSERT INTO api_keys (name, key_prefix, key_hash, scopes, enabled, created_at, expires_at)
            VALUES ('key_with_null_exp', 'prefix2', 'hash2', '[]', 1, '2024-01-01T00:00:00', NULL)
        """))

        # Verify we can insert a row with a value
        await conn.execute(text("""
            INSERT INTO api_keys (name, key_prefix, key_hash, scopes, enabled, created_at, expires_at)
            VALUES ('key_with_exp', 'prefix3', 'hash3', '[]', 1, '2024-01-01T00:00:00', '2025-01-01T00:00:00')
        """))
    await engine.dispose()


@pytest.mark.asyncio
async def test_v015_creates_bootstrap_sentinel_table():
    """v015 creates bootstrap_sentinel table for atomic bootstrap-key minting.
    Verify the table exists with expected columns and can round-trip data."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await v015_bootstrap_sentinel.up(conn)

        tables = {r[0] for r in (await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )).fetchall()}
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(bootstrap_sentinel)"))).fetchall()}

        assert "bootstrap_sentinel" in tables
        assert {"id", "created_at"} <= cols

        # Insert a row and verify round-trip
        test_timestamp = "2024-01-01T12:34:56"
        await conn.execute(text("""
            INSERT INTO bootstrap_sentinel (id, created_at)
            VALUES (1, :ts)
        """), {"ts": test_timestamp})

        rows = (await conn.execute(text("SELECT id, created_at FROM bootstrap_sentinel WHERE id = 1"))).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 1
        assert rows[0][1] == test_timestamp
    await engine.dispose()
