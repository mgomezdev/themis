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
