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
            "connection_config": {"host": "192.168.1.1", "access_code": "00000000", "serial": "TEST"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "ok" in body
    assert isinstance(body["ok"], bool)
