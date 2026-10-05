import pytest

from app import plugins
from app.plugins.host import PluginHost
from tests.plugins.dummy_plugin import DummyProvider


@pytest.fixture(autouse=True)
def _isolated_registry():
    saved = dict(plugins._REGISTRY)
    plugins._REGISTRY.clear()
    DummyProvider.instances.clear()
    yield
    plugins._REGISTRY.clear()
    plugins._REGISTRY.update(saved)


@pytest.fixture
def host(session_factory):
    h = PluginHost()
    h.configure(session_factory)
    return h
