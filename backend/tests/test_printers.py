import pytest


async def test_test_connection_unknown_type(client):
    resp = await client.post(
        "/api/v1/printers/test-connection",
        json={"printer_type": "not_a_real_type", "connection_config": {}},
    )
    assert resp.status_code == 422


async def test_test_connection_known_type_returns_ok_field(client):
    # With a real printer type but no actual hardware, connect() will fail gracefully.
    # The endpoint must return a JSON object with an "ok" key regardless.
    resp = await client.post(
        "/api/v1/printers/test-connection",
        json={
            "printer_type": "bambu",
            "connection_config": {"ip_address": "192.168.1.1", "access_code": "00000000", "serial_number": "TEST"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "ok" in body
    assert isinstance(body["ok"], bool)


async def test_list_printers_includes_connected_field(client):
    # Create a printer via the API
    resp = await client.post(
        "/api/v1/printers",
        json={
            "name": "Test Printer",
            "printer_type": "bambu",
            "connection_config": {"ip_address": "192.168.1.10", "access_code": "12345678", "serial_number": "SN001"},
        },
    )
    assert resp.status_code == 201

    # List printers — must include connected field
    resp = await client.get("/api/v1/printers")
    assert resp.status_code == 200
    printers = resp.json()
    assert len(printers) == 1
    assert "connected" in printers[0]
    # No live client in tests, so connected must be False
    assert printers[0]["connected"] is False


async def test_loaded_filaments_defaults_to_empty_list(client):
    resp = await client.post(
        "/api/v1/printers",
        json={
            "name": "Test",
            "printer_type": "bambu",
            "connection_config": {"ip_address": "1.1.1.1", "access_code": "00000000", "serial_number": "SN1"},
        },
    )
    assert resp.status_code == 201
    assert resp.json()["loaded_filaments"] == []


async def test_create_printer_with_loaded_filaments(client):
    slots = [{"slot": 0, "filament_id": None, "name": "Bambu PLA Matte", "type": "PLA", "color": "#ff0000"}]
    resp = await client.post(
        "/api/v1/printers",
        json={
            "name": "Test",
            "printer_type": "bambu",
            "connection_config": {"ip_address": "1.1.1.2", "access_code": "00000000", "serial_number": "SN2"},
            "loaded_filaments": slots,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["loaded_filaments"] == slots


async def test_patch_loaded_filaments(client):
    resp = await client.post(
        "/api/v1/printers",
        json={
            "name": "Test",
            "printer_type": "bambu",
            "connection_config": {"ip_address": "1.1.1.3", "access_code": "00000000", "serial_number": "SN3"},
        },
    )
    printer_id = resp.json()["id"]
    slots = [{"slot": 0, "filament_id": None, "name": "Bambu PETG HF", "type": "PETG", "color": "#00aaff"}]
    resp = await client.patch(f"/api/v1/printers/{printer_id}", json={"loaded_filaments": slots})
    assert resp.status_code == 200
    assert resp.json()["loaded_filaments"] == slots


async def test_loaded_filaments_null_filament_id_roundtrips(client):
    slots = [{"slot": 0, "filament_id": None, "name": "Generic PLA", "type": "PLA", "color": "#cccccc"}]
    resp = await client.post(
        "/api/v1/printers",
        json={
            "name": "Test",
            "printer_type": "bambu",
            "connection_config": {"ip_address": "1.1.1.4", "access_code": "00000000", "serial_number": "SN4"},
            "loaded_filaments": slots,
        },
    )
    assert resp.status_code == 201
    result = resp.json()["loaded_filaments"][0]
    assert result["filament_id"] is None
