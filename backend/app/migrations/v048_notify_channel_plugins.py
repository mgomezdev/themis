"""Notification channels become plugins (BIZ-252): each configured channel of the legacy `notification_config` row is copied into the
`plugin_configs` row of its plugin (`notify_ntfy`, `notify_discord`, `notify_email`) — enabled flag, settings, event filter and secrets
(the Discord webhook URL, the SMTP password) preserved. An untouched channel gets no row (the plugin is default-enabled with an empty event
list, which sends nothing). The old table stays, unused. Existing plugin rows are never overwritten."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text

version = 48
name = "notify_channel_plugins"
data_only = True        # changes rows, not schema (tests/test_migrations.py skips such a migration when it looks for a schema rollback)

PLUGINS = ("notify_ntfy", "notify_discord", "notify_email")


def _events(raw) -> list:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return list(raw) if isinstance(raw, list) else []


async def up(conn) -> None:
    exists = (await conn.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_config'"))).first()
    if not exists:
        return
    row = (await conn.execute(text("SELECT * FROM notification_config WHERE id = 1"))).mappings().fetchone()
    if row is None:
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    def clean(d: dict) -> dict:
        return {k: v for k, v in d.items() if v is not None}

    def in_range(value, lo: int, hi: int):
        """The old API never bounds-checked these; a value the new settings model would reject is dropped rather than copied (it
        would put the whole channel into a build error)."""
        return value if isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi else None

    channels = {
        "notify_ntfy": (bool(row["ntfy_enabled"]),
                        clean({"server_url": row["ntfy_server_url"], "topic": row["ntfy_topic"], "priority": in_range(row["ntfy_priority"], 1, 5),
                               "events": _events(row["ntfy_events"])}), {}),
        "notify_discord": (bool(row["discord_enabled"]), {"events": _events(row["discord_events"])},
                           clean({"webhook_url": row["discord_webhook_url"]})),
        "notify_email": (bool(row["email_enabled"]),
                         clean({"host": row["email_host"], "port": in_range(row["email_port"], 1, 65535), "username": row["email_username"],
                                "from_addr": row["email_from_addr"], "to_addrs": _events(row["email_to_addrs"]),
                                "events": _events(row["email_events"])}),
                         clean({"password": row["email_password"]})),
    }
    for plugin_id, (enabled, settings, secrets) in channels.items():
        touched = enabled or secrets or any(v for k, v in settings.items() if k != "events") or settings.get("events")
        if not touched:
            continue
        await conn.execute(
            text("INSERT OR IGNORE INTO plugin_configs (plugin_id, enabled, settings, secrets, state, updated_at) "
                 "VALUES (:p, :e, :s, :x, '{}', :now)"),
            {"p": plugin_id, "e": 1 if enabled else 0, "s": json.dumps(settings), "x": json.dumps(secrets), "now": now})


async def down(conn) -> None:
    for plugin_id in PLUGINS:
        await conn.execute(text("DELETE FROM plugin_configs WHERE plugin_id = :p"), {"p": plugin_id})
