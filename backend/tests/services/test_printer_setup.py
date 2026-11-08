"""BIZ-369: vendor-neutral printer setup helpers (blank stripping, missing-field check, change detection, the connection test)."""
import pytest

from app.plugins.bambu.client import BambuMQTTClient
from app.services import printer_setup


class _FakeClient:
    def __init__(self, connects: bool, endpoint=None):
        self._connects, self._endpoint = connects, endpoint
        self.connected = False
        self.disconnected = False

    def connect(self, loop=None):
        self.connected = self._connects

    def control_endpoint(self):
        return self._endpoint

    def disconnect(self, *a, **k):
        self.disconnected = True


def test_strip_blank_drops_empty_and_whitespace_values_only():
    assert printer_setup.strip_blank({"a": "", "b": " ", "c": None, "d": 0, "e": "x"}) == {"d": 0, "e": "x"}
    assert printer_setup.strip_blank(None) == {}


def test_missing_required_lists_absent_required_labels():
    fields = BambuMQTTClient.connection_fields()
    assert printer_setup.missing_required(fields, {"ip_address": "1.2.3.4", "access_code": "  "}) == ["Serial Number", "Access Code"]
    assert printer_setup.missing_required(fields, {"ip_address": "1.2.3.4", "serial_number": "S", "access_code": "A"}) == []


def test_missing_reason_joins_labels():
    assert printer_setup.missing_reason(["Serial Number"]) == "Missing Serial Number"
    assert printer_setup.missing_reason(["Serial Number", "Access Code"]) == "Missing Serial Number, Access Code"


def test_connection_changed_ignores_blanks_and_str_int_differences():
    assert printer_setup.connection_changed({"use_ams": 1, "ip_address": "a"},
                                            {"use_ams": "1", "ip_address": "a ", "access_code": ""}) is False
    assert printer_setup.connection_changed({"ip_address": "a"}, {"ip_address": "b"}) is True
    assert printer_setup.connection_changed({"ip_address": "a"}, {"ip_address": "a", "access_code": "x"}) is True
    assert printer_setup.connection_changed({"ip_address": "a", "access_code": "x"}, {"ip_address": "a"}) is True
    assert printer_setup.connection_changed(None, {}) is False


async def test_failure_hint_reports_reachability(monkeypatch):
    monkeypatch.setattr(printer_setup, "_probe", lambda host, port: True)
    hint, reachable = await printer_setup.failure_hint(_FakeClient(False, ("10.0.0.9", 8883)))
    assert reachable is True and hint.startswith("Reached 10.0.0.9:8883")
    monkeypatch.setattr(printer_setup, "_probe", lambda host, port: False)
    hint, reachable = await printer_setup.failure_hint(_FakeClient(False, ("10.0.0.9", 8883)))
    assert reachable is False and hint.startswith("Couldn't reach 10.0.0.9:8883")
    hint, reachable = await printer_setup.failure_hint(_FakeClient(False, None))
    assert (hint, reachable) == ("Could not connect.", None)


async def test_test_unsaved_strips_blanks_and_always_disconnects(monkeypatch):
    built = []

    def factory(ptype, cfg):
        built.append(cfg)
        built.append(c := _FakeClient(True))
        return c

    monkeypatch.setattr(printer_setup, "create_client_from_config", factory)
    out = await printer_setup.test_unsaved("bambu", {"ip_address": "1.2.3.4", "access_code": ""})
    assert out == printer_setup.TestOutcome(ok=True, error=None, reachable=True)
    assert built[0] == {"ip_address": "1.2.3.4"}                        # never sends an empty credential
    assert built[1].disconnected is True


def test_default_name_uses_the_last_octet():
    assert printer_setup.default_name("P1S", "192.168.7.30") == "P1S 30"


# --- evaluate: the saved printer's test ------------------------------------------------------------------------------

async def _saved(session_factory, cfg: dict, **cols) -> int:
    from app.models import Printer
    async with session_factory() as s:
        p = Printer(name="P", plugin_id="bambu", manufacturer_id="bambu", model_id="p1s", printer_type="bambu",
                    connection_config=cfg, configured=False, **cols)
        s.add(p)
        await s.commit()
        return p.id


async def _row(session_factory, pid):
    from app.models import Printer
    async with session_factory() as s:
        return await s.get(Printer, pid)


_FULL = {"ip_address": "10.0.0.9", "serial_number": "S", "access_code": "A"}


