"""Inventory core (BIZ-215): `inventory_config`, and the move of the Spoolman integration onto the plugin host.

* `spoolman_config` -> `plugin_configs('spoolman')` (url / sync interval as settings, api_key as a secret, sync health as
  state, `enabled` copied) and `extension_slots('filament_inventory') = 'spoolman'` when a URL is set.
* Low-stock default / overrides / alerted set -> `inventory_config`, re-keyed `"spoolman:<id>"`.
* `inventory:read` / `inventory:write` are granted to every key that already holds the matching Spoolman or settings scope
  (the admin session and browser device keys carry scope *snapshots*, so they are covered too; precedent: v021).

`spoolman_config` is kept (read by nothing from here on; dropped in a later cleanup release). Idempotent: existing
plugin/slot/inventory rows are never overwritten."""
from __future__ import annotations

import json

from sqlalchemy import text

version = 35
name = "inventory_core"

_READ_FROM = {"spoolman:read", "settings:read", "spoolman:write", "settings:write"}
_WRITE_FROM = {"spoolman:write", "settings:write"}


def _loads(raw, default):
    if raw is None:
        return default
    try:
        return json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except Exception:
        return default


async def _has_table(conn, table: str) -> bool:
    return bool((await conn.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"), {"t": table})).first())


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS inventory_config (
            id INTEGER PRIMARY KEY,
            deduct_on_complete BOOLEAN NOT NULL DEFAULT 1,
            low_stock_default_g FLOAT,
            low_stock_overrides JSON,
            low_stock_alerted JSON
        )
    """))
    if await _has_table(conn, "spoolman_config"):
        row = (await conn.execute(text("SELECT * FROM spoolman_config WHERE id = 1"))).mappings().first()
        if row is not None:
            settings = {"sync_interval_minutes": row["sync_interval_minutes"] or 15}
            if row["url"]:
                settings["url"] = row["url"]
            secrets = {"api_key": row["api_key"]} if row["api_key"] else {}
            state = {k: v for k, v in {
                "last_sync_at": row["last_sync_at"], "last_attempt_at": row["last_attempt_at"],
                "sync_error": row["last_sync_error"], "sync_error_code": row["last_sync_error_code"]}.items() if v is not None}
            await conn.execute(text(
                "INSERT OR IGNORE INTO plugin_configs (plugin_id, enabled, settings, secrets, state) "
                "VALUES ('spoolman', :e, :s, :k, :t)"),
                {"e": 1 if row["enabled"] else 0, "s": json.dumps(settings), "k": json.dumps(secrets), "t": json.dumps(state)})
            if row["url"]:
                await conn.execute(text("INSERT OR IGNORE INTO extension_slots (kind, plugin_id) VALUES ('filament_inventory', 'spoolman')"))
            overrides = {f"spoolman:{k}": float(v) for k, v in _loads(row["low_stock_overrides"], {}).items()}
            alerted = [f"spoolman:{i}" for i in _loads(row["low_stock_alerted"], [])]
            await conn.execute(text(
                "INSERT OR IGNORE INTO inventory_config (id, deduct_on_complete, low_stock_default_g, low_stock_overrides, low_stock_alerted) "
                "VALUES (1, 1, :d, :o, :a)"),
                {"d": row["low_stock_default_g"], "o": json.dumps(overrides), "a": json.dumps(alerted)})
    for key_id, scopes_raw in (await conn.execute(text("SELECT id, scopes FROM api_keys"))).fetchall():
        scopes = _loads(scopes_raw, [])
        if not isinstance(scopes, list):
            continue
        want = set(scopes)
        if want & _READ_FROM:
            want.add("inventory:read")
        if want & _WRITE_FROM:
            want.add("inventory:write")
        if want != set(scopes):
            await conn.execute(text("UPDATE api_keys SET scopes = :s WHERE id = :i"),
                               {"s": json.dumps(sorted(want)), "i": key_id})


async def down(conn) -> None:
    """Remove what `up` created. `spoolman_config` was never modified, so the old integration works again as-is."""
    await conn.execute(text("DELETE FROM extension_slots WHERE kind = 'filament_inventory' AND plugin_id = 'spoolman'"))
    await conn.execute(text("DELETE FROM plugin_configs WHERE plugin_id = 'spoolman'"))
    await conn.execute(text("DROP TABLE IF EXISTS inventory_config"))
    for key_id, scopes_raw in (await conn.execute(text("SELECT id, scopes FROM api_keys"))).fetchall():
        scopes = _loads(scopes_raw, [])
        if isinstance(scopes, list) and {"inventory:read", "inventory:write"} & set(scopes):
            await conn.execute(text("UPDATE api_keys SET scopes = :s WHERE id = :i"),
                               {"s": json.dumps([x for x in scopes if not x.startswith("inventory:")]), "i": key_id})
