"""BIZ-369: vendor-neutral printer setup helpers (blank stripping, missing-field check, change detection, the connection test)."""
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


async def test_test_unsaved_failure_carries_hint(monkeypatch):
    monkeypatch.setattr(printer_setup, "TEST_CONNECT_POLL_S", 0.2)
    monkeypatch.setattr(printer_setup, "_probe", lambda host, port: False)
    monkeypatch.setattr(printer_setup, "create_client_from_config", lambda *a: _FakeClient(False, ("10.0.0.9", 8883)))
    out = await printer_setup.test_unsaved("bambu", {"ip_address": "10.0.0.9"})
    assert out.ok is False and out.reachable is False and "Couldn't reach" in out.error