async def test_missing_required_field_builds_no_client(session_factory, monkeypatch):
    from app.services.printer_manager import printer_manager
    pid = await _saved(session_factory, {"ip_address": "10.0.0.9"})
    built = []
    monkeypatch.setattr("app.services.printer_client_factory.create_client", lambda p: built.append(p))
    out = await printer_setup.evaluate(pid, session_factory)
    assert out == printer_setup.TestOutcome(False, "Missing Serial Number, Access Code", None)
    assert built == [] and pid not in printer_manager._clients
    row = await _row(session_factory, pid)
    assert (row.configured, row.config_error) == (False, "Missing Serial Number, Access Code")
    assert printer_manager.is_configured(pid) is False


async def test_auto_test_success_configures(session_factory, monkeypatch):
    from app.services.printer_manager import printer_manager
    pid = await _saved(session_factory, _FULL, config_error="old failure")
    printer_manager.set_configured(pid, False)
    broadcasts = []

    async def broadcast(event, payload):
        broadcasts.append((event, payload))

    monkeypatch.setattr(printer_manager, "_on_state_broadcast", broadcast)
    fake = _FakeClient(True)
    fake.connected = True
    monkeypatch.setattr("app.services.printer_client_factory.create_client", lambda p: fake)
    out = await printer_setup.evaluate(pid, session_factory)
    assert out.ok is True
    row = await _row(session_factory, pid)
    assert (row.configured, row.config_error) == (True, None)
    assert printer_manager._clients[pid] is fake and printer_manager.is_configured(pid)
    assert broadcasts == [("printer_configured", {"printer_id": pid, "configured": True, "config_error": None})]


async def _failing(session_factory, monkeypatch, reachable: bool):
    from app.services.printer_manager import printer_manager
    pid = await _saved(session_factory, _FULL)
    fake = _FakeClient(False, ("10.0.0.9", 8883))
    monkeypatch.setattr("app.services.printer_client_factory.create_client", lambda p: fake)
    monkeypatch.setattr(printer_setup, "TEST_CONNECT_POLL_S", 0.1)
    monkeypatch.setattr(printer_setup, "_probe", lambda host, port: reachable)
    out = await printer_setup.evaluate(pid, session_factory)
    return pid, fake, out, printer_manager


async def test_login_failure_tears_client_down(session_factory, monkeypatch):
    pid, fake, out, mgr = await _failing(session_factory, monkeypatch, reachable=True)
    assert (out.ok, out.reachable) == (False, True)
    assert pid not in mgr._clients and fake.disconnected is True          # no reconnect loop against the printer's login
    row = await _row(session_factory, pid)
    assert row.configured is False and row.config_error.startswith("Reached 10.0.0.9:8883")


async def test_unreachable_keeps_client_registered(session_factory, monkeypatch):
    pid, fake, out, mgr = await _failing(session_factory, monkeypatch, reachable=False)
    assert (out.ok, out.reachable) == (False, False)
    assert mgr._clients[pid] is fake and fake.disconnected is False       # its reconnect can still bring the first live connection
    assert (await _row(session_factory, pid)).config_error.startswith("Couldn't reach 10.0.0.9:8883")


async def test_an_exception_is_recorded_not_raised(session_factory, monkeypatch):
    pid = await _saved(session_factory, _FULL)

    def boom(p):
        raise RuntimeError("bad config")

    monkeypatch.setattr("app.services.printer_client_factory.create_client", boom)
    out = await printer_setup.evaluate(pid, session_factory)
    assert out == printer_setup.TestOutcome(False, "bad config", None)
    assert (await _row(session_factory, pid)).config_error == "bad config"


@pytest.mark.auto_test
async def test_auto_tests_run_in_parallel_capped_at_4(monkeypatch):
    import asyncio
    gate, running, peak = asyncio.Event(), [0], [0]

    async def slow(printer_id, session_factory):
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        await gate.wait()
        running[0] -= 1

    monkeypatch.setattr(printer_setup, "evaluate", slow)
    for pid in range(6):
        printer_setup.schedule(pid, None)
    try:
        for _ in range(20):
            await asyncio.sleep(0)
        assert running[0] == 4
    finally:
        gate.set()                                                      # a failed assert must not leave drain() waiting forever
    await printer_setup.drain()
    assert (peak[0], running[0]) == (4, 0)


async def test_test_unsaved_failure_carries_hint(monkeypatch):
    monkeypatch.setattr(printer_setup, "TEST_CONNECT_POLL_S", 0.2)
    monkeypatch.setattr(printer_setup, "_probe", lambda host, port: False)
    monkeypatch.setattr(printer_setup, "create_client_from_config", lambda *a: _FakeClient(False, ("10.0.0.9", 8883)))
    out = await printer_setup.test_unsaved("bambu", {"ip_address": "10.0.0.9"})
    assert out.ok is False and out.reachable is False and "Couldn't reach" in out.error
