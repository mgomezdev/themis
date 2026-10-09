"""Capability selections (replaces extension_slots) and drop installed_plugins.kind (capability model).

`down` only removes capability_selections: it does not resurrect the legacy slot table or `kind` column (the prerelease capability
model does not support running pre-capability code against a downgraded database).

`ALTER TABLE ... DROP COLUMN` needs SQLite 3.35+: the Docker image (python:3.11-slim, Debian bookworm) ships 3.40 and python.org's
Windows 3.11 builds ship 3.4x, so no table rebuild is needed."""
from __future__ import annotations
from sqlalchemy import text

version = 40
name = "capability_selections"

_LEGACY = {"filament_inventory": "inventory.filament"}


async def _has_table(conn, table: str) -> bool:
    return (await conn.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"), {"n": table})).first() is not None


async def _has_column(conn, table: str, column: str) -> bool:
    return any(r[1] == column for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall())


async def up(conn) -> None:
    await conn.execute(text("""CREATE TABLE IF NOT EXISTS capability_selections (
        capability VARCHAR(96) PRIMARY KEY, plugin_id VARCHAR(64), explicit BOOLEAN NOT NULL DEFAULT 0)"""))
    if await _has_table(conn, "extension_slots"):
        for kind, plugin_id in (await conn.execute(text("SELECT kind, plugin_id FROM extension_slots"))).fetchall():
            await conn.execute(text("INSERT OR IGNORE INTO capability_selections (capability, plugin_id, explicit) VALUES (:c, :p, 1)"),
                               {"c": _LEGACY.get(kind, kind), "p": plugin_id})
        await conn.execute(text("DROP TABLE extension_slots"))
    if await _has_column(conn, "installed_plugins", "kind"):
        await conn.execute(text("ALTER TABLE installed_plugins DROP COLUMN kind"))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS capability_selections"))
