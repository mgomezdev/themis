"""Discovery sweeps the printer clients of ENABLED printer plugins only (BIZ-251 Phase D).

Seam: the public `POST /api/v1/printers/discover` route with `_discovery_network` patched to an empty virtual LAN,
so the only thing that can answer is the fake plugin's `discover_host`. Ranges are a single address."""
from unittest.mock import patch

import pytest
import pytest_asyncio

from app import plugins
from app.plugins.host import plugin_host
from tests.services.fake_printer_plugin import FAKE_ID, FakeClient, register_fake
from tests.virtual_printers.virtual_network import VirtualNetwork

IP = FakeClient.discover_target


@pytest.fixture(autouse=True)
def _fake_plugin():
    saved = dict(plugins._REGISTRY)
    register_fake()
    with patch("app.api.routes.printers._discovery_network", return_value=VirtualNetwork()):
        yield
    plugins._REGISTRY.clear()
    plugins._REGISTRY.update(saved)


@pytest_asyncio.fixture
async def host(session_factory):
    await plugin_host.start()
    yield plugin_host


async def _found(client):
    resp = await client.post("/api/v1/printers/discover", json={"ranges": [IP]})
    assert resp.status_code == 200, resp.text
    return resp.json()["found"]


async def test_enabled_plugin_client_is_swept(client, host):
    await host.update_config(FAKE_ID, enabled=True)
    found = await _found(client)
    assert [(f["printer_type"], f["ip"]) for f in found] == [(FAKE_ID, IP)]


async def test_disabled_plugin_client_is_not_swept(client, host):
    await host.update_config(FAKE_ID, enabled=True)
    await host.update_config(FAKE_ID, enabled=False)
    assert await _found(client) == []
