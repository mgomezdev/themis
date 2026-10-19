"""Filament matching tuple (spec 2026-10-19-filament-matching-design.md).

* `ask_manufacturer/ask_type/ask_subtype/ask_color` (nullable; NULL = undefined) on `job_printer_configs`, `job_model_targets`,
  `project_items`, backfilled from the legacy `filament_type`/`filament_color` ("" or "any" = undefined; a type becomes base type +
  subtype `any`, manufacturer undefined). The legacy columns stay as history.
* `filament_map` entries that carry a catalog ask (tool_index null) and `orders.parts[]` get the same tuple keys
  (`filament_manufacturer/filament_type/filament_subtype/filament_color`).
* `printers.loaded_filaments[]` slots get `subtype` (the type string split with the shared suffix rules; plain = standard) and
  `manufacturer` (unknown = null). Empty tool placeholders are left alone.
* `job_printer_configs.slot_plan` (JSON): the slots chosen when a printer claims the job.
* down() writes the tuple back into the legacy `filament_type`/`filament_color` (and the map / order-part keys) before dropping the new
  columns, so a rollback keeps every ask made since the upgrade (a tuple's subtype folds into the legacy type string; manufacturer is lost).
* Jobs `blocked` only because of a filament mismatch go back to `queued` (a filament mismatch no longer blocks a job).
Idempotent."""
from __future__ import annotations

import json

from sqlalchemy import text

from ..services.filament_tuple import ask_from_legacy, slot_from_legacy

version = 53
name = "filament_tuple"

_TABLES = ("job_printer_configs", "job_model_targets", "project_items")
_COLUMNS = (("ask_manufacturer", "VARCHAR(200)"), ("ask_type", "VARCHAR(100)"), ("ask_subtype", "VARCHAR(100)"), ("ask_color", "VARCHAR(20)"))
# The block reasons the queue engine used to write for a filament mismatch (see git history of queue_engine._filament_mismatch).
_FILAMENT_BLOCK_REASONS = ("a mapped tool has no loaded filament", "required filament %", "tool T% has no loaded filament",
                           "loaded filament doesn't match required%")


def _loads(raw, default):
    try:
        value = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except Exception:
        return default
    return value if isinstance(value, type(default)) else default


async def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


def _tuple_keys(entry: dict) -> dict:
    t = ask_from_legacy(entry.get("filament_type"), entry.get("filament_color"))
    return {"filament_manufacturer": t.manufacturer, "filament_type": t.type, "filament_subtype": t.subtype, "filament_color": t.color}


def _migrate_map(raw) -> tuple[list | None, bool]:
    entries = _loads(raw, [])
    changed = False
    for e in entries:
        if isinstance(e, dict) and e.get("tool_index") is None and "filament_subtype" not in e:
            e.update(_tuple_keys(e))
            e.pop("filament_id", None)
            changed = True
    return entries, changed


