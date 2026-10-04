import copy
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient

from app.services.providers.filament_inventory import Filament, InventoryProviderError
from tests import spoolman_mock
from tests.fake_providers import FakeInventoryProvider


@pytest.fixture(autouse=True)
def _restore_spoolman_mock():
    saved = (copy.deepcopy(spoolman_mock._FILAMENTS), copy.deepcopy(spoolman_mock._SPOOLS))
    yield
    spoolman_mock._FILAMENTS[:] = saved[0]
    spoolman_mock._SPOOLS[:] = saved[1]


async def _seed_spoolman(client: AsyncClient) -> None:
    await client.put(
        "/api/v1/settings/spoolman",
        json={"enabled": True, "url": "http://spoolman.test", "api_key": None},
    )


def _stored_bindings(filament_id: int) -> dict:
    fil = next(f for f in spoolman_mock._FILAMENTS if f["id"] == filament_id)
    return json.loads(json.loads(fil["extra"]["orca_profiles"]))


async def test_patch_filament_stores_the_bindings_and_returns_the_updated_filament(client: AsyncClient, spoolman_upstream):
    await _seed_spoolman(client)

    resp = await client.patch(
        "/api/v1/spoolman/filaments/2",
        json={"orca_profiles": {"P1S": ["Bambu PLA @P1S"]}},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 2 and body["name"] == "PLA Black"
    assert json.loads(json.loads(body["extra"]["orca_profiles"])) == {"P1S": ["Bambu PLA @P1S"]}
    # re-read: what Spoolman now holds, and what the list route serves
    assert _stored_bindings(2) == {"P1S": ["Bambu PLA @P1S"]}
    listed = (await client.get("/api/v1/spoolman/filaments")).json()
    assert next(f for f in listed if f["id"] == 2)["extra"] == body["extra"]


async def test_patch_filament_503_when_spoolman_not_configured(client: AsyncClient):
    resp = await client.patch(
        "/api/v1/spoolman/filaments/42",
        json={"orca_profiles": {}},
    )
    assert resp.status_code == 503


async def test_patch_filament_forwards_the_upstream_status(client: AsyncClient, spoolman_upstream):
    await _seed_spoolman(client)
    resp = await client.patch("/api/v1/spoolman/filaments/999", json={"orca_profiles": {}})   # unknown id
    assert resp.status_code == 404


async def test_patch_filament_is_503_with_the_reason_when_spoolman_is_unreachable(client: AsyncClient, spoolman_upstream):
    await _seed_spoolman(client)

    def refuse(request):
        raise httpx.ConnectError("Connection refused")

    spoolman_upstream.handler = refuse
    resp = await client.patch("/api/v1/spoolman/filaments/99", json={"orca_profiles": {}})

    assert resp.status_code == 503
    assert "Connection refused" in resp.json()["detail"]


async def test_patch_filament_501_when_the_provider_has_no_profile_bindings(client: AsyncClient):
    await _seed_spoolman(client)
    provider = FakeInventoryProvider(filaments=[Filament(ref="42", name="PLA")])
    provider.PROFILE_BINDINGS = False
    with patch("app.api.routes.spoolman.get_inventory_provider", AsyncMock(return_value=provider)):
        resp = await client.patch("/api/v1/spoolman/filaments/42", json={"orca_profiles": {"P": ["x"]}})

    assert resp.status_code == 501
    assert provider.filaments["42"].profile_bindings == {}      # nothing written


async def test_patch_filament_passes_a_provider_error_status_through(client: AsyncClient):
    await _seed_spoolman(client)
    provider = FakeInventoryProvider()
    provider.fail_with = InventoryProviderError("Spoolman said no", code="403", status=403)
    with patch("app.api.routes.spoolman.get_inventory_provider", AsyncMock(return_value=provider)):
        resp = await client.patch("/api/v1/spoolman/filaments/1", json={"orca_profiles": {}})
    assert (resp.status_code, resp.json()["detail"]) == (403, "Spoolman said no")


async def test_sync_now_happy_path(client: AsyncClient, spoolman_upstream):
    await _seed_spoolman(client)

    resp = await client.post("/api/v1/spoolman/sync-now")

    assert resp.status_code == 200
    data = resp.json()
    assert data["filament_count"] == len(spoolman_mock._FILAMENTS)
    assert data["spool_count"] == len(spoolman_mock._SPOOLS)


async def test_sync_now_503_when_spoolman_not_configured(client: AsyncClient):
    resp = await client.post("/api/v1/spoolman/sync-now")
    assert resp.status_code == 503


async def test_sync_now_503_when_spoolman_unreachable(client: AsyncClient, spoolman_upstream):
    await _seed_spoolman(client)

    def timeout(request):
        raise httpx.ConnectTimeout("Connection timeout")

    spoolman_upstream.handler = timeout
    resp = await client.post("/api/v1/spoolman/sync-now")

    assert resp.status_code == 503
    assert "Connection timeout" in resp.json()["detail"]
