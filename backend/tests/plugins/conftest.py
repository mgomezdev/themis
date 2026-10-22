import pytest

from app import plugins
from app.plugins.host import PluginHost
from tests.plugins.dummy_plugin import DummyProvider


@pytest.fixture(autouse=True)
def _isolated_registry():
    saved, saved_reports = dict(plugins._REGISTRY), dict(plugins._UI_REPORTS)
    plugins._REGISTRY.clear()
    DummyProvider.instances.clear()
    yield
    plugins._REGISTRY.clear()
    plugins._REGISTRY.update(saved)
    plugins._UI_REPORTS.clear()
    plugins._UI_REPORTS.update(saved_reports)


@pytest.fixture
def host(session_factory):
    h = PluginHost()
    h.configure(session_factory)
    return h
