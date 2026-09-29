import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.exc import IntegrityError
from app.database import Base
from app.models import ApiKey, Printer
from app.migrations import v012_api_keys, v013_filament_any_keyword, v014_api_key_expiration, v016_clear_stored_path, v017_notification_config
from app.migrations.runner import _MIGRATIONS, rollback_last, run_migrations


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
async def test_v016_clears_stored_path_only_for_indexed_files():
    """v016 blanks the legacy absolute stored_path column for rows that have been
    indexed into the library (relative_path set) - it must leave un-migrated legacy
    rows (relative_path == '') untouched, since migrate_legacy_uploads() still needs
    their stored_path to locate the file on disk."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("""
            INSERT INTO uploaded_files
                (original_filename, stored_path, relative_path, folder,
                 size_bytes, content_hash, mtime, missing, plates, uploaded_at)
            VALUES
                ('indexed.3mf', '/data/library/indexed.3mf', 'indexed.3mf', '/',
                 0, '', 0.0, 0, '[]', 't'),
                ('legacy.3mf', '/data/uploads/abc/legacy.3mf', '', '/',
                 0, '', 0.0, 0, '[]', 't')
        """))
        await v016_clear_stored_path.up(conn)
        rows = (await conn.execute(
            text("SELECT original_filename, stored_path, relative_path FROM uploaded_files ORDER BY id")
        )).fetchall()
    assert rows[0] == ("indexed.3mf", "", "indexed.3mf")
    assert rows[1] == ("legacy.3mf", "/data/uploads/abc/legacy.3mf", "")
    await engine.dispose()


@pytest.mark.asyncio
async def test_v017_creates_notification_config_table_with_seeded_row():
    """v017 creates the notification_config singleton table (id=1, same pattern
    as webhook_config) with per-channel enabled flags and JSON event-list columns."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)  # idempotent second run

        tables = {r[0] for r in (await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )).fetchall()}
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(notification_config)"))).fetchall()}
        row = (await conn.execute(text(
            "SELECT id, ntfy_enabled, ntfy_events, discord_enabled, discord_events, "
            "email_enabled, email_to_addrs, email_events FROM notification_config WHERE id = 1"
        ))).fetchone()
    assert "notification_config" in tables
    assert {
        "id", "ntfy_enabled", "ntfy_server_url", "ntfy_topic", "ntfy_priority", "ntfy_events",
        "discord_enabled", "discord_webhook_url", "discord_events",
        "email_enabled", "email_host", "email_port", "email_username",
        "email_password", "email_from_addr", "email_to_addrs", "email_events",
    } <= cols
    assert row == (1, 0, "[]", 0, "[]", 0, "[]", "[]")
    await engine.dispose()


@pytest.mark.asyncio
async def test_v017_up_down_roundtrip():
    """v017.down() drops the table cleanly (mirrors webhook_config's down())."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await v017_notification_config.up(conn)
        tables = {r[0] for r in (await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )).fetchall()}
        assert "notification_config" in tables

        await v017_notification_config.down(conn)
        tables = {r[0] for r in (await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )).fetchall()}
        assert "notification_config" not in tables
    await engine.dispose()


@pytest.mark.asyncio
async def test_migrate_adds_share_token_to_projects_idempotently():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)  # idempotent — second run must not raise
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(projects)"))).fetchall()}
    assert "share_token" in cols
    assert "share_token_created_at" in cols
    await engine.dispose()


# ---------------------------------------------------------------------------
# Runner / chain behavior. `run_migrations` skips versions already recorded in
# schema_migrations, so calling it twice never re-executes an `up()` — the
# idempotency guards inside each migration are only exercised by calling `up()`
# directly (below).
# ---------------------------------------------------------------------------

async def _schema(conn) -> dict:
    """{table: {column: (type, notnull, default, pk)}} — column order ignored (ALTER appends)."""
    tables = [r[0] for r in (await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    )).fetchall()]
    out = {}
    for t in tables:
        out[t] = {r[1]: (r[2], r[3], r[4], r[5])
                  for r in (await conn.execute(text(f"PRAGMA table_info({t})"))).fetchall()}
    return out


async def _dump(conn, tables) -> dict:
    return {t: [tuple(r) for r in (await conn.execute(text(f"SELECT * FROM {t} ORDER BY 1"))).fetchall()]
            for t in tables}


async def test_every_migration_up_twice_is_a_noop():
    """Direct second `up()` on a fully migrated DB: schema and rows unchanged, nothing raises."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await run_migrations(conn)
        # Core inserts apply the models' Python-side column defaults.
        await conn.execute(Printer.__table__.insert().values(
            id=1, name="P1", printer_type="mock", connection_config={}))
        await conn.execute(ApiKey.__table__.insert().values(
            id=1, name="k", key_prefix="pfx", key_hash="h", scopes=["apikeys:write", "customers:read", "customers:write"],
            created_at="2026-01-01T00:00:00"))
        tables = list(await _schema(conn))
        baseline_schema = await _schema(conn)
        baseline_rows = await _dump(conn, tables)

        for m in _MIGRATIONS:
            await m.up(conn)
            assert await _schema(conn) == baseline_schema, f"v{m.version} {m.name} changed the schema"
            assert await _dump(conn, tables) == baseline_rows, f"v{m.version} {m.name} changed rows"
    await engine.dispose()


