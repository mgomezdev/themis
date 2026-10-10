import pytest
from app.services.abstract_printer_client import ConnectionField
from app.plugins.mock.client import MockPrinterClient
from pydantic import BaseModel

from app import plugins
from app.models import Printer
from app.plugins.manifest import HOST_API, PluginManifest
from app.services.printer_client_factory import (
    client_class,
    create_client,
    create_client_from_config,
    printer_client_plugins,
)

VENDORS = ["bambu", "elegoo_centauri", "snapmaker", "mock"]


def _printer(plugin_id: str, config: dict) -> Printer:
    p = Printer()
    p.id = 1
    p.name = "Test"
    p.plugin_id = plugin_id
    p.printer_type = plugin_id
    p.connection_config = config
    p.orca_printer_profiles = []
    p.current_orca_printer_profile = None
    p.awaiting_plate_clear = False
    p.enabled = True
    return p


def test_every_bundled_vendor_is_a_printer_plugin_with_a_connection_form():
    by_id = {m.id: cls for m, cls in printer_client_plugins()}
    assert set(VENDORS) <= set(by_id)
    for cls in by_id.values():
        assert all(f.name and f.label and f.field_type for f in cls.connection_fields())


def test_bambu_fields():
    field_names = [f.name for f in client_class("bambu").connection_fields()]
    assert "serial_number" in field_names
    assert "access_code" in field_names


def test_elegoo_fields():
    field_names = [f.name for f in client_class("elegoo_centauri").connection_fields()]
    assert "ip_address" in field_names
    # camera_url is not a connection field — camera URL is derived from IP at port 3031
    assert "camera_url" not in field_names


def _minimal_config(cls) -> dict:
    """Every required connection field, filled from its default or a placeholder."""
    return {f.name: (f.default if f.default is not None else "1.2.3.4")
            for f in cls.connection_fields() if f.required}


@pytest.mark.parametrize("plugin_id", VENDORS)
def test_create_client_builds_the_plugins_class_for_every_vendor(plugin_id):
    cls = client_class(plugin_id)
    assert type(create_client(_printer(plugin_id, _minimal_config(cls)))) is cls
    assert type(create_client_from_config(plugin_id, _minimal_config(cls))) is cls


class _SpyClient(MockPrinterClient):
    """Records exactly what the factory hands its constructor."""
    calls: list[dict] = []

    def __init__(self, ip_address, on_state=None, **kwargs):
        type(self).calls.append({"ip_address": ip_address, "on_state": on_state, **kwargs})
        super().__init__()

    @classmethod
    def connection_fields(cls):
        return [ConnectionField(name="ip_address", label="IP", field_type="text")]


class _NoSettings(BaseModel):
    pass


@pytest.fixture
def spy_type(monkeypatch):
    _SpyClient.calls = []
    monkeypatch.setitem(plugins._REGISTRY, "spy_vendor", PluginManifest(
        id="spy_vendor", name="Spy", version="1.0.0", host_api=HOST_API, settings_model=_NoSettings, factory=_SpyClient))
    return _SpyClient


def test_create_client_forwards_only_declared_fields_and_accepted_callbacks(spy_type):
    printer = _printer("spy_vendor", {"ip_address": "9.9.9.9", "stray": "dropped", "on_state": "not-a-callback"})
    on_state, on_other = object(), object()

    create_client(printer, on_state=on_state, on_other=on_other)

    # 'stray' / config-supplied 'on_state' are not declared fields; the on_state callback is
    # accepted by the constructor, on_other is not -> only ip_address + the real callback arrive.
    assert spy_type.calls == [{"ip_address": "9.9.9.9", "on_state": on_state}]


def test_create_client_from_config_forwards_only_declared_fields(spy_type):
    create_client_from_config("spy_vendor", {"ip_address": "9.9.9.9", "extra": "ignored"})

    assert spy_type.calls == [{"ip_address": "9.9.9.9", "on_state": None}]


def test_create_client_from_config_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown printer type"):
        create_client_from_config("unknown_type", {})


def test_create_client_unknown_type_raises():
    printer = _printer("klipperish", {"port": 7125})
    with pytest.raises(ValueError, match="Unknown printer type"):
        create_client(printer)
