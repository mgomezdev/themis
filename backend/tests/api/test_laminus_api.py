"""Tests for /api/v1/laminus/catalog/* routes (Features 2 and 3)."""
import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from httpx import AsyncClient

import app.api.routes.laminus as lmod

SAMPLE_CATALOG = {
    "machine": [{"name": "Bambu X1C 0.4 nozzle", "uuid": "m1"}],
    "process": [{"name": "0.20mm Standard", "uuid": "p1"}],
    "filament": [{"name": "Generic PLA", "uuid": "f1"}],
}


# ---- catalog/status tests (Feature 3) ----

async def test_catalog_status_cold_cache_unconfigured(client: AsyncClient):
    """Status when no sidecar configured."""
    lmod._catalog_dict = None
    lmod._catalog_bytes = None
    lmod._health_memo = None
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value=None):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is False
    assert body["status"] == "unconfigured"
    assert body["catalog_counts"] is None


async def test_catalog_status_includes_catalog_counts(client: AsyncClient):
    """catalog/status returns catalog_counts when cache is warm."""
    lmod._catalog_dict = SAMPLE_CATALOG
    lmod._catalog_bytes = json.dumps(SAMPLE_CATALOG).encode()
    lmod._health_memo = None
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value=None):
        resp = await client.get("/api/v1/laminus/catalog/status")
    body = resp.json()
    assert body["catalog_counts"] == {"machine": 1, "process": 1, "filament": 1}


async def test_catalog_status_online(client: AsyncClient):
    """status='online' when health returns catalog_loaded=true."""
    lmod._catalog_dict = SAMPLE_CATALOG
    lmod._catalog_bytes = json.dumps(SAMPLE_CATALOG).encode()
    lmod._health_memo = None
    health_resp = MagicMock()
    health_resp.status_code = 200
    health_resp.json.return_value = {
        "catalog_loaded": True, "catalog_building": False, "catalog_profile_count": 50
    }
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch("httpx.get", return_value=health_resp):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.json()["status"] == "online"


async def test_catalog_status_building_via_flag(client: AsyncClient):
    """status='building' when catalog_building=true."""
    lmod._catalog_dict = None
    lmod._catalog_bytes = None
    lmod._health_memo = None
    health_resp = MagicMock()
    health_resp.status_code = 200
    health_resp.json.return_value = {
        "catalog_loaded": False, "catalog_building": True, "catalog_profile_count": None
    }
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch("httpx.get", return_value=health_resp):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.json()["status"] == "building"


async def test_catalog_status_building_via_503(client: AsyncClient):
    """status='building' when health returns 503 (catalog rebuild in progress)."""
    lmod._catalog_dict = None
    lmod._catalog_bytes = None
    lmod._health_memo = None
    health_resp = MagicMock()
    health_resp.status_code = 503
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch("httpx.get", return_value=health_resp):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.json()["status"] == "building"


async def test_catalog_status_offline_when_health_fails(client: AsyncClient):
    """status='offline' when health check raises."""
    lmod._catalog_dict = None
    lmod._catalog_bytes = None
    lmod._health_memo = None
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch("httpx.get", side_effect=Exception("connection refused")):
        resp = await client.get("/api/v1/laminus/catalog/status")
    assert resp.json()["status"] == "offline"


# ---- refresh drift-gate tests (Feature 2) ----

async def test_refresh_cold_cache_commits_immediately(client: AsyncClient):
    """Cold cache (first sync) commits without drift check."""
    lmod._catalog_dict = None
    lmod._catalog_bytes = None
    lmod._pending_sync = None

    with patch("app.api.routes.laminus._fetch_catalog", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = (json.dumps(SAMPLE_CATALOG).encode(), SAMPLE_CATALOG)
        resp = await client.post("/api/v1/laminus/catalog/refresh")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert lmod._catalog_dict == SAMPLE_CATALOG
    assert lmod._pending_sync is None


async def test_refresh_no_drift_commits_and_returns_ok(client: AsyncClient):
    """Identical catalog (no drift) commits immediately."""
    lmod._catalog_dict = SAMPLE_CATALOG
    lmod._catalog_bytes = json.dumps(SAMPLE_CATALOG).encode()
    lmod._pending_sync = None

    with patch("app.api.routes.laminus._fetch_catalog", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.catalog_utils.compute_drift", new_callable=AsyncMock) as mock_drift:
        mock_fetch.return_value = (json.dumps(SAMPLE_CATALOG).encode(), SAMPLE_CATALOG)
        mock_drift.return_value = None  # no drift

        resp = await client.post("/api/v1/laminus/catalog/refresh")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert lmod._pending_sync is None


async def test_refresh_drift_returns_pending_remaps_and_parks_catalog(client: AsyncClient):
    """Drift detected → pending_remaps returned, old catalog stays, _pending_sync set."""
    old_bytes = json.dumps(SAMPLE_CATALOG).encode()
    lmod._catalog_dict = SAMPLE_CATALOG
    lmod._catalog_bytes = old_bytes
    lmod._pending_sync = None

    new_catalog = {"machine": [], "process": [], "filament": []}
    new_bytes = json.dumps(new_catalog).encode()

    drift_payload = {
        "pending": {
            "printers": [{"field": "current_orca_printer_profile", "stale_value": "Bambu X1C 0.4 nozzle",
                          "options_kind": "machine", "required": True,
                          "affected_printer_ids": [1], "affected_printer_names": ["X1C"],
                          "affected_slots": [None]}],
            "jobs": [], "spoolman_filaments": [],
        },
        "options": {"machine": [], "process": [], "filament": [], "filament_uuids": []},
        "spoolman_error": None,
    }

    with patch("app.api.routes.laminus._fetch_catalog", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.catalog_utils.compute_drift", new_callable=AsyncMock) as mock_drift:
        mock_fetch.return_value = (new_bytes, new_catalog)
        mock_drift.return_value = drift_payload

        resp = await client.post("/api/v1/laminus/catalog/refresh")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending_remaps"
    assert "sync_id" in body
    assert len(body["pending"]["printers"]) == 1

    # Old catalog still active
    assert lmod._catalog_bytes == old_bytes
    # Pending sync was parked
    assert lmod._pending_sync is not None
    assert lmod._pending_sync["sync_id"] == body["sync_id"]
    assert lmod._pending_sync["raw"] == new_bytes
