from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.auth import SCOPES

pytestmark = pytest.mark.asyncio


async def _bootstrap(client: AsyncClient) -> tuple[str, dict[str, str]]:
    """Create the first (bootstrap, full-access) key and return (raw_key, headers)
    so subsequent calls in these tests — which now require apikeys:* scopes since
    the table is no longer empty — can authenticate."""
    resp = await client.post("/api/v1/api-keys", json={"name": "Bootstrap", "scopes": []})
    raw = resp.json()["key"]
    return raw, {"X-Api-Key": raw}


async def test_create_first_key_ignores_requested_scopes_grants_all(client: AsyncClient):
    resp = await client.post("/api/v1/api-keys", json={"name": "Browser", "scopes": ["jobs:read"]})
    assert resp.status_code == 200
    data = resp.json()
    assert set(data["scopes"]) == SCOPES
    assert data["name"] == "Browser"
    assert "key" in data
    assert data["key"].startswith("thm_")


async def test_create_second_key_with_explicit_scopes(client: AsyncClient):
    _raw, headers = await _bootstrap(client)
    resp = await client.post(
        "/api/v1/api-keys",
        json={"name": "Ordinus", "scopes": ["files:write", "projects:write"]},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["scopes"] == ["files:write", "projects:write"]


async def test_create_unknown_scope_422(client: AsyncClient):
    _raw, headers = await _bootstrap(client)
    resp = await client.post(
        "/api/v1/api-keys", json={"name": "Bad", "scopes": ["not:a:scope"]}, headers=headers,
    )
    assert resp.status_code == 422


async def test_list_never_includes_raw_key_or_hash(client: AsyncClient):
    _raw, headers = await _bootstrap(client)
    resp = await client.get("/api/v1/api-keys", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert "key" not in data[0]
    assert "key_hash" not in data[0]
    assert data[0]["key_prefix"].startswith("thm_")


async def test_revoke_sets_disabled_and_revoked_at(client: AsyncClient):
    _raw, headers = await _bootstrap(client)
    second = await client.post(
        "/api/v1/api-keys", json={"name": "Second", "scopes": ["apikeys:write"]}, headers=headers,
    )
    second_id = second.json()["id"]

    resp = await client.post(f"/api/v1/api-keys/{second_id}/revoke", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["revoked_at"] is not None


async def test_delete_removes_row(client: AsyncClient):
    _raw, headers = await _bootstrap(client)
    second = await client.post(
        "/api/v1/api-keys", json={"name": "Second", "scopes": ["apikeys:write"]}, headers=headers,
    )
    second_id = second.json()["id"]

    resp = await client.delete(f"/api/v1/api-keys/{second_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    list_resp = await client.get("/api/v1/api-keys", headers=headers)
    ids = [row["id"] for row in list_resp.json()]
    assert second_id not in ids


async def test_cannot_revoke_last_apikeys_write_key(client: AsyncClient):
    raw, headers = await _bootstrap(client)
    list_resp = await client.get("/api/v1/api-keys", headers=headers)
    key_id = list_resp.json()[0]["id"]

    resp = await client.post(f"/api/v1/api-keys/{key_id}/revoke", headers=headers)
    assert resp.status_code == 400


async def test_cannot_delete_last_apikeys_write_key(client: AsyncClient):
    raw, headers = await _bootstrap(client)
    list_resp = await client.get("/api/v1/api-keys", headers=headers)
    key_id = list_resp.json()[0]["id"]

    resp = await client.delete(f"/api/v1/api-keys/{key_id}", headers=headers)
    assert resp.status_code == 400
