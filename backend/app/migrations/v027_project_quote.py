"""projects.price_visible / quote_accepted_at: staff decide when the customer portal shows the quote; the customer can accept it."""
from __future__ import annotations
from sqlalchemy import text

version = 27
name = "project_quote"


async def up(conn) -> None:
    cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(projects)"))).fetchall()}
    if "price_visible" not in cols:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN price_visible BOOLEAN NOT NULL DEFAULT 0"))
    if "quote_accepted_at" not in cols:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN quote_accepted_at TEXT"))


async def down(conn) -> None:
    await conn.execute(text("ALTER TABLE projects DROP COLUMN quote_accepted_at"))
    await conn.execute(text("ALTER TABLE projects DROP COLUMN price_visible"))
