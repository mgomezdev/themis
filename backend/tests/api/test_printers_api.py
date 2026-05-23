import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select
from app.models import Printer
from app.services.abstract_printer_client import PrinterCapabilities


async def test_get_printer_types(client):
    response = await client.get("/api/v1/printers/types")
    assert response.status_code == 200
    types = response.json()
    assert isinstance(types, list)
    printer_type_names = [t["printer_type"] for t in types]
    assert "bambu" in printer_type_names
    assert "elegoo_centauri" in printer_type_names


async def test_list_printers_empty(client):
    response = await client.get("/api/v1/printers")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_printer(client):
    payload = {
        "name": "X1 Carbon",
        "printer_type": "bambu",
        "connection_config": {"ip_address": "192.168.1.10", "serial_number": "ABC", "access_code": "secret"},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4",
    }
    response = await client.post("/api/v1/printers", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "X1 Carbon"
    assert data["printer_type"] == "bambu"
    assert data["id"] is not None


async def test_get_printer(client):
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {"ip_address": "1.2.3.4", "serial_number": "X", "access_code": "Y"},
        "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]
    response = await client.get(f"/api/v1/printers/{printer_id}")
    assert response.status_code == 200
    assert response.json()["id"] == printer_id


async def test_get_printer_not_found(client):
    response = await client.get("/api/v1/printers/9999")
    assert response.status_code == 404


async def test_update_printer(client):
    create = await client.post("/api/v1/printers", json={
        "name": "Old Name", "printer_type": "bambu",
        "connection_config": {}, "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]
    response = await client.patch(f"/api/v1/printers/{printer_id}", json={"name": "New Name"})
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"


async def test_delete_printer(client):
    create = await client.post("/api/v1/printers", json={
        "name": "Temp", "printer_type": "bambu",
        "connection_config": {}, "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]
    response = await client.delete(f"/api/v1/printers/{printer_id}")
    assert response.status_code == 204
    response = await client.get(f"/api/v1/printers/{printer_id}")
    assert response.status_code == 404


async def test_plate_cleared_sets_gate(client):
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {}, "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]
    with patch("app.api.routes.printers.printer_manager") as mock_mgr:
        with patch("app.api.routes.printers.queue_engine") as mock_qe:
            response = await client.post(f"/api/v1/printers/{printer_id}/plate-cleared")
    assert response.status_code == 200
    mock_mgr.set_awaiting_plate_clear.assert_called_once_with(printer_id, False)


async def test_switch_active_preset(client):
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4", "Bambu Lab P1S 0.2"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4",
    })
    printer_id = create.json()["id"]
    response = await client.patch(
        f"/api/v1/printers/{printer_id}/active-preset",
        json={"preset": "Bambu Lab P1S 0.2"},
    )
    assert response.status_code == 200
    assert response.json()["current_orca_printer_profile"] == "Bambu Lab P1S 0.2"


async def test_switch_active_preset_invalid(client):
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4",
    })
    printer_id = create.json()["id"]
    response = await client.patch(
        f"/api/v1/printers/{printer_id}/active-preset",
        json={"preset": "Not A Real Preset"},
    )
    assert response.status_code == 422
