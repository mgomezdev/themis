"""v048 (legacy notification_config -> channel plugin settings, BIZ-252) and the bundled channels end to end through the real host."""
import json

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v048_notify_channel_plugins as mig
from app.plugins.host import plugin_host
from app.plugins.notify_ntfy import channel as ntfy_mod
from app.services import notify as notify_mod
from app.services.notify import notify

LEGACY_DDL = """CREATE TABLE notification_config (
    id INTEGER PRIMARY KEY, ntfy_enabled BOOLEAN, ntfy_server_url TEXT, ntfy_topic TEXT, ntfy_priority INTEGER, ntfy_events JSON,
    discord_enabled BOOLEAN, discord_webhook_url TEXT, discord_events JSON,
    email_enabled BOOLEAN, email_host TEXT, email_port INTEGER, email_username TEXT, email_password TEXT, email_from_addr TEXT,
    email_to_addrs JSON, email_events JSON)"""
PLUGIN_DDL = """CREATE TABLE plugin_configs (plugin_id VARCHAR(64) PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT 0,
    settings JSON NOT NULL DEFAULT '{}', secrets JSON NOT NULL DEFAULT '{}', state JSON NOT NULL DEFAULT '{}', updated_at VARCHAR(32))"""


@pytest.fixture
async def conn():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c:
        await c.execute(text(PLUGIN_DDL))
        yield c
    await engine.dispose()


async def _rows(c):
    out = {}
    for r in (await c.execute(text("SELECT plugin_id, enabled, settings, secrets FROM plugin_configs"))).fetchall():
        out[r[0]] = (bool(r[1]), json.loads(r[2]), json.loads(r[3]))
    return out


async def _legacy(c, **cols):
    await c.execute(text(LEGACY_DDL))
    base = dict(id=1, ntfy_enabled=0, ntfy_server_url=None, ntfy_topic=None, ntfy_priority=None, ntfy_events="[]",
                discord_enabled=0, discord_webhook_url=None, discord_events="[]",
                email_enabled=0, email_host=None, email_port=None, email_username=None, email_password=None, email_from_addr=None,
                email_to_addrs="[]", email_events="[]")
    base.update(cols)
    await c.execute(text(f"INSERT INTO notification_config ({', '.join(base)}) VALUES ({', '.join(':' + k for k in base)})"), base)


async def test_every_configured_channel_keeps_its_enabled_flag_settings_events_and_secrets(conn):
    await _legacy(conn, ntfy_enabled=1, ntfy_server_url="https://ntfy.sh", ntfy_topic="themis", ntfy_priority=4,
                  ntfy_events=json.dumps(["job.complete", "job.failed"]),
                  discord_enabled=1, discord_webhook_url="https://discord.test/api/webhooks/1/TOKEN", discord_events=json.dumps(["job.blocked"]),
                  email_enabled=0, email_host="smtp.test", email_port=587, email_username="u", email_password="pw-1",
                  email_from_addr="t@example.test", email_to_addrs=json.dumps(["a@example.test"]), email_events=json.dumps(["spool.low"]))

    await mig.up(conn)

    assert await _rows(conn) == {
        "notify_ntfy": (True, {"server_url": "https://ntfy.sh", "topic": "themis", "priority": 4, "events": ["job.complete", "job.failed"]}, {}),
        "notify_discord": (True, {"events": ["job.blocked"]}, {"webhook_url": "https://discord.test/api/webhooks/1/TOKEN"}),
        "notify_email": (False, {"host": "smtp.test", "port": 587, "username": "u", "from_addr": "t@example.test",
                                "to_addrs": ["a@example.test"], "events": ["spool.low"]}, {"password": "pw-1"}),
    }


async def test_an_untouched_channel_gets_no_row_and_a_disabled_but_configured_one_stays_disabled(conn):
    await _legacy(conn, discord_enabled=0, discord_webhook_url="https://discord.test/h", discord_events=json.dumps(["job.failed"]))

    await mig.up(conn)

    rows = await _rows(conn)
    assert set(rows) == {"notify_discord"} and rows["notify_discord"][0] is False


async def test_it_never_overwrites_an_existing_plugin_row_and_is_idempotent(conn):
    await _legacy(conn, ntfy_enabled=1, ntfy_server_url="https://old", ntfy_topic="old", ntfy_events=json.dumps(["job.complete"]))
    await conn.execute(text("INSERT INTO plugin_configs (plugin_id, enabled, settings, secrets) VALUES ('notify_ntfy', 1, '{\"topic\": \"new\"}', '{}')"))

    await mig.up(conn)
    await mig.up(conn)

    assert (await _rows(conn))["notify_ntfy"][1] == {"topic": "new"}


