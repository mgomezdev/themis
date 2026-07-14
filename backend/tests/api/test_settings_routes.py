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
    lmod._catalog_dict = catalog

    filaments_response = [
        {"id": 1, "name": "PLA Red", "extra": {"orca_profiles": json.dumps(json.dumps({"f1": "PLA"}))}}
    ]

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


async def test_spoolman_test_connection_stale_uuid_returns_pending_remaps(client):
    """Three filaments share one stale UUID → single grouped entry with three affected_filament_ids."""
    catalog = {"machine": [], "process": [], "filament": [{"name": "PLA New", "uuid": "f-new"}]}
    lmod._catalog_dict = catalog
    lmod._pending_sync = None

    filaments_response = [
        {"id": 9, "name": "Red PLA", "extra": {"orca_profiles": json.dumps(json.dumps({"stale-uuid": "PLA Old"}))}},
        {"id": 14, "name": "Blue PLA", "extra": {"orca_profiles": json.dumps(json.dumps({"stale-uuid": "PLA Old"}))}},
        {"id": 22, "name": "White PLA", "extra": {"orca_profiles": json.dumps(json.dumps({"stale-uuid": "PLA Old"}))}},
    ]

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
    assert set(spool_entries[0]["affected_filament_ids"]) == {9, 14, 22}
    assert body["pending"]["printers"] == []
    assert body["pending"]["jobs"] == []
    assert lmod._pending_sync is not None
    assert lmod._pending_sync["raw"] is None  # Spoolman-only


async def test_spoolman_test_connection_cold_catalog_returns_ok(client):
    """Cold cache → skip UUID check, return normal success."""
    lmod._catalog_dict = None

    with patch("app.services.spoolman_service.test_connection", new_callable=AsyncMock) as mock_test, \
         patch("app.services.spoolman_service.fetch_filaments", new_callable=AsyncMock) as mock_fetch:
        mock_test.return_value = {"version": "0.19.0"}

        resp = await client.post("/api/v1/settings/spoolman/test", json={"url": "http://spoolman.test"})

    mock_fetch.assert_not_called()
    assert resp.status_code == 200
