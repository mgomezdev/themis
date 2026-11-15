"""Review fit check (BIZ-363): printers.bed_z_mm and the part_sizes cache. Idempotent."""
from __future__ import annotations
from sqlalchemy import text

version = 64
name = "part_sizes"


async def up(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
    if "bed_z_mm" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN bed_z_mm FLOAT"))
    await conn.execute(text(
        "CREATE TABLE IF NOT EXISTS part_sizes (part_key VARCHAR(200) PRIMARY KEY, size_x_mm FLOAT NOT NULL, "
        "size_y_mm FLOAT NOT NULL, size_z_mm FLOAT NOT NULL, measured_at VARCHAR(40) NOT NULL)"))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS part_sizes"))
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}
    if "bed_z_mm" in cols:
        await conn.execute(text("ALTER TABLE printers DROP COLUMN bed_z_mm"))
