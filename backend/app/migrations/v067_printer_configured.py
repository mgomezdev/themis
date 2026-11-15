"""Printer configuration status (BIZ-369): `printers.configured` (only configured printers are queue-eligible) and `config_error`
(last connection-test failure / missing-field reason). Printers that existed before the column are back-filled configured so a
working fleet stays in the queue on upgrade. Idempotent."""
from __future__ import annotations

from sqlalchemy import text

version = 67
name = "printer_configured"


async def _cols(conn, table):
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def up(conn) -> None:
    cols = await _cols(conn, "printers")
    if "configured" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN configured BOOLEAN NOT NULL DEFAULT 0"))
        await conn.execute(text("UPDATE printers SET configured = 1"))
    if "config_error" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN config_error TEXT"))


async def down(conn) -> None:
    cols = await _cols(conn, "printers")
    if "config_error" in cols:
        await conn.execute(text("ALTER TABLE printers DROP COLUMN config_error"))
    if "configured" in cols:
        await conn.execute(text("ALTER TABLE printers DROP COLUMN configured"))