async def test_upgrade_from_legacy_database_applies_every_version_in_order_and_keeps_rows(monkeypatch):
    """A pre-migration install (old-shaped tables holding real rows) upgrades in version order."""
    applied_order: list[int] = []
    for m in _MIGRATIONS:
        orig_up = m.up

        async def spy(conn, _orig=orig_up, _v=m.version):
            applied_order.append(_v)
            await _orig(conn)
        monkeypatch.setattr(m, "up", spy)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE printers (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL, "
            "printer_type VARCHAR(50) NOT NULL, connection_config JSON NOT NULL, "
            "awaiting_plate_clear BOOLEAN, orca_printer_profiles JSON, "
            "current_orca_printer_profile VARCHAR(255), enabled BOOLEAN)"
        ))
        await conn.execute(text(
            "CREATE TABLE uploaded_files (id INTEGER PRIMARY KEY, original_filename VARCHAR, "
            "stored_path VARCHAR, plates JSON, uploaded_at VARCHAR)"
        ))
        await conn.execute(text(
            "CREATE TABLE queue_config (id INTEGER PRIMARY KEY, check_interval_minutes INTEGER)"
        ))
        await conn.execute(text(
            "INSERT INTO printers VALUES (7, 'Legacy P', 'bambu', '{\"ip\": \"10.0.0.7\"}', 1, '[]', NULL, 1)"
        ))
        await conn.execute(text(
            "INSERT INTO uploaded_files VALUES (3, 'old.3mf', '/data/uploads/old.3mf', '[]', '2025-01-01T00:00:00')"
        ))
        await conn.execute(text("INSERT INTO queue_config VALUES (1, 9)"))

        await run_migrations(conn)

        # Every migration ran exactly once, oldest first.
        assert applied_order == list(range(1, len(_MIGRATIONS) + 1))
        recorded = (await conn.execute(text("SELECT version, name FROM schema_migrations ORDER BY version"))).fetchall()
        assert [tuple(r) for r in recorded] == [(m.version, m.name) for m in _MIGRATIONS]

        # Pre-existing rows survive, new columns arrive with their declared defaults.
        printer = (await conn.execute(text(
            "SELECT name, printer_type, connection_config, awaiting_plate_clear, queue_on, "
            "loaded_filaments, no_snapshots_while_idle, bed_x_mm, bed_y_mm, "
            "lifetime_job_count, lifetime_print_seconds FROM printers WHERE id = 7"
        ))).one()
        assert tuple(printer) == ("Legacy P", "bambu", '{"ip": "10.0.0.7"}', 1, 1, "[]", 0, 256.0, 256.0, 0, 0)

        upload = (await conn.execute(text(
            "SELECT original_filename, stored_path, relative_path, folder, size_bytes, "
            "content_hash, mtime, missing FROM uploaded_files WHERE id = 3"
        ))).one()
        # Legacy row is not indexed (relative_path ''), so v016 must leave its stored_path alone.
        assert tuple(upload) == ("old.3mf", "/data/uploads/old.3mf", "", "/", 0, "", 0.0, 0)

        queue = (await conn.execute(text(
            "SELECT check_interval_minutes, operator_name, snapshot_interval_seconds, estimates_enabled "
            "FROM queue_config WHERE id = 1"
        ))).one()
        assert tuple(queue) == (9, None, 2, 0)

        # v015 still creates its (retired, model-less) table via raw SQL for fresh installs.
        tables = {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()}
        assert "bootstrap_sentinel" in tables

        # Later migrations' side effects landed too (seeded singleton rows).
        assert (await conn.execute(text("SELECT username FROM admin_account WHERE id = 1"))).scalar() == "admin"
        assert (await conn.execute(text("SELECT COUNT(*) FROM notification_config"))).scalar() == 1

        # Re-running is a true no-op: nothing re-applied, bookkeeping unchanged.
        applied_order.clear()
        before = await _dump(conn, ["schema_migrations"])
        await run_migrations(conn)
        assert applied_order == []
        assert await _dump(conn, ["schema_migrations"]) == before
    await engine.dispose()


