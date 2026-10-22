"""PrinterManager.connect_all_enabled_printers records each printer's plugin, so dormancy applies after a restart."""
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from app import plugins
from app.models import Printer
from app.plugins.host import plugin_host
from app.plugins.manifest import Manufacturer, PrinterModel
from app.services import printer_manager as pm_module
from app.services.printer_manager import PrinterManager
from tests.plugins.dummy_plugin import make_manifest

FAKE = "fake_printer"


@pytest.fixture(autouse=True)
def _fake_printer_plugin():
    saved = dict(plugins._REGISTRY)
    plugins.register_plugin(make_manifest(FAKE, manufacturers=(
        Manufacturer("acme", "Acme 3D", (PrinterModel("a1", "Acme A1"),)),
    )))
    yield
    plugins._REGISTRY.clear()
    plugins._REGISTRY.update(saved)


@pytest_asyncio.fixture
async def started(session_factory):
    await plugin_host.start()


async def _startup(session_factory, monkeypatch) -> tuple[PrinterManager, int]:
    async with session_factory() as s:
        printer = Printer(name="Restarted", printer_type=FAKE, plugin_id=FAKE, manufacturer_id="acme", model_id="a1",
                          connection_config={}, enabled=True)
        s.add(printer)
        await s.commit()
        pid = printer.id
    monkeypatch.setattr(pm_module, "create_client", lambda printer: MagicMock(connected=True, is_idle=True))
    mgr = PrinterManager()
    mgr.connect_printer = mgr.register_client                  # no real connection, just register the fake client
    await mgr.connect_all_enabled_printers(session_factory)
    return mgr, pid


async def test_enabled_plugin_printer_is_ready_after_startup(session_factory, monkeypatch, started):
    await plugin_host.update_config(FAKE, enabled=True)

    mgr, pid = await _startup(session_factory, monkeypatch)

    assert mgr.is_printer_ready(pid) is True


async def test_disabled_plugin_printer_is_not_ready_after_startup(session_factory, monkeypatch, started):
    await plugin_host.update_config(FAKE, enabled=False)

    mgr, pid = await _startup(session_factory, monkeypatch)

    assert pid in mgr._clients and mgr.is_printer_ready(pid) is False


async def test_removed_plugin_printer_is_not_ready_after_startup(session_factory, monkeypatch, started):
    await plugin_host.update_config(FAKE, enabled=True)
    del plugins._REGISTRY[FAKE]

    mgr, pid = await _startup(session_factory, monkeypatch)

    assert pid in mgr._clients and mgr.is_printer_ready(pid) is False
