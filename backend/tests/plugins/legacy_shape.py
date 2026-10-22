"""Test helper: put a fully migrated database back into its pre-v040 shape (extension_slots, installed_plugins.kind), so the
older migrations can be tested in the shape they originally ran in. v040 itself has no `down` (it drops data)."""
from sqlalchemy import text


async def restore_pre_040_shape(conn) -> None:
    await conn.execute(text("CREATE TABLE IF NOT EXISTS extension_slots (kind VARCHAR(64) PRIMARY KEY, plugin_id VARCHAR(64))"))
    for cap, plugin_id in (await conn.execute(text("SELECT capability, plugin_id FROM capability_selections"))).fetchall():
        kind = "filament_inventory" if cap == "inventory.filament" else cap
        await conn.execute(text("INSERT OR IGNORE INTO extension_slots (kind, plugin_id) VALUES (:k, :p)"), {"k": kind, "p": plugin_id})
    await conn.execute(text("DROP TABLE capability_selections"))
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(installed_plugins)"))).fetchall()}
    if "kind" not in cols:
        await conn.execute(text("ALTER TABLE installed_plugins ADD COLUMN kind VARCHAR(64) NOT NULL DEFAULT ''"))
