"""v043: core printer-model registry (BIZ-262). Every distinct (plugin, manufacturer, model) key already on a printer gets a
stable UUID row, and `printers.model_uuid` points at it. Display names are placeholders (the key's ids): the registry sync
fills them from the plugin manifests on startup without ever changing the UUID."""
from __future__ import annotations

import uuid

from sqlalchemy import text

version = 43
name = "printer_model_registry"


async def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS printer_models (
            id VARCHAR(36) PRIMARY KEY,
            plugin_id VARCHAR(64) NOT NULL,
            manufacturer_id VARCHAR(64) NOT NULL,
            model_id VARCHAR(64) NOT NULL,
            manufacturer_name VARCHAR(255) NOT NULL DEFAULT '',
            display_name VARCHAR(255) NOT NULL DEFAULT '',
            bed_x_mm FLOAT NOT NULL DEFAULT 256.0,
            bed_y_mm FLOAT NOT NULL DEFAULT 256.0,
            toolheads INTEGER NOT NULL DEFAULT 1,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            declared BOOLEAN NOT NULL DEFAULT 1,
            UNIQUE (plugin_id, manufacturer_id, model_id)
        )"""))
    if "model_uuid" not in await _columns(conn, "printers"):
        await conn.execute(text("ALTER TABLE printers ADD COLUMN model_uuid VARCHAR(36)"))
    keys = (await conn.execute(text(
        "SELECT DISTINCT plugin_id, manufacturer_id, model_id FROM printers "
        "WHERE plugin_id IS NOT NULL AND manufacturer_id IS NOT NULL AND model_id IS NOT NULL"))).fetchall()
    for plugin_id, manufacturer_id, model_id in keys:
        existing = (await conn.execute(text(
            "SELECT id FROM printer_models WHERE plugin_id=:p AND manufacturer_id=:m AND model_id=:d"),
            {"p": plugin_id, "m": manufacturer_id, "d": model_id})).scalar()
        uid = existing or str(uuid.uuid4())
        if not existing:
            await conn.execute(text(
                "INSERT INTO printer_models (id, plugin_id, manufacturer_id, model_id, manufacturer_name, display_name, "
                "bed_x_mm, bed_y_mm, toolheads, enabled, declared) VALUES (:i, :p, :m, :d, :m, :d, 256.0, 256.0, 1, 1, 1)"), {"i": uid, "p": plugin_id, "m": manufacturer_id, "d": model_id})
        await conn.execute(text(
            "UPDATE printers SET model_uuid=:i WHERE plugin_id=:p AND manufacturer_id=:m AND model_id=:d AND model_uuid IS NULL"),
            {"i": uid, "p": plugin_id, "m": manufacturer_id, "d": model_id})


async def down(conn) -> None:
    if "model_uuid" in await _columns(conn, "printers"):
        await conn.execute(text("ALTER TABLE printers DROP COLUMN model_uuid"))
    await conn.execute(text("DROP TABLE IF EXISTS printer_models"))
