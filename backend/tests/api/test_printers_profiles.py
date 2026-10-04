from tests.catalog_helpers import prime_catalog
from app.services import catalog_service
import pytest
from unittest.mock import AsyncMock, patch

_FAKE_CATALOG = {
    "machine": [
        {"name": "Bambu Lab P1S 0.4 nozzle", "uuid": "m-uuid-1",
         "manufacturer": "Bambu Lab", "model": "P1S", "nozzle": "0.4"},
    ],
    "process": [
        {"name": "0.20mm Standard", "uuid": "p-uuid-1",
         "compatible_printers": ["Bambu Lab P1S 0.4 nozzle"]},
    ],
    "filament": [
        {"name": "Bambu PLA Basic", "uuid": "f-uuid-1",
         "compatible_printers": ["Bambu Lab P1S 0.4 nozzle"]},
    ],
}

_FAKE_CAT = catalog_service.catalog_from_dict(_FAKE_CATALOG)


async def test_get_profiles_no_active_preset(client):
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    pid = create.json()["id"]
    response = await client.get(f"/api/v1/printers/{pid}/profiles")
    assert response.status_code == 200
    assert response.json() == {"print_profiles": [], "filament_profiles": []}


async def test_get_profiles_with_active_preset(client):
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4 nozzle"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4 nozzle",
    })
    pid = create.json()["id"]
    with patch("app.api.routes.printers._fetch_sidecar_catalog", return_value=_FAKE_CAT):
        response = await client.get(f"/api/v1/printers/{pid}/profiles")
    assert response.status_code == 200
    data = response.json()
    assert "0.20mm Standard" in data["print_profiles"]
    assert "Bambu PLA Basic" in data["filament_profiles"]


async def test_get_profiles_sidecar_unavailable(client):
    """When the sidecar is down, return empty lists (not local files or hardcoded values)."""
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4 nozzle"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4 nozzle",
    })
    pid = create.json()["id"]
    with patch("app.api.routes.printers._fetch_sidecar_catalog", return_value=None):
        response = await client.get(f"/api/v1/printers/{pid}/profiles")
    assert response.status_code == 200
    assert response.json() == {"print_profiles": [], "filament_profiles": []}


async def test_list_orca_printer_presets(client):
    with patch("app.api.routes.printers._fetch_sidecar_catalog", return_value=_FAKE_CAT):
        response = await client.get("/api/v1/printers/orca-presets")
    assert response.status_code == 200
    assert "Bambu Lab P1S 0.4 nozzle" in response.json()


async def test_list_orca_printer_presets_sidecar_unavailable(client):
    """When the sidecar is down, return an empty list (no local fallback)."""
    with patch("app.api.routes.printers._fetch_sidecar_catalog", return_value=None):
        response = await client.get("/api/v1/printers/orca-presets")
    assert response.status_code == 200
    assert response.json() == []


async def test_rescan_profiles_with_warm_catalog_succeeds(client):
    """Regression: rescan-profiles used to call refresh_catalog() with no session,
    so its Depends(get_session) default (a Depends object, never resolved outside
    the FastAPI framework) hit _apply_drift_gate's `await session.get(...)` once
    the catalog was warm (drift-gate path only runs when a prior catalog exists)."""
    prime_catalog(_FAKE_CATALOG)
    catalog_service._catalog_bytes = b"{}"
    catalog_service._pending_sync = None
    with patch("app.services.catalog_service.fetch_catalog", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.catalog_utils.compute_drift", new_callable=AsyncMock) as mock_drift, \
         patch("app.api.routes.printers._fetch_sidecar_catalog", return_value=_FAKE_CAT):
        mock_fetch.return_value = (b"{}", _FAKE_CAT)
        mock_drift.return_value = None  # no drift
        response = await client.post("/api/v1/printers/rescan-profiles")
    assert response.status_code == 200, response.text
    assert response.json() == {"machine_presets": 1}
