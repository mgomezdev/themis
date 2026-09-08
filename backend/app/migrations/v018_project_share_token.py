"""Add share_token / share_token_created_at to projects for public share links."""
from __future__ import annotations
from sqlalchemy import text

version = 18
name = "project_share_token"


async def up(conn) -> None:
    info = (await conn.execute(text("PRAGMA table_info(projects)"))).fetchall()
    cols = {row[1] for row in info}
    if "share_token" not in cols:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN share_token VARCHAR(64)"))
    if "share_token_created_at" not in cols:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN share_token_created_at VARCHAR(32)"))
    await conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_share_token ON projects (share_token)"
    ))


async def down(conn) -> None:
    await conn.execute(text("DROP INDEX IF EXISTS uq_projects_share_token"))
    await conn.execute(text("ALTER TABLE projects DROP COLUMN share_token_created_at"))
    await conn.execute(text("ALTER TABLE projects DROP COLUMN share_token"))
