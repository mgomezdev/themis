"""Printer identity (BIZ-251/262): every printer gets the plugin, manufacturer and model it is, backfilled from the legacy
`printer_type`. Connection settings, bed size and every other column are untouched; `printer_type` stays for one release."""
from __future__ import annotations

from sqlalchemy import text

version = 42
name = "printer_model_identity"

# legacy printer_type -> (plugin_id, manufacturer_id, model_id). Bambu's tested model is the migration target only: the
# plugin offers every Bambu model.
LEGACY_IDENTITY = {
    "bambu": ("bambu", "bambu", "p1s"),
    "elegoo_centauri": ("elegoo_centauri", "elegoo", "centauri"),
    "snapmaker_extended": ("snapmaker", "snapmaker", "u1_extended"),
    "mock": ("mock", "mock", "mock"),
}
_COLUMNS = ("plugin_id", "manufacturer_id", "model_id")


async def _columns(conn) -> set[str]:
    return {r[1] for r in (await conn.execute(text("PRAGMA table_info(printers)"))).fetchall()}


async def up(conn) -> None:
    have = await _columns(conn)
    for col in _COLUMNS:
        if col not in have:
            await conn.execute(text(f"ALTER TABLE printers ADD COLUMN {col} VARCHAR(64)"))
    for legacy, (plugin_id, manufacturer_id, model_id) in LEGACY_IDENTITY.items():
        await conn.execute(
            text("UPDATE printers SET plugin_id = :p, manufacturer_id = :m, model_id = :d"
                 " WHERE printer_type = :t AND plugin_id IS NULL"),
            {"p": plugin_id, "m": manufacturer_id, "d": model_id, "t": legacy})


async def down(conn) -> None:
    have = await _columns(conn)
    for col in _COLUMNS:
        if col in have:
            await conn.execute(text(f"ALTER TABLE printers DROP COLUMN {col}"))
