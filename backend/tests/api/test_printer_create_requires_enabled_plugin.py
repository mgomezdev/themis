"""Creating a printer on a disabled plugin is refused (422, nothing stored); a wizard-created printer stores the plugin
client's printer_type (BIZ-251 review)."""
import pytest
import pytest_asyncio
from sqlalchemy import text

from app import plugins
from app.plugins.host import plugin_host
from app.plugins.manifest import Manufacturer, PrinterModel
from tests.plugins.dummy_plugin import make_manifest

FAKE = "fake_printer"
IDENTITY = {"plugin_id": FAKE, "manufacturer_id": "acme", "model_id": "a1"}


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
async def host_started(session_factory):
    await plugin_host.start()
    yield
    await plugin_host.update_config("mock", enabled=True)       # leave the default-enabled mock plugin as found


async def _count(session_factory) -> int:
    async with session_factory() as s:
        return (await s.execute(text("SELECT COUNT(*) FROM printers"))).scalar_one()


async def _post(client, **fields):
    return await client.post("/api/v1/printers", json={"name": "P", "connection_config": {}, **fields})


async def test_identity_create_on_a_disabled_plugin_is_422_and_stores_nothing(client, session_factory, host_started):
    resp = await _post(client, **IDENTITY)

    assert resp.status_code == 422
    assert "disabled" in str(resp.json()["detail"]).lower()
    assert await _count(session_factory) == 0


async def test_enabling_the_plugin_lets_the_same_identity_create_succeed(client, session_factory, host_started):
    assert (await _post(client, **IDENTITY)).status_code == 422
    await plugin_host.update_config(FAKE, enabled=True)

    resp = await _post(client, **IDENTITY)

    assert resp.status_code == 201, resp.text
    assert await _count(session_factory) == 1


async def test_legacy_printer_type_on_a_disabled_plugin_is_422_and_stores_nothing(client, session_factory, host_started):
    await plugin_host.update_config("mock", enabled=False)

    resp = await _post(client, printer_type="mock")

    assert resp.status_code == 422
    assert "disabled" in str(resp.json()["detail"]).lower()
    assert await _count(session_factory) == 0

    await plugin_host.update_config("mock", enabled=True)
    assert (await _post(client, printer_type="mock")).status_code == 201
    assert await _count(session_factory) == 1


@pytest.mark.parametrize("plugin_id, manufacturer, model, expected", [
    ("snapmaker", "snapmaker", "u1_extended", "snapmaker_extended"),
    ("bambu", "bambu", "p1s", "bambu"),
    ("elegoo_centauri", "elegoo", "centauri", "elegoo_centauri"),
])
async def test_wizard_created_printer_stores_the_client_class_printer_type(
        client, session_factory, host_started, plugin_id, manufacturer, model, expected):
    await plugin_host.update_config(plugin_id, enabled=True)

    resp = await _post(client, plugin_id=plugin_id, manufacturer_id=manufacturer, model_id=model)

    assert resp.status_code == 201, resp.text
    assert resp.json()["printer_type"] == expected
    async with session_factory() as s:
        stored = (await s.execute(text("SELECT printer_type, plugin_id FROM printers WHERE id = :i"),
                                  {"i": resp.json()["id"]})).one()
    assert tuple(stored) == (expected, plugin_id)


async def test_plugin_whose_factory_is_not_a_client_class_stores_the_plugin_id(client, session_factory, host_started):
    await plugin_host.update_config(FAKE, enabled=True)

    resp = await _post(client, **IDENTITY)

    assert resp.status_code == 201, resp.text
    assert resp.json()["printer_type"] == FAKE


async def test_a_legacy_printer_type_is_kept_verbatim(client, host_started):
    await plugin_host.update_config("bambu", enabled=True)

    resp = await _post(client, printer_type="bambu")

    assert resp.status_code == 201, resp.text
    assert resp.json()["printer_type"] == "bambu"
