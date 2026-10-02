"""True cost of a job: shop machine/labour rates, per-printer rate override, project labour log, part unit costs."""
from __future__ import annotations
from sqlalchemy import text

version = 28
name = "job_costs"


async def _cols(conn, table: str) -> set[str]:
    return {row[1] for row in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS cost_config (
            id INTEGER PRIMARY KEY,
            machine_rate_per_hour REAL NOT NULL DEFAULT 0,
            labour_rate_per_hour REAL NOT NULL DEFAULT 0
        )
    """))
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS project_labor (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            minutes     INTEGER NOT NULL,
            note        TEXT,
            logged_on   VARCHAR(10) NOT NULL,
            created_at  VARCHAR(32) NOT NULL DEFAULT ''
        )
    """))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_project_labor_project_id ON project_labor (project_id)"))
    if "machine_rate_per_hour" not in await _cols(conn, "printers"):
        await conn.execute(text("ALTER TABLE printers ADD COLUMN machine_rate_per_hour REAL"))
    if "unit_cost" not in await _cols(conn, "project_parts"):
        await conn.execute(text("ALTER TABLE project_parts ADD COLUMN unit_cost REAL"))


async def down(conn) -> None:
    await conn.execute(text("ALTER TABLE project_parts DROP COLUMN unit_cost"))
    await conn.execute(text("ALTER TABLE printers DROP COLUMN machine_rate_per_hour"))
    await conn.execute(text("DROP TABLE IF EXISTS project_labor"))
    await conn.execute(text("DROP TABLE IF EXISTS cost_config"))