async def test_rollback_last_undoes_only_the_newest_migration():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await run_migrations(conn)
        newest = _MIGRATIONS[-1]
        assert (newest.version, newest.name) == (22, "admin_account")
        before = await _schema(conn)
        assert "admin_account" in before and "admin_session" in before["api_keys"]

        await rollback_last(conn)

        after = await _schema(conn)
        assert "admin_account" not in after
        assert "admin_session" not in after["api_keys"]
        # Everything else is untouched.
        assert {t: c for t, c in after.items() if t != "api_keys"} == {
            t: c for t, c in before.items() if t not in ("api_keys", "admin_account")
        }
        assert set(before["api_keys"]) - set(after["api_keys"]) == {"admin_session"}
        versions = [r[0] for r in (await conn.execute(text("SELECT version FROM schema_migrations ORDER BY version"))).fetchall()]
        assert versions == list(range(1, newest.version))

        # The rolled-back version re-applies cleanly (column sets; create_all and ALTER spell defaults differently).
        await run_migrations(conn)
        reapplied = await _schema(conn)
        assert {t: set(c) for t, c in reapplied.items()} == {t: set(c) for t, c in before.items()}
        assert (await conn.execute(text("SELECT COUNT(*) FROM schema_migrations"))).scalar() == newest.version
    await engine.dispose()


async def test_rollback_last_on_empty_history_is_a_noop(capsys):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await rollback_last(conn)
        assert "Nothing to roll back." in capsys.readouterr().out
        assert (await conn.execute(text("SELECT COUNT(*) FROM schema_migrations"))).scalar() == 0
    await engine.dispose()


async def test_rollback_last_refuses_unknown_recorded_version():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await run_migrations(conn)
        await conn.execute(text("INSERT INTO schema_migrations (version, name) VALUES (999, 'from_the_future')"))
        with pytest.raises(RuntimeError, match="v999 not found"):
            await rollback_last(conn)
        # Bookkeeping untouched by the refused rollback.
        assert (await conn.execute(text("SELECT COUNT(*) FROM schema_migrations WHERE version = 999"))).scalar() == 1
    await engine.dispose()


# ---------------------------------------------------------------------------
# `python -m app.migrations.migrate` CLI — the module runs at import time against
# the process-wide engine, so it is exercised in a subprocess with its own data dir.
# ---------------------------------------------------------------------------

def _run_migrate_cli(data_dir: Path, *args: str) -> subprocess.CompletedProcess:
    backend_dir = Path(__file__).resolve().parent.parent
    return subprocess.run(
        [sys.executable, "-m", "app.migrations.migrate", *args],
        cwd=backend_dir, capture_output=True, text=True, timeout=60,
        env={**os.environ, "THEMIS_DATA_DIR": str(data_dir)},
    )


def _versions_in(db_file: Path) -> list[int]:
    import sqlite3
    con = sqlite3.connect(db_file)
    try:
        return [r[0] for r in con.execute("SELECT version FROM schema_migrations ORDER BY version")]
    finally:
        con.close()


def test_migrate_cli_up_then_down(tmp_path):
    db_file = tmp_path / "themis.db"
    total = len(_MIGRATIONS)

    up = _run_migrate_cli(tmp_path, "up")
    assert up.returncode == 0, up.stderr
    assert "Done." in up.stdout
    assert _versions_in(db_file) == list(range(1, total + 1))

    again = _run_migrate_cli(tmp_path, "up")
    assert again.returncode == 0, again.stderr
    assert "Applying migration" not in again.stdout
    assert _versions_in(db_file) == list(range(1, total + 1))

    down = _run_migrate_cli(tmp_path, "down")
    assert down.returncode == 0, down.stderr
    assert f"Rolling back v{total}: {_MIGRATIONS[-1].name}" in down.stdout
    assert _versions_in(db_file) == list(range(1, total))


@pytest.mark.parametrize("args", [(), ("sideways",)])
def test_migrate_cli_rejects_missing_or_unknown_command(tmp_path, args):
    result = _run_migrate_cli(tmp_path, *args)
    assert result.returncode == 1
    assert "Usage: python -m app.migrations.migrate up|down" in result.stderr
    # Nothing was applied (the DB file may exist — opening the engine creates it — but stays empty).
    import sqlite3
    con = sqlite3.connect(tmp_path / "themis.db")
    try:
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0
    finally:
        con.close()
