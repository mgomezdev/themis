# backend/tests/api/test_queue_wiring.py
import pytest
from unittest.mock import patch, AsyncMock


async def test_plate_cleared_wakes_queue(client):
    # Create a printer
    create = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = create.json()["id"]

    with patch("app.api.routes.printers.queue_engine") as mock_qe:
        with patch("app.api.routes.printers.printer_manager") as mock_pm:
            response = await client.post(f"/api/v1/printers/{printer_id}/plate-cleared")
    assert response.status_code == 200
    mock_qe.wake.assert_called_once()
