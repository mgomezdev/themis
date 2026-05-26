import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_fleet_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/fleet")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_fleet_returns_printer_with_offline_state(client: AsyncClient) -> None:
    await client.post("/api/v1/printers", json={
        "name": "Forge",
        "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "192.168.1.100"},
    })

    resp = await client.get("/api/v1/fleet")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    p = data[0]
    assert p["name"] == "Forge"
    assert p["printer_type"] == "elegoo_centauri"
    assert p["connected"] is False
    assert p["state"] == "unknown"
    assert p["progress"] == 0
    assert p["remaining_time"] == 0
    assert p["temperatures"] == {}
    assert p["layer_num"] is None
    assert p["total_layers"] is None
    assert p["current_print"] is None
    assert p["loaded_filaments"] == []


async def test_fleet_includes_loaded_filaments(client: AsyncClient) -> None:
    filament = {
        "slot": 0,
        "filament_id": None,
        "name": "Bambu PA-CF",
        "type": "PA-CF",
        "color": "#0c0c0c",
    }
    await client.post("/api/v1/printers", json={
        "name": "Forge",
        "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "192.168.1.100"},
        "loaded_filaments": [filament],
    })

    resp = await client.get("/api/v1/fleet")
    assert resp.status_code == 200
    assert resp.json()[0]["loaded_filaments"] == [filament]


async def test_fleet_awaiting_plate_clear_field_present(client: AsyncClient) -> None:
    await client.post("/api/v1/printers", json={
        "name": "Atlas",
        "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "192.168.1.10"},
    })

    resp = await client.get("/api/v1/fleet")
    assert resp.status_code == 200
    assert "awaiting_plate_clear" in resp.json()[0]
