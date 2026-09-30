import pytest
from app.services.abstract_printer_client import ConnectionField
from app.services.mock_printer_client import MockPrinterClient
from app.services.printer_client_factory import (
    get_printer_types_for_ui,
    create_client,
    create_client_from_config,
    REGISTRY,
)
from app.models import Printer


def _printer(printer_type: str, config: dict) -> Printer:
    p = Printer()
    p.id = 1
    p.name = "Test"
    p.printer_type = printer_type
    p.connection_config = config
    p.orca_printer_profiles = []
    p.current_orca_printer_profile = None
    p.awaiting_plate_clear = False
    p.enabled = True
    return p


def test_ui_type_list_covers_every_registry_entry():
    types = get_printer_types_for_ui()
    assert [t["printer_type"] for t in types] == list(REGISTRY)
    for t in types:
        assert isinstance(t["display_name"], str) and t["display_name"]
        assert all({"name", "label", "field_type", "required"} <= set(f) for f in t["connection_fields"])


def test_get_printer_types_bambu_fields():
    types = {t["printer_type"]: t for t in get_printer_types_for_ui()}
    assert "bambu" in types
    field_names = [f["name"] for f in types["bambu"]["connection_fields"]]
    assert "serial_number" in field_names
    assert "access_code" in field_names


def test_get_printer_types_elegoo_fields():
    types = {t["printer_type"]: t for t in get_printer_types_for_ui()}
    assert "elegoo_centauri" in types
    field_names = [f["name"] for f in types["elegoo_centauri"]["connection_fields"]]
    assert "ip_address" in field_names
    # camera_url is not a connection field — camera URL is derived from IP at port 3031
    assert "camera_url" not in field_names


def _minimal_config(cls) -> dict:
    """Every required connection field, filled from its default or a placeholder."""
    return {f.name: (f.default if f.default is not None else "1.2.3.4")
            for f in cls.connection_fields() if f.required}


@pytest.mark.parametrize("printer_type", list(REGISTRY))
def test_create_client_builds_the_registered_class_for_every_type(printer_type):
    cls = REGISTRY[printer_type]
    assert type(create_client(_printer(printer_type, _minimal_config(cls)))) is cls
    assert type(create_client_from_config(printer_type, _minimal_config(cls))) is cls


class _SpyClient(MockPrinterClient):
    """Records exactly what the factory hands its constructor."""
    calls: list[dict] = []

    def __init__(self, ip_address, on_state=None, **kwargs):
        type(self).calls.append({"ip_address": ip_address, "on_state": on_state, **kwargs})
        super().__init__()

    @classmethod
    def connection_fields(cls):
        return [ConnectionField(name="ip_address", label="IP", field_type="text")]


@pytest.fixture
def spy_type(monkeypatch):
    _SpyClient.calls = []
    monkeypatch.setitem(REGISTRY, "spy", _SpyClient)
    return _SpyClient


def test_create_client_forwards_only_declared_fields_and_accepted_callbacks(spy_type):
    printer = _printer("spy", {"ip_address": "9.9.9.9", "stray": "dropped", "on_state": "not-a-callback"})
    on_state, on_other = object(), object()

    create_client(printer, on_state=on_state, on_other=on_other)

    # 'stray' / config-supplied 'on_state' are not declared fields; the on_state callback is
    # accepted by the constructor, on_other is not -> only ip_address + the real callback arrive.
    assert spy_type.calls == [{"ip_address": "9.9.9.9", "on_state": on_state}]


def test_create_client_from_config_forwards_only_declared_fields(spy_type):
    create_client_from_config("spy", {"ip_address": "9.9.9.9", "extra": "ignored"})

    assert spy_type.calls == [{"ip_address": "9.9.9.9", "on_state": None}]


def test_create_client_from_config_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown printer type"):
        create_client_from_config("unknown_type", {})


def test_create_client_unknown_type_raises():
    printer = _printer("moonraker", {"port": 7125})
    with pytest.raises(ValueError, match="Unknown printer type"):
        create_client(printer)
