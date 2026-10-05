"""Plugin host core tables (BIZ-205): plugin_configs, extension_slots, plugin_schema_versions."""
from __future__ import annotations
from sqlalchemy import text

version = 34
name = "plugin_host"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS plugin_configs (
            plugin_id VARCHAR(64) PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT 0,
            settings JSON NOT NULL DEFAULT '{}',
            secrets JSON NOT NULL DEFAULT '{}',
            state JSON NOT NULL DEFAULT '{}',
            updated_at VARCHAR(32)
        )
    """))
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS extension_slots (
            kind VARCHAR(64) PRIMARY KEY,
            plugin_id VARCHAR(64)
        )
    """))
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS plugin_schema_versions (
            plugin_id VARCHAR(64) NOT NULL,
            version INTEGER NOT NULL,
            name VARCHAR(200) NOT NULL,
            applied_at VARCHAR(32) NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            PRIMARY KEY (plugin_id, version)
        )
    """))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS plugin_schema_versions"))
    await conn.execute(text("DROP TABLE IF EXISTS extension_slots"))
    await conn.execute(text("DROP TABLE IF EXISTS plugin_configs"))
