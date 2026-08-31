import json
from unittest.mock import AsyncMock, patch, MagicMock

import app.api.routes.laminus as lmod
from httpx import AsyncClient


async def test_get_queue_config_operator_name_null_on_fresh_row(client: AsyncClient):
    resp = await client.get("/api/v1/settings/queue")

    assert resp.status_code == 200
    body = resp.json()
    assert body["operator_name"] is None
    assert body["check_interval_minutes"] == 5


async def test_put_operator_name_only_leaves_check_interval_untouched(client: AsyncClient):
    await client.put("/api/v1/settings/queue", json={"check_interval_minutes": 10})

    resp = await client.put("/api/v1/settings/queue", json={"operator_name": "Workshop Lead"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["operator_name"] == "Workshop Lead"
    assert body["check_interval_minutes"] == 10


async def test_put_check_interval_only_leaves_operator_name_untouched(client: AsyncClient):
    await client.put("/api/v1/settings/queue", json={"operator_name": "Workshop Lead"})

    resp = await client.put("/api/v1/settings/queue", json={"check_interval_minutes": 15})

    assert resp.status_code == 200
    body = resp.json()
    assert body["check_interval_minutes"] == 15
    assert body["operator_name"] == "Workshop Lead"


async def test_put_empty_operator_name_clears_it_to_null(client: AsyncClient):
    await client.put("/api/v1/settings/queue", json={"operator_name": "Workshop Lead"})

    resp = await client.put("/api/v1/settings/queue", json={"operator_name": ""})

    assert resp.status_code == 200
    assert resp.json()["operator_name"] is None


async def test_estimates_enabled_get_put(client: AsyncClient):
    """GET /settings/queue includes estimates_enabled; PUT persists it."""
    get_resp = await client.get("/api/v1/settings/queue")
    assert get_resp.status_code == 200
    assert "estimates_enabled" in get_resp.json()
    assert get_resp.json()["estimates_enabled"] is False

    put_resp = await client.put("/api/v1/settings/queue", json={"estimates_enabled": True})
    assert put_resp.status_code == 200
    assert put_resp.json()["estimates_enabled"] is True

    get_resp2 = await client.get("/api/v1/settings/queue")
    assert get_resp2.json()["estimates_enabled"] is True


async def test_spoolman_test_connection_all_uuids_valid_returns_ok(client):
    """All Spoolman filament UUIDs present in catalog → normal success response."""
    catalog = {"machine": [], "process": [], "filament": [{"name": "PLA", "uuid": "f1"}]}
    original_catalog = lmod._catalog_dict
    original_pending = lmod._pending_sync
    lmod._catalog_dict = catalog

    filaments_response = [
        {"id": 1, "name": "PLA Red", "extra": {"orca_profiles": json.dumps(json.dumps({"f1": "PLA"}))}}
    ]

    try:
        with patch("app.services.spoolman_service.test_connection", new_callable=AsyncMock) as mock_test, \
             patch("app.services.spoolman_service.fetch_filaments", new_callable=AsyncMock) as mock_fetch:
            mock_test.return_value = {"version": "0.19.0"}
            mock_fetch.return_value = filaments_response

            resp = await client.post("/api/v1/settings/spoolman/test", json={"url": "http://spoolman.test"})

        assert resp.status_code == 200
        body = resp.json()
        # Status is "ok" (in some shape) — the exact key depends on the existing handler shape
        # Accept either {"status": "ok"} or {"ok": True}
        assert body.get("status") == "ok" or body.get("ok") is True
        mock_fetch.assert_called_once()
    finally:
        lmod._catalog_dict = original_catalog
        lmod._pending_sync = original_pending


async def test_spoolman_test_connection_stale_name_returns_pending_remaps(client):
    """Three filaments share one stale profile name → single grouped entry with three affected_filament_ids."""
    # Catalog has "PLA New" but NOT "PLA Old" — so "PLA Old" is stale
    catalog = {"machine": [], "process": [], "filament": [{"name": "PLA New", "uuid": "f-new"}]}
    original_catalog = lmod._catalog_dict
    original_pending = lmod._pending_sync
    lmod._catalog_dict = catalog
    lmod._pending_sync = None

    # Three Spoolman filaments all reference "PLA Old" for the same printer preset
    filaments_response = [
        {"id": 9, "name": "Red PLA", "extra": {"orca_profiles": json.dumps(json.dumps({"Bambu X1C 0.4 nozzle": ["PLA Old"]}))}},
        {"id": 14, "name": "Blue PLA", "extra": {"orca_profiles": json.dumps(json.dumps({"Bambu X1C 0.4 nozzle": ["PLA Old"]}))}},
        {"id": 22, "name": "White PLA", "extra": {"orca_profiles": json.dumps(json.dumps({"Bambu X1C 0.4 nozzle": ["PLA Old"]}))}},
    ]

    try:
        with patch("app.services.spoolman_service.test_connection", new_callable=AsyncMock) as mock_test, \
             patch("app.services.spoolman_service.fetch_filaments", new_callable=AsyncMock) as mock_fetch:
            mock_test.return_value = {"version": "0.19.0"}
            mock_fetch.return_value = filaments_response

            resp = await client.post("/api/v1/settings/spoolman/test", json={"url": "http://spoolman.test"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending_remaps"
        assert "sync_id" in body
        spool_entries = body["pending"]["spoolman_filaments"]
        assert len(spool_entries) == 1
        entry = spool_entries[0]
        assert entry["printer_preset"] == "Bambu X1C 0.4 nozzle"
        assert entry["stale_name"] == "PLA Old"
        assert set(entry["affected_filament_ids"]) == {9, 14, 22}
        assert body["pending"]["printers"] == []
        assert body["pending"]["jobs"] == []
        assert lmod._pending_sync is not None
        assert lmod._pending_sync["raw"] is None  # Spoolman-only
    finally:
        lmod._catalog_dict = original_catalog
        lmod._pending_sync = original_pending


async def test_spoolman_test_connection_cold_catalog_returns_ok(client):
    """Cold cache → skip UUID check, return normal success."""
    original_catalog = lmod._catalog_dict
    original_pending = lmod._pending_sync
    lmod._catalog_dict = None

    try:
        with patch("app.services.spoolman_service.test_connection", new_callable=AsyncMock) as mock_test, \
             patch("app.services.spoolman_service.fetch_filaments", new_callable=AsyncMock) as mock_fetch:
            mock_test.return_value = {"version": "0.19.0"}

            resp = await client.post("/api/v1/settings/spoolman/test", json={"url": "http://spoolman.test"})

        mock_fetch.assert_not_called()
        assert resp.status_code == 200
    finally:
        lmod._catalog_dict = original_catalog
        lmod._pending_sync = original_pending


# ---------------------------------------------------------------------------
# Notification config
# ---------------------------------------------------------------------------

async def test_get_notifications_fresh_db_all_channels_present_disabled(client: AsyncClient):
    resp = await client.get("/api/v1/settings/notifications")

    assert resp.status_code == 200
    body = resp.json()

    assert body["ntfy"] == {"enabled": False, "server_url": None, "topic": None, "events": []}
    assert body["discord"] == {"enabled": False, "webhook_url": None, "events": []}
    assert body["email"] == {
        "enabled": False, "host": None, "port": None, "username": None,
        "password": None, "from_addr": None, "to_addrs": [], "events": [],
    }


async def test_put_notifications_only_ntfy_leaves_others_default(client: AsyncClient):
    resp = await client.put("/api/v1/settings/notifications", json={
        "ntfy": {
            "enabled": True,
            "server_url": "https://ntfy.sh",
            "topic": "themis-test",
            "events": ["job.complete"],
        }
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["ntfy"] == {
        "enabled": True, "server_url": "https://ntfy.sh", "topic": "themis-test",
        "events": ["job.complete"],
    }
    assert body["discord"] == {"enabled": False, "webhook_url": None, "events": []}
    assert body["email"]["enabled"] is False


async def test_put_notifications_second_put_other_channel_preserves_first(client: AsyncClient):
    await client.put("/api/v1/settings/notifications", json={
        "ntfy": {
            "enabled": True,
            "server_url": "https://ntfy.sh",
            "topic": "themis-test",
            "events": ["job.complete"],
        }
    })

    resp = await client.put("/api/v1/settings/notifications", json={
        "discord": {
            "enabled": True,
            "webhook_url": "https://discord.com/api/webhooks/xyz",
            "events": ["job.failed"],
        }
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["ntfy"] == {
        "enabled": True, "server_url": "https://ntfy.sh", "topic": "themis-test",
        "events": ["job.complete"],
    }
    assert body["discord"] == {
        "enabled": True, "webhook_url": "https://discord.com/api/webhooks/xyz",
        "events": ["job.failed"],
    }


async def test_notifications_test_ntfy_success(client: AsyncClient):
    with patch("app.api.routes.settings.send_ntfy", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = None
        resp = await client.post("/api/v1/settings/notifications/test", json={
            "channel": "ntfy",
            "config": {"server_url": "https://ntfy.sh", "topic": "themis-test", "priority": None},
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    mock_send.assert_called_once()


async def test_notifications_test_ntfy_failure(client: AsyncClient):
    with patch("app.api.routes.settings.send_ntfy", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = "ntfy server responded 500"
        resp = await client.post("/api/v1/settings/notifications/test", json={
            "channel": "ntfy",
            "config": {"server_url": "https://ntfy.sh", "topic": "themis-test"},
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["message"] == "ntfy server responded 500"


async def test_notifications_test_ntfy_missing_field(client: AsyncClient):
    resp = await client.post("/api/v1/settings/notifications/test", json={
        "channel": "ntfy",
        "config": {},
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "message" in body


async def test_notifications_test_discord_success(client: AsyncClient):
    with patch("app.api.routes.settings.send_discord", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = None
        resp = await client.post("/api/v1/settings/notifications/test", json={
            "channel": "discord",
            "config": {"webhook_url": "https://discord.com/api/webhooks/xyz"},
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    mock_send.assert_called_once()


async def test_notifications_test_discord_failure(client: AsyncClient):
    with patch("app.api.routes.settings.send_discord", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = "Discord webhook responded 404"
        resp = await client.post("/api/v1/settings/notifications/test", json={
            "channel": "discord",
            "config": {"webhook_url": "https://discord.com/api/webhooks/xyz"},
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["message"] == "Discord webhook responded 404"


async def test_notifications_test_discord_missing_field(client: AsyncClient):
    resp = await client.post("/api/v1/settings/notifications/test", json={
        "channel": "discord",
        "config": {},
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "message" in body


async def test_notifications_test_email_success(client: AsyncClient):
    with patch("app.api.routes.settings.send_email", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = None
        resp = await client.post("/api/v1/settings/notifications/test", json={
            "channel": "email",
            "config": {
                "host": "smtp.example.com", "port": 587, "username": None, "password": None,
                "from_addr": "themis@example.com", "to_addrs": ["me@example.com"],
            },
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    mock_send.assert_called_once()


async def test_notifications_test_email_failure(client: AsyncClient):
    with patch("app.api.routes.settings.send_email", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = "Connection refused"
        resp = await client.post("/api/v1/settings/notifications/test", json={
            "channel": "email",
            "config": {
                "host": "smtp.example.com", "port": 587, "username": None, "password": None,
                "from_addr": "themis@example.com", "to_addrs": ["me@example.com"],
            },
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["message"] == "Connection refused"


async def test_notifications_test_email_missing_field(client: AsyncClient):
    resp = await client.post("/api/v1/settings/notifications/test", json={
        "channel": "email",
        "config": {"host": "smtp.example.com"},
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "message" in body
