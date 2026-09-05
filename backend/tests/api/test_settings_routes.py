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


async def test_webhook_config_get_never_returns_raw_secret(client: AsyncClient):
    await client.put("/api/v1/settings/webhook", json={"secret": "s3cr3t-value"})

    resp = await client.get("/api/v1/settings/webhook")

    assert resp.status_code == 200
    body = resp.json()
    assert "secret" not in body
    assert body["has_secret"] is True
    assert "s3cr3t-value" not in resp.text


async def test_webhook_config_put_response_also_never_returns_raw_secret(client: AsyncClient):
    resp = await client.put("/api/v1/settings/webhook", json={"secret": "s3cr3t-value"})

    assert resp.status_code == 200
    assert "s3cr3t-value" not in resp.text
    assert resp.json()["has_secret"] is True


async def test_webhook_config_has_secret_false_when_unset(client: AsyncClient):
    resp = await client.get("/api/v1/settings/webhook")

    assert resp.json()["has_secret"] is False


async def test_webhook_config_put_omitting_secret_leaves_it_unchanged(client: AsyncClient):
    await client.put("/api/v1/settings/webhook", json={"secret": "s3cr3t-value"})

    resp = await client.put("/api/v1/settings/webhook", json={"url": "https://example.com/hook"})

    assert resp.status_code == 200
    assert resp.json()["has_secret"] is True
    assert resp.json()["url"] == "https://example.com/hook"


async def test_webhook_config_put_empty_secret_clears_it(client: AsyncClient):
    await client.put("/api/v1/settings/webhook", json={"secret": "s3cr3t-value"})

    resp = await client.put("/api/v1/settings/webhook", json={"secret": ""})

    assert resp.status_code == 200
    assert resp.json()["has_secret"] is False


async def test_spoolman_config_get_never_returns_raw_api_key(client: AsyncClient):
    await client.put("/api/v1/settings/spoolman", json={"api_key": "sm-key-value"})

    resp = await client.get("/api/v1/settings/spoolman")

    assert resp.status_code == 200
    body = resp.json()
    assert "api_key" not in body
    assert body["has_api_key"] is True
    assert "sm-key-value" not in resp.text


async def test_spoolman_config_put_omitting_api_key_leaves_it_unchanged(client: AsyncClient):
    await client.put("/api/v1/settings/spoolman", json={"api_key": "sm-key-value"})

    resp = await client.put("/api/v1/settings/spoolman", json={"url": "http://spoolman.test"})

    assert resp.status_code == 200
    assert resp.json()["has_api_key"] is True
    assert resp.json()["url"] == "http://spoolman.test"


async def test_spoolman_config_put_empty_api_key_clears_it(client: AsyncClient):
    await client.put("/api/v1/settings/spoolman", json={"api_key": "sm-key-value"})

    resp = await client.put("/api/v1/settings/spoolman", json={"api_key": ""})

    assert resp.status_code == 200
    assert resp.json()["has_api_key"] is False


async def test_spoolman_test_falls_back_to_saved_api_key_when_omitted(client: AsyncClient):
    """/spoolman/test must still be able to use the saved key even though GET
    no longer exposes it - the caller omits api_key rather than resending it."""
    await client.put(
        "/api/v1/settings/spoolman",
        json={"url": "http://spoolman.test", "api_key": "sm-key-value"},
    )

    with patch(
        "app.api.routes.settings.spoolman_service.test_connection",
        new_callable=AsyncMock,
        return_value={"version": "1.0"},
    ) as mock_test:
        resp = await client.post("/api/v1/settings/spoolman/test", json={})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_test.assert_called_once_with("http://spoolman.test", "sm-key-value")


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

    assert body["ntfy"] == {"enabled": False, "server_url": None, "topic": None, "priority": None, "events": []}
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
        "priority": None, "events": ["job.complete"],
    }
    assert body["discord"] == {"enabled": False, "webhook_url": None, "events": []}
    assert body["email"]["enabled"] is False


async def test_put_notifications_ntfy_priority_round_trips(client: AsyncClient):
    """The ntfy 'priority' field must actually persist — it was previously
    accepted by the request body but silently dropped (no matching DB column),
    so real job-event notifications never honored a configured priority."""
    resp = await client.put("/api/v1/settings/notifications", json={
        "ntfy": {
            "enabled": True,
            "server_url": "https://ntfy.sh",
            "topic": "themis-test",
            "priority": 4,
            "events": ["job.complete"],
        }
    })
    assert resp.status_code == 200
    assert resp.json()["ntfy"]["priority"] == 4

    resp = await client.get("/api/v1/settings/notifications")
    assert resp.status_code == 200
    assert resp.json()["ntfy"]["priority"] == 4


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
        "priority": None, "events": ["job.complete"],
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
