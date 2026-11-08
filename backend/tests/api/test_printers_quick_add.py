"""BIZ-369: a printer is created from its identity (model + IP) alone, saved unconfigured and tested in the background; only a
real change to how it connects voids a confirmed setup; a saved printer can be re-tested on demand."""
import pytest
from sqlalchemy import text

from app.models import Printer
from app.services import printer_setup
from app.services.printer_manager import printer_manager

ELEGOO = {"plugin_id": "elegoo_centauri", "manufacturer_id": "elegoo", "model_id": "centauri"}
P1S = {"plugin_id": "bambu", "manufacturer_id": "bambu", "model_id": "p1s"}


async def _post(client, **body):
    return await client.post("/api/v1/printers", json=body)


async def _row(session_factory, printer_id) -> Printer:
    async with session_factory() as s:
        return await s.get(Printer, printer_id)


async def test_create_with_only_ip_and_model(client, session_factory, scheduled_tests):
    r = await _post(client, name="C1", **ELEGOO, connection_config={"ip_address": "10.0.0.5", "port": "", "x": None})
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["configured"], body["config_error"]) == (False, None)
    row = await _row(session_factory, body["id"])
    assert row.connection_config == {"ip_address": "10.0.0.5"}            # blanks are omitted, never stored as ""
    assert row.configured is False
    assert scheduled_tests == [body["id"]]
    assert printer_manager.is_configured(body["id"]) is False
    assert body["id"] not in printer_manager._clients                    # the background test connects it, not the request


async def test_create_without_ip_is_422(client, session_factory):
    r = await _post(client, name="C1", **ELEGOO, connection_config={"ip_address": "  "})
    assert r.status_code == 422 and r.json()["detail"] == "connection_config.ip_address is required"
    async with session_factory() as s:
        assert (await s.execute(text("SELECT COUNT(*) FROM printers"))).scalar() == 0


async def test_duplicate_ip_and_model_is_409(client, session_factory):
    assert (await _post(client, name="A", **ELEGOO, connection_config={"ip_address": "10.0.0.5"})).status_code == 201
    r = await _post(client, name="B", **ELEGOO, connection_config={"ip_address": " 10.0.0.5 "})
    assert r.status_code == 409
    assert r.json()["detail"] == "A already uses 10.0.0.5 as Centauri Carbon"
    async with session_factory() as s:
        assert (await s.execute(text("SELECT COUNT(*) FROM printers"))).scalar() == 1


async def test_same_ip_other_model_is_allowed(client):
    assert (await _post(client, name="A", **ELEGOO, connection_config={"ip_address": "10.0.0.5"})).status_code == 201
    assert (await _post(client, name="B", **P1S, connection_config={"ip_address": "10.0.0.5"})).status_code == 201


async def test_default_name_from_model_and_octet(client):
    r = await _post(client, **P1S, connection_config={"ip_address": "192.168.7.30"})
    assert r.status_code == 201 and r.json()["name"] == "P1S 30"


async def _configured_bambu(create_printer, **cfg) -> int:
    return await create_printer(connection_config={"ip_address": "10.0.0.9", "serial_number": "S1", "access_code": "A1",
                                                   "use_ams": 1, **cfg})


async def test_patch_connection_change_unconfigures_and_retests(client, create_printer, session_factory, scheduled_tests):
    pid = await _configured_bambu(create_printer)
    scheduled_tests.clear()
    r = await client.patch(f"/api/v1/printers/{pid}", json={"connection_config": {
        "ip_address": "10.0.0.9", "serial_number": "S1", "access_code": "NEW", "use_ams": 1, "flow_cali": ""}})
    assert r.status_code == 200
    assert (r.json()["configured"], r.json()["config_error"]) == (False, None)
    row = await _row(session_factory, pid)
    assert row.configured is False
    assert row.connection_config == {"ip_address": "10.0.0.9", "serial_number": "S1", "access_code": "NEW", "use_ams": 1}
    assert scheduled_tests == [pid] and printer_manager.is_configured(pid) is False


@pytest.mark.parametrize("patch", [
    {"name": "Renamed"},
    {"model_id": "p1s"},                                                   # same model within the same plugin
    {"queue_on": False, "build_plate_type": "Textured PEI"},
    {"connection_config": {"ip_address": "10.0.0.9 ", "serial_number": "S1", "access_code": "A1", "use_ams": "1",
                           "bed_leveling": ""}},                           # identical values, re-saved by the form
])
async def test_patch_without_a_connection_change_keeps_configured(client, create_printer, session_factory, scheduled_tests,
                                                                  patch):
    pid = await _configured_bambu(create_printer)
    scheduled_tests.clear()
    r = await client.patch(f"/api/v1/printers/{pid}", json=patch)
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is True
    assert (await _row(session_factory, pid)).configured is True
    assert scheduled_tests == [] and printer_manager.is_configured(pid) is True


class _FakeClient:
    def __init__(self, connected: bool, endpoint=None):
        self.connected, self._endpoint = connected, endpoint
        self.disconnected = False

    def control_endpoint(self):
        return self._endpoint

    def disconnect(self, *a, **k):
        self.disconnected = True


async def test_saved_printer_test_endpoint(client, create_printer, session_factory, monkeypatch):
    pid = await create_printer(configured=False, **ELEGOO, connection_config={"ip_address": "10.0.0.3"})
    fake = _FakeClient(connected=True)
    monkeypatch.setattr("app.services.printer_client_factory.create_client", lambda printer: fake)
    r = await client.post(f"/api/v1/printers/{pid}/test")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "error": None, "configured": True, "config_error": None}
    assert (await _row(session_factory, pid)).configured is True
    assert printer_manager._clients[pid] is fake and printer_manager.is_configured(pid)     # left connected


async def test_saved_printer_test_endpoint_404(client):
    assert (await client.post("/api/v1/printers/999/test")).status_code == 404


async def test_saved_printer_test_reports_the_failure(client, create_printer, session_factory, monkeypatch):
    pid = await create_printer(configured=False, **ELEGOO, connection_config={"ip_address": "10.0.0.3"})
    monkeypatch.setattr(printer_setup, "TEST_CONNECT_POLL_S", 0.1)
    monkeypatch.setattr(printer_setup, "_probe", lambda host, port: False)
    monkeypatch.setattr("app.services.printer_client_factory.create_client",
                        lambda printer: _FakeClient(connected=False, endpoint=("10.0.0.3", 3030)))
    r = await client.post(f"/api/v1/printers/{pid}/test")
    body = r.json()
    assert (body["ok"], body["configured"]) == (False, False)
    assert body["error"].startswith("Couldn't reach 10.0.0.3:3030") and body["config_error"] == body["error"]
