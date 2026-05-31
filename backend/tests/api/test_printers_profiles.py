import pytest
from unittest.mock import patch


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
    with patch("app.api.routes.printers._profile_index") as mock_idx:
        mock_idx.compatible_profiles.return_value = {
            "print_profiles": ["0.20mm Standard"],
            "filament_profiles": ["Bambu PLA Basic"],
        }
        response = await client.get(f"/api/v1/printers/{pid}/profiles")
    assert response.status_code == 200
    data = response.json()
    assert "0.20mm Standard" in data["print_profiles"]
    mock_idx.compatible_profiles.assert_called_once_with("Bambu Lab P1S 0.4 nozzle")


async def test_list_orca_printer_presets(client):
    with patch("app.api.routes.printers.ProfileService") as MockSvc:
        MockSvc.return_value.get_printer_preset_names.return_value = ["Bambu Lab P1S 0.4 nozzle"]
        response = await client.get("/api/v1/printers/orca-presets")
    assert response.status_code == 200
    assert "Bambu Lab P1S 0.4 nozzle" in response.json()
