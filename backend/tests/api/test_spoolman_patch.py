from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


async def _seed_spoolman(client: AsyncClient) -> None:
    await client.put(
        "/api/v1/settings/spoolman",
        json={"enabled": True, "url": "http://spoolman.test", "api_key": None},
    )


async def test_patch_filament_returns_updated_filament(client: AsyncClient):
    await _seed_spoolman(client)
    updated = {"id": 42, "name": "PLA Basic", "extra": {"orca_profiles": '{"P1S": ["Bambu PLA @P1S"]}'}}

    with patch(
        "app.api.routes.spoolman.spoolman_service.patch_filament",
        new_callable=AsyncMock,
        return_value=updated,
    ) as mock_patch:
        resp = await client.patch(
            "/api/v1/spoolman/filaments/42",
            json={"orca_profiles": {"P1S": ["Bambu PLA @P1S"]}},
        )

    assert resp.status_code == 200
    assert resp.json() == updated
    mock_patch.assert_called_once_with(
        "http://spoolman.test", None, 42, {"P1S": ["Bambu PLA @P1S"]}
    )


async def test_patch_filament_503_when_spoolman_not_configured(client: AsyncClient):
    resp = await client.patch(
        "/api/v1/spoolman/filaments/42",
        json={"orca_profiles": {}},
    )
    assert resp.status_code == 503


async def test_patch_filament_forwards_spoolman_error(client: AsyncClient):
    await _seed_spoolman(client)
    with patch(
        "app.api.routes.spoolman.spoolman_service.patch_filament",
        new_callable=AsyncMock,
        side_effect=Exception("Connection refused"),
    ):
        resp = await client.patch(
            "/api/v1/spoolman/filaments/99",
            json={"orca_profiles": {}},
        )
    assert resp.status_code == 503
    assert "Connection refused" in resp.json()["detail"]
