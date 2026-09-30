from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import SCOPES
from app.database import Base, get_session
from app.main import app

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    """Starts with an EMPTY api_keys table (unlike conftest's `client`), so tests can check that
    an empty table is not an open door (the old bootstrap hatch is gone) and seed their own key.
    Shadows conftest's `client` fixture for every test in this module.

    Backed by a real on-disk SQLite file rather than `:memory:`: an in-memory
    DB's default StaticPool hands every checkout the *same* physical
    connection, so two genuinely concurrent AsyncSessions (as in
    test_concurrent_bootstrap_race_condition below) end up sharing one
    connection's single SQLite transaction context instead of each getting
    its own — breaking the isolation the race-condition test depends on. A
    file-backed DB gives each session a real, independently-locked
    connection, so SQLite's own locking (not a shared Python object) decides
    who wins the race."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
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
    """Seed one full-scope key directly in the DB (there is no HTTP bootstrap any more) and
    return (raw_key, headers)."""
    from app.models import ApiKey
    from app.services.api_key_service import generate_key, hash_key

    raw, prefix = generate_key()
    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    session.add(ApiKey(name="Bootstrap", key_prefix=prefix, key_hash=hash_key(raw),
                       scopes=sorted(SCOPES), enabled=True, created_at="2026-01-01T00:00:00"))
    await session.commit()
    await agen.aclose()
    return raw, {"X-Api-Key": raw}


async def test_empty_table_is_not_an_open_door(client: AsyncClient):
    """Bootstrap hatch removed: with no keys at all, a remote unauthenticated caller can't
    mint one or read anything."""
    resp = await client.post("/api/v1/api-keys", json={"name": "Browser", "scopes": ["jobs:read"]})
    assert resp.status_code == 401
    assert (await client.get("/api/v1/api-keys")).status_code == 401
    assert (await client.get("/api/v1/projects")).status_code == 401


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
    rows = (await client.get("/api/v1/api-keys", headers=headers)).json()
    assert [r["name"] for r in rows] == ["Bootstrap"]  # no key was minted


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


# The "last apikeys:write key" guard can only fire when the caller is NOT that key (a caller holding
# apikeys:write is itself one, so revoking any other key always leaves it). The env bootstrap key is
# such a caller: it authenticates without being a row in api_keys.

@pytest.fixture
def env_admin_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("THEMIS_BOOTSTRAP_KEY", "bootstrap-secret-not-in-db")
    return {"X-Api-Key": "bootstrap-secret-not-in-db"}


async def _only_key(client: AsyncClient, headers: dict[str, str]) -> dict:
    rows = (await client.get("/api/v1/api-keys", headers=headers)).json()
    assert len(rows) == 1
    return rows[0]


async def test_cannot_revoke_last_apikeys_write_key(client: AsyncClient, env_admin_headers):
    _raw, own_headers = await _bootstrap(client)
    key = await _only_key(client, own_headers)

    resp = await client.post(f"/api/v1/api-keys/{key['id']}/revoke", headers=env_admin_headers)

    assert resp.status_code == 400
    assert "last key" in resp.json()["detail"]
    after = await _only_key(client, own_headers)  # still there, still enabled, not revoked
    assert (after["enabled"], after["revoked_at"]) == (True, None)


async def test_cannot_delete_last_apikeys_write_key(client: AsyncClient, env_admin_headers):
    _raw, own_headers = await _bootstrap(client)
    key = await _only_key(client, own_headers)

    resp = await client.delete(f"/api/v1/api-keys/{key['id']}", headers=env_admin_headers)

    assert resp.status_code == 400
    assert "last key" in resp.json()["detail"]
    assert (await _only_key(client, own_headers))["id"] == key["id"]


async def test_can_revoke_a_apikeys_write_key_while_another_remains(client: AsyncClient, env_admin_headers):
    """The guard is about the LAST one: with a second apikeys:write key present, revoking works."""
    _raw, own_headers = await _bootstrap(client)
    second = (await client.post(
        "/api/v1/api-keys", json={"name": "Second", "scopes": ["apikeys:write"]}, headers=own_headers,
    )).json()

    resp = await client.post(f"/api/v1/api-keys/{second['id']}/revoke", headers=env_admin_headers)

    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


async def test_cannot_revoke_own_api_key(client: AsyncClient):
    raw, headers = await _bootstrap(client)
    key = await _only_key(client, headers)

    resp = await client.post(f"/api/v1/api-keys/{key['id']}/revoke", headers=headers)

    assert resp.status_code == 400
    assert "own" in resp.json().get("detail", "").lower()
    after = await _only_key(client, headers)
    assert (after["enabled"], after["revoked_at"]) == (True, None)


async def test_cannot_delete_own_api_key(client: AsyncClient):
    raw, headers = await _bootstrap(client)
    key = await _only_key(client, headers)

    resp = await client.delete(f"/api/v1/api-keys/{key['id']}", headers=headers)

    assert resp.status_code == 400
    assert "own" in resp.json().get("detail", "").lower()
    assert (await _only_key(client, headers))["id"] == key["id"]


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


async def test_create_key_grants_only_requested_scopes(client: AsyncClient):
    _raw, headers = await _bootstrap(client)
    resp = await client.post("/api/v1/api-keys", json={"name": "Narrow", "scopes": ["files:read"]},
                             headers=headers)
    assert resp.status_code == 200
    assert resp.json()["scopes"] == ["files:read"]
