"""Materials get a `subtype` (filament matching spec 2026-10-19): `material` now holds the base type only. Existing combined strings
("PLA+", "PLA-CF", "PLA Silk") are split with the shared suffix rules; plain gets `standard`."""
from __future__ import annotations

from sqlalchemy import text

from ....services.filament_tuple import split_type

version = 2
name = "subtype"


async def up(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(local_inv_materials)"))).fetchall()}
    if "subtype" not in cols:
        await conn.execute(text("ALTER TABLE local_inv_materials ADD COLUMN subtype VARCHAR(64)"))
    rows = (await conn.execute(text("SELECT id, material FROM local_inv_materials WHERE material IS NOT NULL AND subtype IS NULL"))).fetchall()
    for row_id, raw in rows:
        base, subtype = split_type(raw)
        await conn.execute(text("UPDATE local_inv_materials SET material = :m, subtype = :s WHERE id = :i"),
                           {"m": base, "s": subtype, "i": row_id})


async def down(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(local_inv_materials)"))).fetchall()}
    if "subtype" in cols:
        await conn.execute(text("ALTER TABLE local_inv_materials DROP COLUMN subtype"))      # `material` stays the base type
