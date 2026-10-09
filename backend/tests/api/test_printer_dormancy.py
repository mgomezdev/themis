"""A printer whose plugin is disabled or removed stays stored, keeps its identity, is flagged dormant in the fleet
view and is never queue-eligible (BIZ-262)."""
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from app import plugins
from app.plugins.host import plugin_host
from app.plugins.manifest import Manufacturer, PrinterModel
from app.services.printer_manager import printer_manager
from tests.plugins.dummy_plugin import make_manifest

FAKE = "fake_printer"


@pytest.fixture(autouse=True)
def _fake_printer_plugin():
    saved = dict(plugins._REGISTRY)
    plugins.register_plugin(make_manifest(FAKE, default_enabled=True, manufacturers=(
        Manufacturer("acme", "Acme 3D", (PrinterModel("a1", "Acme A1"),)),
    )))
    yield
    plugins._REGISTRY.clear()
    plugins._REGISTRY.update(saved)


@pytest_asyncio.fixture
async def enabled_plugin(session_factory):
    await plugin_host.start()
    await plugin_host.update_config(FAKE, enabled=True)
    yield


async def _create(client) -> int:
    resp = await client.post("/api/v1/printers", json={
        "name": "Dormant candidate", "plugin_id": FAKE, "manufacturer_id": "acme", "model_id": "a1",
        "connection_config": {}, "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _row(session_factory, pid: int) -> dict:
    async with session_factory() as s:
        row = (await s.execute(text(
            "SELECT id, name, plugin_id, manufacturer_id, model_id FROM printers WHERE id = :i"), {"i": pid})).mappings().one()
    return dict(row)


async def _fleet_item(client, pid: int) -> dict:
    resp = await client.get("/api/v1/fleet")
    assert resp.status_code == 200, resp.text
    return next(i for i in resp.json() if i["id"] == pid)


async def test_disabled_plugin_makes_printer_dormant_and_not_queue_eligible(client, session_factory, enabled_plugin):
    pid = await _create(client)
    before = await _row(session_factory, pid)

    await plugin_host.update_config(FAKE, enabled=False)

    item = await _fleet_item(client, pid)
    assert item["dormant"] is True and item["dormant_reason"] == "plugin_disabled"
    assert await _row(session_factory, pid) == before                       # row and identity untouched

    live = MagicMock(connected=True, is_idle=True)                          # would be ready if not dormant
    printer_manager.register_client(pid, live)
    assert printer_manager.is_printer_ready(pid) is False


async def test_re_enabling_the_plugin_clears_dormancy(client, session_factory, enabled_plugin):
    pid = await _create(client)
    before = await _row(session_factory, pid)
    await plugin_host.update_config(FAKE, enabled=False)
    assert (await _fleet_item(client, pid))["dormant"] is True

    await plugin_host.update_config(FAKE, enabled=True)
    item = await _fleet_item(client, pid)
    assert not item.get("dormant")
    assert await _row(session_factory, pid) == before


async def test_removed_plugin_makes_printer_dormant_without_breaking_listing(client, session_factory):
    pid = await _create(client)
    before = await _row(session_factory, pid)

    del plugins._REGISTRY[FAKE]                                             # manifest gone, row remains

    item = await _fleet_item(client, pid)
    assert item["dormant"] is True and item["dormant_reason"] == "plugin_removed"
    listing = await client.get("/api/v1/printers")
    assert listing.status_code == 200
    assert any(p["id"] == pid for p in listing.json())
    assert await _row(session_factory, pid) == before
