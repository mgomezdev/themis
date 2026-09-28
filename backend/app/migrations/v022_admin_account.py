"""Admin account singleton (first boot creates it, no password) + admin session marker on api_keys."""
from __future__ import annotations
from sqlalchemy import text

version = 22
name = "admin_account"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS admin_account (
            id INTEGER PRIMARY KEY,
            username VARCHAR(64) NOT NULL DEFAULT 'admin',
            password_hash VARCHAR(255),
            allow_local_login BOOLEAN NOT NULL DEFAULT 1,
            recovery_code_hash VARCHAR(64),
            recovery_code_expires_at VARCHAR(32),
            recovery_attempts INTEGER NOT NULL DEFAULT 0
        )
    """))
    await conn.execute(text("INSERT OR IGNORE INTO admin_account (id, username) VALUES (1, 'admin')"))

    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(api_keys)"))).fetchall()}
    if "admin_session" not in cols:
        await conn.execute(text("ALTER TABLE api_keys ADD COLUMN admin_session BOOLEAN NOT NULL DEFAULT 0"))


async def down(conn) -> None:
    await conn.execute(text("ALTER TABLE api_keys DROP COLUMN admin_session"))
    await conn.execute(text("DROP TABLE IF EXISTS admin_account"))
