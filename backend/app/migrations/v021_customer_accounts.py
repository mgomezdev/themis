"""Customer accounts, project stages, and customer-bound login sessions."""
from __future__ import annotations
import json

from sqlalchemy import text

version = 21
name = "customer_accounts"


async def _cols(conn, table: str) -> set[str]:
    return {row[1] for row in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            email VARCHAR(255) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            created_at VARCHAR(32) NOT NULL
        )
    """))

    project_cols = await _cols(conn, "projects")
    if "stage" not in project_cols:
        # Existing projects keep today's behavior (jobs go straight into the queue).
        await conn.execute(text(
            "ALTER TABLE projects ADD COLUMN stage VARCHAR(20) NOT NULL DEFAULT 'queued'"
        ))
    if "customer_id" not in project_cols:
        await conn.execute(text(
            "ALTER TABLE projects ADD COLUMN customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL"
        ))

    if "customer_id" not in await _cols(conn, "api_keys"):
        await conn.execute(text(
            "ALTER TABLE api_keys ADD COLUMN customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE"
        ))

    # Keys that can already mint any key (apikeys:write) get the new customer-admin
    # scopes, so an existing full-access browser key can manage customers.
    rows = (await conn.execute(text("SELECT id, scopes FROM api_keys"))).fetchall()
    for key_id, raw in rows:
        scopes = json.loads(raw) if isinstance(raw, str) else (raw or [])
        if "apikeys:write" in scopes:
            added = [s for s in ("customers:read", "customers:write") if s not in scopes]
            if added:
                await conn.execute(text("UPDATE api_keys SET scopes = :s WHERE id = :id"),
                                   {"s": json.dumps(scopes + added), "id": key_id})


async def down(conn) -> None:
    await conn.execute(text("ALTER TABLE api_keys DROP COLUMN customer_id"))
    await conn.execute(text("ALTER TABLE projects DROP COLUMN customer_id"))
    await conn.execute(text("ALTER TABLE projects DROP COLUMN stage"))
    await conn.execute(text("DROP TABLE IF EXISTS customers"))
