"""Persist per-print-group process intent. Existing groups keep default slicing behavior."""
from sqlalchemy import text

version = 68
name = "project_group_overrides"


async def up(conn):
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(project_groups)"))).fetchall()}
    if "overrides" not in cols:
        await conn.execute(text("ALTER TABLE project_groups ADD COLUMN overrides JSON"))


async def down(conn):
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(project_groups)"))).fetchall()}
    if "overrides" in cols:
        await conn.execute(text("ALTER TABLE project_groups DROP COLUMN overrides"))
