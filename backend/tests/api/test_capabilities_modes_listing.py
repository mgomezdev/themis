"""GET /capabilities with choose-one / fan-out capabilities (BIZ-250): fan-out with no provider is hidden; both are listed once
provided; PUT /capabilities/{cap}/provider is refused for fan-out and accepted for choose-one (the capability default)."""
import pytest

from app import plugins
from app.plugins.capabilities.definition import CapabilityDef
from app.plugins.host import plugin_host
from app.plugins.manifest import HOST_API, PluginManifest, Provide
from tests.plugins.dummy_plugin import DummyProvider, DummySettings

FAN, CHOOSE = "modes_x.fan", "modes_x.choose"
DEFS = (CapabilityDef(FAN, 1, "Fan", mode="fan_out"), CapabilityDef(CHOOSE, 1, "Choose", mode="choose_one"))


def _manifest(*, provides: bool) -> PluginManifest:
    return PluginManifest(id="modes_x", name="modes_x", version="1.0.0", host_api=HOST_API, settings_model=DummySettings,
                          factory=DummyProvider, provides={FAN: Provide(), CHOOSE: Provide()} if provides else {}, defines=DEFS)


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


async def test_fan_out_is_hidden_until_provided_while_choose_one_is_always_listed(client):
    plugins.register_plugin(_manifest(provides=False))
    ids = await _ids(client)
    assert FAN not in ids and CHOOSE in ids

    plugins._REGISTRY.pop("modes_x")
    plugins.register_plugin(_manifest(provides=True))
    assert FAN in await _ids(client)


async def test_provider_selection_is_refused_for_fan_out_and_sets_the_default_for_choose_one(client):
    plugins.register_plugin(_manifest(provides=True))
    await plugin_host.reload()

    assert (await client.put(f"/api/v1/capabilities/{FAN}/provider", json={"plugin_id": "modes_x"})).status_code == 422
    ok = await client.put(f"/api/v1/capabilities/{CHOOSE}/provider", json={"plugin_id": "modes_x"})
    assert ok.status_code == 200 and ok.json()["plugin_id"] == "modes_x"
