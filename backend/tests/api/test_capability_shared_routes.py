"""The routes every `inventory.filament` provider serves at `/api/v1/capabilities/inventory.filament/…` (BIZ-245): a client of those
paths keeps working when the provider is swapped. Parametrized over the bundled providers."""
import pytest

from app.plugins.capabilities.filament_inventory import CAPABILITY
from app.plugins.host import plugin_host
from tests.inventory_helpers import enable_spoolman

BASE = f"/api/v1/capabilities/{CAPABILITY}"


async def _use(provider_id: str) -> None:
    if provider_id == "spoolman":
        await enable_spoolman()
    else:
        await plugin_host.set_provider(CAPABILITY, provider_id)


@pytest.fixture(params=["spoolman", "local_inventory"])
async def active(request, client):
    await _use(request.param)
    return request.param


async def test_low_stock_is_served_at_the_capability_path_by_every_provider(client, active):
    assert (await client.get(f"{BASE}/low-stock")).json() == {"default_g": None, "overrides": {}}

    put = await client.put(f"{BASE}/low-stock", json={"default_g": 150, "overrides": {"3": 40}})

    assert put.status_code == 200 and put.json() == {"default_g": 150.0, "overrides": {"3": 40.0}}
    assert (await client.get(f"{BASE}/low-stock")).json() == put.json()
    assert (await client.get("/api/v1/inventory/settings")).json()["low_stock"] == put.json()      # same data as the neutral API


async def test_each_providers_thresholds_are_kept_apart_when_the_provider_is_swapped(client):
    await _use("spoolman")
    await client.put(f"{BASE}/low-stock", json={"default_g": 100, "overrides": {"1": 10}})
    await _use("local_inventory")
    assert (await client.get(f"{BASE}/low-stock")).json()["overrides"] == {}
    await client.put(f"{BASE}/low-stock", json={"default_g": 100, "overrides": {"1": 99}})
    await _use("spoolman")

    assert (await client.get(f"{BASE}/low-stock")).json()["overrides"] == {"1": 10.0}


async def test_low_stock_validates_like_the_neutral_settings(client, active):
    assert (await client.put(f"{BASE}/low-stock", json={"default_g": -1})).status_code == 422
    assert (await client.put(f"{BASE}/low-stock", json={"overrides": {"a:1": 5}})).status_code == 422
    assert (await client.get(f"{BASE}/low-stock")).json() == {"default_g": None, "overrides": {}}


async def test_low_stock_needs_the_neutral_inventory_scopes(client, active, session_factory):
    from tests.api.test_inventory_api import _client_with
    reader = await _client_with(session_factory, ["inventory:read"])
    spoolman_only = await _client_with(session_factory, ["spoolman:read", "spoolman:write"])
    async with reader, spoolman_only:
        assert (await reader.get(f"{BASE}/low-stock")).status_code == 200
        assert (await reader.put(f"{BASE}/low-stock", json={"default_g": 1})).status_code == 403
        assert (await spoolman_only.get(f"{BASE}/low-stock")).status_code == 403


async def test_a_provider_specific_extra_is_only_there_for_the_provider_that_has_it(client):
    await _use("local_inventory")
    assert (await client.get(f"{BASE}/weight-log")).status_code == 200
    await _use("spoolman")
    assert (await client.get(f"{BASE}/weight-log")).status_code == 404


async def test_with_no_provider_selected_the_capability_path_is_409(client):
    await plugin_host.set_provider(CAPABILITY, None)
    assert (await client.get(f"{BASE}/low-stock")).status_code == 409
