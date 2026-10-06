"""Plugin installation (BIZ-223): `installed_plugins` (non-bundled packages) and the append-only `audit_log`."""
from __future__ import annotations
from sqlalchemy import text

version = 39
name = "plugin_install"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS installed_plugins (
            plugin_id VARCHAR(64) PRIMARY KEY,
            version VARCHAR(64) NOT NULL,
            name VARCHAR(200) NOT NULL DEFAULT '',
            kind VARCHAR(64) NOT NULL DEFAULT '',
            publisher VARCHAR(200),
            source VARCHAR(16) NOT NULL,
            source_url VARCHAR(500),
            ref VARCHAR(200),
            subdir VARCHAR(300),
            commit_sha VARCHAR(64),
            archive_sha256 VARCHAR(64) NOT NULL,
            installed_at VARCHAR(32) NOT NULL,
            status VARCHAR(24) NOT NULL,
            error TEXT,
            previous_version VARCHAR(64)
        )
    """))
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at VARCHAR(32) NOT NULL,
            actor VARCHAR(64) NOT NULL,
            action VARCHAR(64) NOT NULL,
            target VARCHAR(200),
            detail JSON NOT NULL DEFAULT '{}'
        )
    """))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS audit_log"))
    await conn.execute(text("DROP TABLE IF EXISTS installed_plugins"))
