from app.plugins.capabilities.filament_inventory import InvMaterial, InventoryProviderError
from tests.catalog_helpers import cached_raw, prime_catalog
from tests.fake_providers import FakeInventoryProvider
from app.services import catalog_service
import json
from unittest.mock import AsyncMock, patch, MagicMock
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


async def test_spoolman_test_falls_back_to_saved_api_key_when_omitted(client: AsyncClient, spoolman_upstream):
    """/spoolman/test must still be able to use the saved key even though GET
    no longer exposes it - the caller omits api_key rather than resending it."""
    await client.put(
        "/api/v1/settings/spoolman",
        json={"url": "http://spoolman.test", "api_key": "sm-key-value"},
    )

    resp = await client.post("/api/v1/settings/spoolman/test", json={})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert [(str(r.url), r.headers.get("X-API-Key")) for r in spoolman_upstream.requests] == [
        ("http://spoolman.test/api/v1/info", "sm-key-value")]


async def test_spoolman_test_falls_back_to_saved_api_key_when_url_is_also_sent(client: AsyncClient, spoolman_upstream):
    """The actual frontend path: it always sends url (required to even enable the
    Test button) but omits api_key when the user hasn't retyped it. The fallback
    must not be gated on the url also being missing."""
    await client.put(
        "/api/v1/settings/spoolman",
        json={"url": "http://spoolman.test", "api_key": "sm-key-value"},
    )

    resp = await client.post("/api/v1/settings/spoolman/test", json={"url": "http://spoolman.test"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert [(str(r.url), r.headers.get("X-API-Key")) for r in spoolman_upstream.requests] == [
        ("http://spoolman.test/api/v1/info", "sm-key-value")]


def _bound(ref, name, bindings):
    return InvMaterial(ref=str(ref), name=name, profile_links=bindings)


def _patch_inventory(provider):
    return patch("app.plugins.host.plugin_host.build_candidate", return_value=provider)


async def test_spoolman_test_connection_all_names_valid_returns_ok(client):
    """All profile names bound in Spoolman present in the catalog → normal success response."""
    original_pending = catalog_service._pending_sync
    prime_catalog({"machine": [], "process": [], "filament": [{"name": "PLA", "uuid": "f1"}]})
    provider = FakeInventoryProvider(materials=[_bound(1, "PLA Red", {"Bambu X1C": ["PLA"]})])

    try:
        with _patch_inventory(provider):
            resp = await client.post("/api/v1/settings/spoolman/test", json={"url": "http://spoolman.test"})

        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "ok" and body.get("ok") is True
        assert provider.calls == ["test_connection", "list_materials"]
        assert catalog_service._pending_sync is original_pending       # nothing parked
    finally:
        catalog_service._pending_sync = original_pending


async def test_spoolman_test_connection_stale_name_returns_pending_remaps(client):
    """Three filaments share one stale profile name → single grouped entry with three affected_filament_ids."""
    # Catalog has "PLA New" but NOT "PLA Old" — so "PLA Old" is stale
    original_pending = catalog_service._pending_sync
    prime_catalog({"machine": [], "process": [], "filament": [{"name": "PLA New", "uuid": "f-new"}]})
    catalog_service._pending_sync = None
    stale = {"Bambu X1C 0.4 nozzle": ["PLA Old"]}
    provider = FakeInventoryProvider(materials=[
        _bound(9, "Red PLA", stale), _bound(14, "Blue PLA", stale), _bound(22, "White PLA", stale),
        _bound(30, "Fine PLA", {"Bambu X1C 0.4 nozzle": ["PLA New"]}),     # valid binding: not flagged
    ])

    try:
        with _patch_inventory(provider):
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
        assert sorted(entry["affected_filament_ids"]) == [9, 14, 22]          # ints, as the frontend always got
        assert entry["affected_filament_names"] == ["Red PLA", "Blue PLA", "White PLA"]
        assert body["pending"]["printers"] == []
        assert body["pending"]["jobs"] == []
        assert catalog_service._pending_sync is not None
        assert catalog_service._pending_sync["raw"] is None  # Spoolman-only
    finally:
        catalog_service._pending_sync = original_pending


async def test_spoolman_test_connection_cold_catalog_returns_ok(client):
    """Cold cache → skip the name check, return normal success."""
    prime_catalog(None)
    provider = FakeInventoryProvider()

    with _patch_inventory(provider):
        resp = await client.post("/api/v1/settings/spoolman/test", json={"url": "http://spoolman.test"})

    assert resp.status_code == 200
    assert provider.calls == ["test_connection"]        # no filaments fetched


async def test_spoolman_test_connection_skips_the_name_check_without_profile_bindings(client):
    prime_catalog({"machine": [], "process": [], "filament": []})
    provider = FakeInventoryProvider(materials=[_bound(1, "x", {"P": ["gone"]})])
    provider.capabilities = frozenset()

    with _patch_inventory(provider):
        resp = await client.post("/api/v1/settings/spoolman/test", json={"url": "http://spoolman.test"})

    assert resp.json() == {"ok": True, "status": "ok", "version": "fake"}
    assert provider.calls == ["test_connection"]


async def test_spoolman_test_connection_reports_a_failed_connection_and_survives_a_failed_filament_fetch(client):
    prime_catalog({"machine": [], "process": [], "filament": []})
    down = FakeInventoryProvider()
    down.fail_with = InventoryProviderError("Connection refused", code="ConnectError")
    with _patch_inventory(down):
        resp = await client.post("/api/v1/settings/spoolman/test", json={"url": "http://spoolman.test"})
    assert resp.json() == {"ok": False, "message": "Connection refused"}

    flaky = FakeInventoryProvider()

    async def list_materials():
        raise InventoryProviderError("boom")

    flaky.list_materials = list_materials
    with _patch_inventory(flaky):
        resp = await client.post("/api/v1/settings/spoolman/test", json={"url": "http://spoolman.test"})
    assert resp.json()["ok"] is True and resp.json()["status"] == "ok"      # best-effort: connection still ok


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


async def test_slice_cache_stale_policy_defaults_on_and_round_trips(client: AsyncClient, session_factory):
    """BIZ-191: 'always use latest slicer settings' is on by default; turning it off persists and leaves the other
    queue settings alone."""
    from app.models import QueueConfig
    assert (await client.get("/api/v1/settings/queue")).json()["slice_cache_use_latest_settings"] is True

    resp = await client.put("/api/v1/settings/queue", json={"slice_cache_use_latest_settings": False})

    assert resp.status_code == 200
    assert resp.json()["slice_cache_use_latest_settings"] is False
    async with session_factory() as s:
        row = await s.get(QueueConfig, 1)
        assert row.slice_cache_use_latest_settings is False
        assert row.check_interval_minutes == 5
    resp = await client.put("/api/v1/settings/queue", json={"operator_name": "x"})
    assert resp.json()["slice_cache_use_latest_settings"] is False   # omitted ⇒ unchanged
