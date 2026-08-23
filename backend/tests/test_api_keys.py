from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import SCOPES
from app.database import Base, get_session
from app.main import app

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """These tests exercise the real bootstrap-hatch behavior (empty api_keys
    table grants unauthenticated access to the first POST), so — unlike the
    shared `client` fixture in conftest.py — this one does NOT pre-seed a key.
    Shadows conftest's `client` fixture for every test in this module."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


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


async def test_cannot_revoke_own_api_key(client: AsyncClient):
    raw, headers = await _bootstrap(client)
    list_resp = await client.get("/api/v1/api-keys", headers=headers)
    key_id = list_resp.json()[0]["id"]

    resp = await client.post(f"/api/v1/api-keys/{key_id}/revoke", headers=headers)
    assert resp.status_code == 400
    assert "own" in resp.json().get("detail", "").lower()


async def test_cannot_delete_own_api_key(client: AsyncClient):
    raw, headers = await _bootstrap(client)
    list_resp = await client.get("/api/v1/api-keys", headers=headers)
    key_id = list_resp.json()[0]["id"]

    resp = await client.delete(f"/api/v1/api-keys/{key_id}", headers=headers)
    assert resp.status_code == 400
    assert "own" in resp.json().get("detail", "").lower()


async def test_create_second_key_with_zero_scopes_rejects_400(client: AsyncClient):
    _raw, headers = await _bootstrap(client)
    resp = await client.post(
        "/api/v1/api-keys",
        json={"name": "ZeroScope", "scopes": []},
        headers=headers,
    )
    assert resp.status_code == 400


async def test_get_scopes_returns_sorted_list_matching_auth_scopes(client: AsyncClient):
    raw, headers = await _bootstrap(client)
    resp = await client.get("/api/v1/api-keys/scopes", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert set(data) == SCOPES


async def test_create_key_with_expires_at(client: AsyncClient):
    _raw, headers = await _bootstrap(client)
    expires_at = "2099-12-31T23:59:59"
    resp = await client.post(
        "/api/v1/api-keys",
        json={"name": "ExpireTest", "scopes": ["files:read"], "expires_at": expires_at},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["expires_at"] == expires_at
    assert data["name"] == "ExpireTest"


async def test_concurrent_bootstrap_race_condition(client: AsyncClient):
    """Regression: two concurrent bootstrap POSTs (empty api_keys table, no scopes)
    must result in exactly one 200 (with full scopes) and one 400 (scope required).
    BootstrapSentinel insert should win on one request, causing the other to fail
    bootstrap detection and reject due to zero scopes."""

    async def make_bootstrap_request():
        return await client.post("/api/v1/api-keys", json={"name": "Bootstrap", "scopes": []})

    # Fire two requests concurrently; one wins the sentinel insert, one loses
    resp1, resp2 = await asyncio.gather(make_bootstrap_request(), make_bootstrap_request())

    # Exactly one 200, one 400
    statuses = sorted([resp1.status_code, resp2.status_code])
    assert statuses == [200, 400], f"Expected [200, 400], got {statuses}"

    # The 200 response: full-scopes bootstrap key
    success_resp = resp1 if resp1.status_code == 200 else resp2
    success_data = success_resp.json()
    assert set(success_data["scopes"]) == SCOPES
    assert success_data["name"] == "Bootstrap"
    assert "key" in success_data
    assert success_data["key"].startswith("thm_")

    # The 400 response: zero scopes not allowed (non-bootstrap path)
    fail_resp = resp2 if resp1.status_code == 200 else resp1
    fail_data = fail_resp.json()
    assert "scope" in fail_data.get("detail", "").lower()

    # Verify exactly 1 key in database by listing with the successful key
    headers = {"X-Api-Key": success_data["key"]}
    list_resp = await client.get("/api/v1/api-keys", headers=headers)
    assert list_resp.status_code == 200
    keys = list_resp.json()
    assert len(keys) == 1
    assert keys[0]["id"] == success_data["id"]


async def test_create_key_with_empty_sentinel_but_existing_keys_respects_scopes(client: AsyncClient):
    """Regression: bootstrap_sentinel starts empty on any fresh migration run,
    including an upgraded deployment that already has rows in api_keys. Without
    the _table_is_empty() precondition guarding the sentinel-insert attempt, the
    first create_key call on such a system would still win the (empty) sentinel
    insert and silently escalate a narrow-scope request to full access — this
    reproduces that exact scenario by clearing bootstrap_sentinel back to empty
    after a normal bootstrap, so api_keys is non-empty but the sentinel isn't."""
    from sqlalchemy import text

    _, headers = await _bootstrap(client)

    session_gen = app.dependency_overrides[get_session]()
    session = await session_gen.__anext__()
    await session.execute(text("DELETE FROM bootstrap_sentinel"))
    await session.commit()

    resp = await client.post(
        "/api/v1/api-keys",
        json={"name": "Narrow", "scopes": ["files:read"]},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["scopes"] == ["files:read"]
    assert set(data["scopes"]) != SCOPES
