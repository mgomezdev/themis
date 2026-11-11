"""Project item kinds (BIZ-356): project_items.kind (stl | 3mf_object | 3mf_plate | gcode, existing rows are STLs), source_ref,
pack_rule (split | together | solo | locked) and source_hash. Idempotent."""
from __future__ import annotations
from sqlalchemy import text

version = 61
name = "project_item_kinds"

_COLUMNS = (
    ("kind", "VARCHAR(20) NOT NULL DEFAULT 'stl'"),
    ("source_ref", "VARCHAR(64)"),
    ("pack_rule", "VARCHAR(20) NOT NULL DEFAULT 'split'"),
    ("source_hash", "VARCHAR(64)"),
)


async def _cols(conn) -> set[str]:
    return {r[1] for r in (await conn.execute(text("PRAGMA table_info(project_items)"))).fetchall()}


async def up(conn) -> None:
    for col, ddl in _COLUMNS:
        if col not in await _cols(conn):
            await conn.execute(text(f"ALTER TABLE project_items ADD COLUMN {col} {ddl}"))


async def down(conn) -> None:
    for col, _ in reversed(_COLUMNS):
        if col in await _cols(conn):
            await conn.execute(text(f"ALTER TABLE project_items DROP COLUMN {col}"))
