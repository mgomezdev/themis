"""A new vendor plugin works without touching core: registering a manifest whose `factory` is an
AbstractPrinterClient subclass is all `create_client` / `create_client_from_config` need (BIZ-251 Phase D)."""
from types import SimpleNamespace

import pytest

from app import plugins
from app.services.printer_client_factory import create_client, create_client_from_config
from tests.services.fake_printer_plugin import FAKE_ID, FakeClient, register_fake


@pytest.fixture(autouse=True)
def _fake_plugin():
    saved = dict(plugins._REGISTRY)
    register_fake()
    yield
    plugins._REGISTRY.clear()
    plugins._REGISTRY.update(saved)


def _printer(plugin_id, config):
    return SimpleNamespace(id=1, plugin_id=plugin_id, printer_type=plugin_id, connection_config=config)


def test_create_client_builds_the_plugins_class_and_drops_undeclared_keys():
    c = create_client(_printer(FAKE_ID, {"ip_address": "9.9.9.9", "use_tls": True, "stray": "x"}))
    assert type(c) is FakeClient
    assert (c.ip_address, c.use_tls) == ("9.9.9.9", True)


def test_create_client_from_config_builds_the_plugins_class_and_drops_undeclared_keys():
    c = create_client_from_config(FAKE_ID, {"ip_address": "1.2.3.4", "stray": "x"})
    assert type(c) is FakeClient
    assert (c.ip_address, c.use_tls) == ("1.2.3.4", False)


def test_unknown_plugin_id_raises_value_error():
    with pytest.raises(ValueError):
        create_client(_printer("no_such_plugin", {}))
    with pytest.raises(ValueError):
        create_client_from_config("no_such_plugin", {})


def test_plugin_whose_factory_is_not_a_client_class_raises_value_error():
    register_fake("not_a_printer", factory=lambda settings: object())
    with pytest.raises(ValueError):
        create_client_from_config("not_a_printer", {"ip_address": "1.2.3.4"})
    with pytest.raises(ValueError):
        create_client(_printer("not_a_printer", {"ip_address": "1.2.3.4"}))
    assert FakeClient.instances == []
