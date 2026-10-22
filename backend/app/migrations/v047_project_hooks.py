"""Reliable project hooks for companion apps (BIZ-172): `projects.external_ref` (unique with `source_app`), `idempotency_keys`, and
`webhook_destinations` (the legacy single `webhook_config` becomes the destination named `default`)."""
from __future__ import annotations
from datetime import datetime, timezone

from sqlalchemy import text

version = 47
name = "project_hooks"


async def up(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(projects)"))).fetchall()}
    if "external_ref" not in cols:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN external_ref VARCHAR(255)"))
    await conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_projects_source_external_ref ON projects (source_app, external_ref) "
        "WHERE external_ref IS NOT NULL"))
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope VARCHAR(160) NOT NULL,
            key VARCHAR(200) NOT NULL,
            request_hash VARCHAR(64) NOT NULL,
            state VARCHAR(12) NOT NULL DEFAULT 'in_progress',
            status_code INTEGER,
            response JSON,
            created_at VARCHAR(32) NOT NULL,
            CONSTRAINT ux_idempotency_scope_key UNIQUE (scope, key)
        )
    """))
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS webhook_destinations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(100) NOT NULL UNIQUE,
            url VARCHAR(1024),
            secret VARCHAR(256),
            events JSON NOT NULL DEFAULT '[]',
            enabled BOOLEAN NOT NULL DEFAULT 1,
            created_at VARCHAR(32) NOT NULL,
            updated_at VARCHAR(32) NOT NULL,
            last_attempt_at VARCHAR(32),
            last_success_at VARCHAR(32),
            last_status INTEGER,
            last_error TEXT
        )
    """))
    legacy = (await conn.execute(text("SELECT url, secret, events FROM webhook_config WHERE id = 1"))).fetchone()
    if legacy is not None and legacy[0]:
        now = datetime.now(timezone.utc).isoformat()
        await conn.execute(text(
            "INSERT OR IGNORE INTO webhook_destinations (name, url, secret, events, enabled, created_at, updated_at) "
            "VALUES ('default', :url, :secret, :events, 1, :now, :now)"),
            {"url": legacy[0], "secret": legacy[1], "events": legacy[2] if isinstance(legacy[2], str) else "[]", "now": now})


async def down(conn) -> None:
    # Note: destinations edited after the upgrade are lost; the old `webhook_config` row is whatever it was before v047.
    await conn.execute(text("DROP TABLE IF EXISTS webhook_destinations"))
    await conn.execute(text("DROP TABLE IF EXISTS idempotency_keys"))
    await conn.execute(text("DROP INDEX IF EXISTS ux_projects_source_external_ref"))
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(projects)"))).fetchall()}
    if "external_ref" in cols:
        await conn.execute(text("ALTER TABLE projects DROP COLUMN external_ref"))
