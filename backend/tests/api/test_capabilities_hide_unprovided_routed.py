"""GET /capabilities hides a ROUTED capability nobody provides; exclusive ones are always listed."""
from typing import Protocol, runtime_checkable

import pytest

from app import plugins
from app.plugins.capabilities.definition import CapabilityDef
from app.plugins.host import plugin_host
from app.plugins.manifest import HOST_API, PluginManifest, Provide
from tests.plugins.dummy_plugin import DummyProvider, DummySettings

CAP = "routed_x.ping"


@runtime_checkable
class PingProvider(Protocol):
    def ping(self, value: str) -> str: ...


DEFINITION = CapabilityDef(CAP, 1, "Ping", mode="routed", protocol=PingProvider)


def _manifest(*, provides: bool) -> PluginManifest:
    return PluginManifest(id="routed_x", name="routed_x", version="1.0.0", host_api=HOST_API, settings_model=DummySettings,
                          factory=DummyProvider, provides={CAP: Provide()} if provides else {}, defines=(DEFINITION,))


@pytest.fixture(autouse=True)
async def _empty_registry():
    saved = dict(plugins._REGISTRY)
    plugins._REGISTRY.clear()
    yield
    plugins._REGISTRY.clear()
    plugins._REGISTRY.update(saved)
    await plugin_host.reload()


async def _ids(client) -> set[str]:
    await plugin_host.reload()
    return {c["id"] for c in (await client.get("/api/v1/capabilities")).json()["capabilities"]}


async def test_core_routed_capability_without_a_provider_is_not_listed(client):
    assert "printer.client" not in await _ids(client)


async def test_exclusive_capability_without_a_provider_is_still_listed(client):
    assert "inventory.filament" in await _ids(client)


async def test_plugin_defined_routed_capability_is_hidden_until_a_plugin_provides_it(client):
    plugins.register_plugin(_manifest(provides=False))
    assert CAP not in await _ids(client)

    plugins._REGISTRY.pop("routed_x")
    plugins.register_plugin(_manifest(provides=True))
    assert CAP in await _ids(client)
