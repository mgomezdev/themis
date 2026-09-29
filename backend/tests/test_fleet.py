import logging
from dataclasses import asdict
from types import SimpleNamespace

from httpx import AsyncClient

from app.models import Printer
from app.services.abstract_printer_client import PrinterCapabilities
from app.services.printer_manager import printer_manager


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
    assert p["capabilities"] == {}
    assert p["awaiting_plate_clear"] is False


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


# ---------------------------------------------------------------------------
# Live (connected) branch: telemetry comes from the client through the vendor serializer
# ---------------------------------------------------------------------------

class _FakeElegoo:
    printer_type = "elegoo_centauri"

    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.state = SimpleNamespace(
            connected=connected, state="printing", progress=42.5,
            total_ticks=6000, current_ticks=1200,           # (6000-1200)/60 = 80 minutes left
            filename="benchy.3mf", temperatures={"nozzle": 215.0, "bed": 60.0},
            layer_num=12, total_layers=200, fan_model=50, fan_aux=10, fan_box=0,
            print_speed_pct=120,
        )

    def get_capabilities(self) -> PrinterCapabilities:
        return PrinterCapabilities(pause_resume=True)


async def _add_printer(client: AsyncClient, name: str) -> int:
    resp = await client.post("/api/v1/printers", json={
        "name": name, "printer_type": "elegoo_centauri", "connection_config": {"ip_address": "192.168.1.100"},
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_fleet_serves_live_telemetry_for_a_connected_printer(client: AsyncClient) -> None:
    pid = await _add_printer(client, "Forge")
    printer_manager._clients[pid] = _FakeElegoo()

    (p,) = (await client.get("/api/v1/fleet")).json()

    assert p["id"] == pid
    assert p["name"] == "Forge"
    assert p["connected"] is True
    assert p["state"] == "printing"
    assert p["progress"] == 42.5
    assert p["remaining_time"] == 80
    assert p["current_print"] == "benchy.3mf"
    assert p["temperatures"] == {"nozzle": 215.0, "bed": 60.0}
    assert (p["layer_num"], p["total_layers"]) == (12, 200)
    assert (p["fan_model"], p["fan_aux"], p["fan_box"]) == (50, 10, 0)
    assert p["speed_factor"] == 1.2
    assert p["klippy_state"] == "ready"
    assert p["capabilities"] == asdict(PrinterCapabilities(pause_resume=True))
    assert p["capabilities"]["pause_resume"] is True and p["capabilities"]["camera"] is False


async def test_fleet_live_data_belongs_only_to_the_connected_printer(client: AsyncClient) -> None:
    live_id = await _add_printer(client, "Live")
    await _add_printer(client, "Cold")
    printer_manager._clients[live_id] = _FakeElegoo()

    by_name = {p["name"]: p for p in (await client.get("/api/v1/fleet")).json()}

    assert by_name["Live"]["state"] == "printing"
    assert by_name["Cold"]["state"] == "unknown" and by_name["Cold"]["connected"] is False
    assert by_name["Cold"]["temperatures"] == {}


async def test_fleet_treats_a_registered_but_disconnected_client_as_offline(client: AsyncClient) -> None:
    pid = await _add_printer(client, "Dropped")
    printer_manager._clients[pid] = _FakeElegoo(connected=False)

    (p,) = (await client.get("/api/v1/fleet")).json()

    assert p["connected"] is False
    assert p["state"] == "unknown"
    assert p["progress"] == 0
    assert p["current_print"] is None  # stale client telemetry is not leaked


async def test_fleet_plate_gate_comes_from_the_database_not_the_live_state(client: AsyncClient, session_factory) -> None:
    pid = await _add_printer(client, "Gated")
    printer_manager._clients[pid] = _FakeElegoo()
    async with session_factory() as s:
        (await s.get(Printer, pid)).awaiting_plate_clear = True
        await s.commit()
    assert printer_manager.is_awaiting_plate_clear(pid) is False  # manager disagrees on purpose

    (p,) = (await client.get("/api/v1/fleet")).json()

    assert p["awaiting_plate_clear"] is True


async def test_fleet_survives_a_client_whose_state_cannot_be_read(client: AsyncClient, caplog) -> None:
    good_id = await _add_printer(client, "Good")
    bad_id = await _add_printer(client, "Broken")
    printer_manager._clients[good_id] = _FakeElegoo()

    class _Broken(_FakeElegoo):
        @property
        def state(self):
            raise RuntimeError("vendor SDK exploded")

        @state.setter
        def state(self, _value):
            pass

    printer_manager._clients[bad_id] = _Broken()

    with caplog.at_level(logging.ERROR, logger="app.api.routes.fleet"):
        resp = await client.get("/api/v1/fleet")

    assert resp.status_code == 200
    by_name = {p["name"]: p for p in resp.json()}
    assert by_name["Good"]["state"] == "printing"
    assert by_name["Broken"]["state"] == "unknown" and by_name["Broken"]["connected"] is False
    assert f"Failed to get normalized state for printer {bad_id}" in caplog.text