async def up(conn) -> None:
    for table in _TABLES:
        have = await _columns(conn, table)
        if not have:
            continue
        for col, ddl in _COLUMNS:
            if col not in have:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        rows = (await conn.execute(text(
            f"SELECT id, filament_type, filament_color FROM {table} "
            "WHERE ask_manufacturer IS NULL AND ask_type IS NULL AND ask_subtype IS NULL AND ask_color IS NULL"))).fetchall()
        for row_id, ftype, fcolor in rows:
            t = ask_from_legacy(ftype, fcolor)
            if t.is_undefined():
                continue
            await conn.execute(text(f"UPDATE {table} SET ask_manufacturer = :m, ask_type = :t, ask_subtype = :s, ask_color = :c WHERE id = :i"),
                               {"m": t.manufacturer, "t": t.type, "s": t.subtype, "c": t.color, "i": row_id})
        if "filament_map" in have:
            for row_id, raw in (await conn.execute(text(f"SELECT id, filament_map FROM {table} WHERE filament_map IS NOT NULL"))).fetchall():
                entries, changed = _migrate_map(raw)
                if changed:
                    await conn.execute(text(f"UPDATE {table} SET filament_map = :m WHERE id = :i"), {"m": json.dumps(entries), "i": row_id})

    for oid, parts_raw in (await conn.execute(text("SELECT id, parts FROM orders"))).fetchall():
        parts = _loads(parts_raw, [])
        changed = False
        for part in parts:
            if isinstance(part, dict) and "filament_subtype" not in part and ("filament_color" in part or "filament_type" in part):
                part.update(_tuple_keys(part))
                changed = True
        if changed:
            await conn.execute(text("UPDATE orders SET parts = :p WHERE id = :i"), {"p": json.dumps(parts), "i": oid})

    if "slot_plan" not in await _columns(conn, "job_printer_configs"):
        await conn.execute(text("ALTER TABLE job_printer_configs ADD COLUMN slot_plan JSON"))

    for pid, slots_raw in (await conn.execute(text("SELECT id, loaded_filaments FROM printers"))).fetchall():
        slots = _loads(slots_raw, [])
        changed = False
        for slot in slots:
            if not isinstance(slot, dict) or not str(slot.get("type") or "").strip() or "subtype" in slot:
                continue
            t = slot_from_legacy(slot)
            slot["type"], slot["subtype"] = t.type, t.subtype
            slot.setdefault("manufacturer", None)
            changed = True
        if changed:
            await conn.execute(text("UPDATE printers SET loaded_filaments = :s WHERE id = :i"), {"s": json.dumps(slots), "i": pid})

    clause = " OR ".join("block_reason LIKE :r%d" % i for i in range(len(_FILAMENT_BLOCK_REASONS)))
    await conn.execute(text(f"UPDATE jobs SET status = 'queued', block_reason = NULL WHERE status = 'blocked' AND ({clause})"),
                       {f"r{i}": r for i, r in enumerate(_FILAMENT_BLOCK_REASONS)})


def _legacy_pair(ask_type, ask_subtype, ask_color) -> tuple[str, str]:
    """A tuple ask as the pre-tuple (type string, colour): undefined = "any"; subtype any/standard folds away, plus -> "PLA+"."""
    if not ask_type:
        return "any", ask_color or "any"
    sub = (ask_subtype or "").casefold()
    legacy = ask_type if sub in ("", "any", "standard") else f"{ask_type}+" if sub == "plus" else f"{ask_type}-{ask_subtype}"
    return legacy, ask_color or "any"


def _restore_keys(entry: dict) -> bool:
    if not isinstance(entry, dict) or entry.get("tool_index") is not None or "filament_subtype" not in entry:
        return False
    entry["filament_type"], entry["filament_color"] = _legacy_pair(entry.get("filament_type"), entry.pop("filament_subtype"),
                                                                    entry.get("filament_color"))
    entry.pop("filament_manufacturer", None)
    return True


async def down(conn) -> None:
    for table in _TABLES:
        have = await _columns(conn, table)
        if "ask_type" in have:
            for row_id, atype, asub, acolor in (await conn.execute(text(f"SELECT id, ask_type, ask_subtype, ask_color FROM {table}"))).fetchall():
                ftype, fcolor = _legacy_pair(atype, asub, acolor)
                await conn.execute(text(f"UPDATE {table} SET filament_type = :t, filament_color = :c WHERE id = :i"),
                                   {"t": ftype, "c": fcolor, "i": row_id})
        if "filament_map" in have:
            for row_id, raw in (await conn.execute(text(f"SELECT id, filament_map FROM {table} WHERE filament_map IS NOT NULL"))).fetchall():
                entries = _loads(raw, [])
                if any([_restore_keys(e) for e in entries]):
                    await conn.execute(text(f"UPDATE {table} SET filament_map = :m WHERE id = :i"), {"m": json.dumps(entries), "i": row_id})
    for oid, parts_raw in (await conn.execute(text("SELECT id, parts FROM orders"))).fetchall():
        parts = _loads(parts_raw, [])
        if any([_restore_keys(p) for p in parts]):
            await conn.execute(text("UPDATE orders SET parts = :p WHERE id = :i"), {"p": json.dumps(parts), "i": oid})

    if "slot_plan" in await _columns(conn, "job_printer_configs"):
        await conn.execute(text("ALTER TABLE job_printer_configs DROP COLUMN slot_plan"))
    for table in _TABLES:
        have = await _columns(conn, table)
        for col, _ in _COLUMNS:
            if col in have:
                await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))
