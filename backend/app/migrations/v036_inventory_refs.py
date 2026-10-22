"""Provider-namespaced inventory refs (BIZ-217): add, backfill, (dual-write in the app), drop later.

* `printers.loaded_filaments[].inventory = {"provider": "spoolman", "spool_ref": "<id>"}` next to `spoolman_spool_id`. The key is
  `inventory`, never `filament_id` (a Bambu AMS tray code).
* `material_provider` / `material_ref` (TEXT, NULL) on `job_printer_configs`, `job_model_targets`, `project_items`, backfilled
  `('spoolman', CAST(filament_id AS TEXT))`; the same pair added to every `orders.parts[]` entry that has a `filament_id`.

Nothing legacy is removed: `filament_id` / `spoolman_spool_id` stay (written in step by the app while the provider is Spoolman).
Idempotent; `down()` removes only what `up` added."""
from __future__ import annotations

import json

from sqlalchemy import text

version = 36
name = "inventory_refs"

_TABLES = ("job_printer_configs", "job_model_targets", "project_items")
_PROVIDER = "spoolman"


def _loads(raw, default):
    try:
        value = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except Exception:
        return default
    return value if isinstance(value, type(default)) else default


async def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def up(conn) -> None:
    for table in _TABLES:
        have = await _columns(conn, table)
        if not have:
            continue
        if "material_provider" not in have:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN material_provider VARCHAR(64)"))
        if "material_ref" not in have:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN material_ref VARCHAR(128)"))
        await conn.execute(text(
            f"UPDATE {table} SET material_provider = :p, material_ref = CAST(filament_id AS TEXT) "
            "WHERE filament_id IS NOT NULL AND material_ref IS NULL"), {"p": _PROVIDER})

    for pid, slots_raw in (await conn.execute(text("SELECT id, loaded_filaments FROM printers"))).fetchall():
        slots = _loads(slots_raw, [])
        changed = False
        for slot in slots:
            if not isinstance(slot, dict) or slot.get("inventory"):
                continue
            legacy = slot.get("spoolman_spool_id")
            if legacy is not None and str(legacy).strip() != "":
                slot["inventory"] = {"provider": _PROVIDER, "spool_ref": str(legacy)}
                changed = True
        if changed:
            await conn.execute(text("UPDATE printers SET loaded_filaments = :s WHERE id = :i"), {"s": json.dumps(slots), "i": pid})

    for oid, parts_raw in (await conn.execute(text("SELECT id, parts FROM orders"))).fetchall():
        parts = _loads(parts_raw, [])
        changed = False
        for part in parts:
            if isinstance(part, dict) and part.get("filament_id") is not None and not part.get("material_ref"):
                part["material_provider"], part["material_ref"] = _PROVIDER, str(part["filament_id"])
                changed = True
        if changed:
            await conn.execute(text("UPDATE orders SET parts = :p WHERE id = :i"), {"p": json.dumps(parts), "i": oid})


async def down(conn) -> None:
    for oid, parts_raw in (await conn.execute(text("SELECT id, parts FROM orders"))).fetchall():
        parts = _loads(parts_raw, [])
        if any(isinstance(p, dict) and ("material_ref" in p or "material_provider" in p) for p in parts):
            for p in parts:
                if isinstance(p, dict):
                    p.pop("material_provider", None)
                    p.pop("material_ref", None)
            await conn.execute(text("UPDATE orders SET parts = :p WHERE id = :i"), {"p": json.dumps(parts), "i": oid})
    for pid, slots_raw in (await conn.execute(text("SELECT id, loaded_filaments FROM printers"))).fetchall():
        slots = _loads(slots_raw, [])
        if any(isinstance(s, dict) and "inventory" in s for s in slots):
            for s in slots:
                if isinstance(s, dict):
                    s.pop("inventory", None)
            await conn.execute(text("UPDATE printers SET loaded_filaments = :s WHERE id = :i"), {"s": json.dumps(slots), "i": pid})
    for table in _TABLES:
        have = await _columns(conn, table)
        for col in ("material_ref", "material_provider"):
            if col in have:
                await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))