@pytest.mark.parametrize("setup", [None, "empty-table"])
async def test_without_a_legacy_table_or_row_it_is_a_no_op(conn, setup):
    if setup:
        await conn.execute(text(LEGACY_DDL))
    await mig.up(conn)
    assert await _rows(conn) == {}


async def test_down_removes_the_channel_rows_only(conn):
    await _legacy(conn, ntfy_enabled=1, ntfy_server_url="https://n", ntfy_topic="t")
    await conn.execute(text("INSERT INTO plugin_configs (plugin_id, enabled) VALUES ('spoolman', 1)"))
    await mig.up(conn)
    await mig.down(conn)
    assert set(await _rows(conn)) == {"spoolman"}


# --- the bundled channels through the real host -------------------------------------------------------------------

_REAL = httpx.AsyncClient


async def test_bundled_channels_are_enabled_by_default_but_send_nothing_until_events_are_chosen(session_factory, monkeypatch):
    sent = []
    monkeypatch.setattr(ntfy_mod.httpx, "AsyncClient",
                        lambda **kw: _REAL(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)), **kw))
    notify_mod._seen.clear()
    await plugin_host.start()

    assert set(plugin_host.active_providers("notify.channel")) >= {"notify_ntfy", "notify_discord", "notify_email"}
    out = await notify("job.complete", 1, "T", "M")
    assert set(out.values()) == {"skipped"} and sent == []


async def test_configuring_a_bundled_channel_from_its_settings_makes_it_send(client, session_factory, monkeypatch):
    sent = []
    monkeypatch.setattr(ntfy_mod.httpx, "AsyncClient",
                        lambda **kw: _REAL(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)), **kw))
    notify_mod._seen.clear()
    await plugin_host.start()

    saved = await client.put("/api/v1/plugins/notify_ntfy", json={
        "settings": {"server_url": "https://ntfy.example.test", "topic": "themis", "events": ["job.complete"]}})
    out = await notify("job.complete", 3, "Done", "benchy finished")
    await notify("job.failed", 3, "Failed", "nope")

    assert saved.status_code == 200 and out["notify_ntfy"] == "ok"
    (request,) = sent
    assert json.loads(request.content)["message"] == "benchy finished"


async def test_a_channel_plugin_lists_its_settings_page_and_a_secret_is_never_echoed(client, session_factory):
    await plugin_host.start()
    await client.put("/api/v1/plugins/notify_discord", json={"settings": {"events": ["job.failed"]}, "secrets": {"webhook_url": "https://d.test/h/TOKEN"}})

    body = (await client.get("/api/v1/plugins/notify_discord")).json()

    assert body["ui"]["mode"] == "page" and body["ui"]["nav_label"] == "Discord" and body["ui"]["nav_placement"] == "settings"
    assert body["secrets"] == {"webhook_url": True} and "TOKEN" not in json.dumps(body)
    listing = {p["id"]: p for p in (await client.get("/api/v1/plugins")).json()["plugins"]}
    assert {"notify_ntfy", "notify_discord", "notify_email"} <= set(listing)
    assert all(listing[i]["enabled"] for i in ("notify_ntfy", "notify_discord", "notify_email"))


async def test_values_the_old_api_accepted_but_the_new_settings_reject_are_dropped_not_copied(conn):
    await _legacy(conn, ntfy_enabled=1, ntfy_server_url="https://n", ntfy_topic="t", ntfy_priority=9, ntfy_events=json.dumps(["job.complete"]),
                  email_enabled=1, email_host="smtp.test", email_port=99999, email_from_addr="a@x.test",
                  email_to_addrs=json.dumps(["b@x.test"]), email_events=json.dumps(["job.complete"]))

    await mig.up(conn)

    rows = await _rows(conn)
    assert "priority" not in rows["notify_ntfy"][1] and "port" not in rows["notify_email"][1]
    from app.plugins.notify_email.settings import EmailSettings
    from app.plugins.notify_ntfy.settings import NtfySettings
    NtfySettings(**rows["notify_ntfy"][1])                      # the migrated settings actually build
    EmailSettings(**rows["notify_email"][1])
