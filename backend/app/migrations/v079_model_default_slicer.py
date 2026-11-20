"""Allow printers to inherit a slicing provider from their model."""
from sqlalchemy import text

version = 79
name = "model_default_slicer"


async def _has_column(conn) -> bool:
    return any(row[1] == "default_slicer_plugin_id"
               for row in (await conn.execute(text("PRAGMA table_info(printer_models)"))).fetchall())


async def up(conn) -> None:
    if not await _has_column(conn):
        await conn.execute(text("ALTER TABLE printer_models ADD COLUMN default_slicer_plugin_id VARCHAR(64)"))


async def down(conn) -> None:
    if await _has_column(conn):
        await conn.execute(text("ALTER TABLE printer_models DROP COLUMN default_slicer_plugin_id"))
