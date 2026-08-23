from __future__ import annotations

import pytest
import pytest_asyncio
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from fastapi import Depends, FastAPI
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import require_scope
from app.database import Base, get_session
from app.models import ApiKey
from app.services.api_key_service import generate_key, hash_key

pytestmark = pytest.mark.asyncio


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_scope("files:read"))])
    async def protected() -> dict:
        return {"ok": True}

    @app.get("/printers/{id}/snapshot", dependencies=[Depends(require_scope("printers:read"))])
    async def printer_snapshot(id: str) -> dict:
        return {"ok": True}

    return app


@pytest_asyncio.fixture
async def env():
    """A fresh in-memory DB + FastAPI test app wired to it, plus a helper to
    seed ApiKey rows directly."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    app = _make_app()

    async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session

    async def seed_key(scopes: list[str], enabled: bool = True) -> str:
        raw, prefix = generate_key()
        async with factory() as s:
            s.add(ApiKey(
                name="test", key_prefix=prefix, key_hash=hash_key(raw),
                scopes=scopes, enabled=enabled, created_at=_now(),
            ))
            await s.commit()
        return raw

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, seed_key, factory

    await engine.dispose()


async def test_no_key_empty_table_passes_through(env):
    client, _seed_key, _factory = env
    resp = await client.get("/protected")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_no_key_nonempty_table_401(env):
    client, seed_key, _factory = env
    await seed_key(["jobs:read"])  # some unrelated key exists, table non-empty
    resp = await client.get("/protected")
    assert resp.status_code == 401


async def test_bad_key_401(env):
    client, seed_key, _factory = env
    await seed_key(["files:read"])
    resp = await client.get("/protected", headers={"X-Api-Key": "thm_not-a-real-key"})
    assert resp.status_code == 401


async def test_valid_key_missing_scope_403(env):
    client, seed_key, _factory = env
    raw = await seed_key(["jobs:read"])
    resp = await client.get("/protected", headers={"X-Api-Key": raw})
    assert resp.status_code == 403


async def test_valid_key_with_scope_200(env):
    client, seed_key, _factory = env
    raw = await seed_key(["files:read"])
    resp = await client.get("/protected", headers={"X-Api-Key": raw})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_disabled_key_401(env):
    client, seed_key, _factory = env
    raw = await seed_key(["files:read"], enabled=False)
    resp = await client.get("/protected", headers={"X-Api-Key": raw})
    assert resp.status_code == 401


async def test_key_via_query_param(env):
    client, seed_key, _factory = env
    raw = await seed_key(["printers:read"])
    # ?key= works on allowlisted routes (ending in /snapshot)
    resp = await client.get(f"/printers/printer1/snapshot?key={raw}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_key_via_query_param_not_allowed_on_generic_route(env):
    client, seed_key, _factory = env
    raw = await seed_key(["files:read"])
    # ?key= is blocked on non-allowlisted routes
    resp = await client.get(f"/protected?key={raw}")
    assert resp.status_code == 401


async def test_revoked_at_set_without_disabled_flag_401(env):
    client, seed_key, factory = env
    raw = await seed_key(["files:read"])
    # Directly set revoked_at on the key via the session, without flipping enabled.
    # This tests the regression case: revoked_at alone should block the key.
    from sqlalchemy import update
    async with factory() as s:
        await s.execute(
            update(ApiKey).where(ApiKey.key_prefix == raw[:12]).values(revoked_at=_now())
        )
        await s.commit()
    # Key should now be rejected even though enabled is still True
    resp = await client.get("/protected", headers={"X-Api-Key": raw})
    assert resp.status_code == 401


async def test_expired_key_401(env):
    client, seed_key, factory = env
    raw = await seed_key(["files:read"])
    # Set expires_at to a past timestamp
    from sqlalchemy import update
    async with factory() as s:
        await s.execute(
            update(ApiKey).where(ApiKey.key_prefix == raw[:12]).values(expires_at="2020-01-01T00:00:00")
        )
        await s.commit()
    resp = await client.get("/protected", headers={"X-Api-Key": raw})
    assert resp.status_code == 401


async def test_future_expiry_key_200(env):
    client, seed_key, factory = env
    raw = await seed_key(["files:read"])
    # Set expires_at to a far future timestamp
    from sqlalchemy import update
    async with factory() as s:
        await s.execute(
            update(ApiKey).where(ApiKey.key_prefix == raw[:12]).values(expires_at="2099-12-31T23:59:59")
        )
        await s.commit()
    resp = await client.get("/protected", headers={"X-Api-Key": raw})
    assert resp.status_code == 200


async def test_null_expiry_key_still_valid_200(env):
    client, seed_key, _factory = env
    raw = await seed_key(["files:read"])
    # expires_at is None by default; key should still authenticate
    resp = await client.get("/protected", headers={"X-Api-Key": raw})
    assert resp.status_code == 200


async def test_last_used_at_set_on_first_call(env):
    client, seed_key, factory = env
    raw = await seed_key(["files:read"])
    # Before the call, verify last_used_at is None
    from sqlalchemy import select
    async with factory() as s:
        key = (await s.execute(select(ApiKey).where(ApiKey.key_prefix == raw[:12]))).scalar_one()
        assert key.last_used_at is None
    # First authenticated call should set last_used_at
    resp = await client.get("/protected", headers={"X-Api-Key": raw})
    assert resp.status_code == 200
    # Verify last_used_at is now set
    async with factory() as s:
        key = (await s.execute(select(ApiKey).where(ApiKey.key_prefix == raw[:12]))).scalar_one()
        assert key.last_used_at is not None


async def test_last_used_at_not_updated_within_same_minute(env):
    client, seed_key, factory = env
    raw = await seed_key(["files:read"])
    # First call sets last_used_at
    resp = await client.get("/protected", headers={"X-Api-Key": raw})
    assert resp.status_code == 200
    from sqlalchemy import select
    async with factory() as s:
        key = (await s.execute(select(ApiKey).where(ApiKey.key_prefix == raw[:12]))).scalar_one()
        first_used_at = key.last_used_at
        assert first_used_at is not None
    # Second call within same minute should not update last_used_at
    resp = await client.get("/protected", headers={"X-Api-Key": raw})
    assert resp.status_code == 200
    async with factory() as s:
        key = (await s.execute(select(ApiKey).where(ApiKey.key_prefix == raw[:12]))).scalar_one()
        assert key.last_used_at == first_used_at


async def test_last_used_at_updated_from_past_minute(env):
    client, seed_key, factory = env
    raw = await seed_key(["files:read"])
    # Manually set last_used_at to a past minute
    from sqlalchemy import update, select
    past_minute = "2020-01-01T12:00:00"
    async with factory() as s:
        await s.execute(
            update(ApiKey).where(ApiKey.key_prefix == raw[:12]).values(last_used_at=past_minute)
        )
        await s.commit()
    # Verify it was set
    async with factory() as s:
        key = (await s.execute(select(ApiKey).where(ApiKey.key_prefix == raw[:12]))).scalar_one()
        assert key.last_used_at == past_minute
    # Make an authenticated call
    resp = await client.get("/protected", headers={"X-Api-Key": raw})
    assert resp.status_code == 200
    # Verify last_used_at was updated (and is no longer the past value)
    async with factory() as s:
        key = (await s.execute(select(ApiKey).where(ApiKey.key_prefix == raw[:12]))).scalar_one()
        assert key.last_used_at != past_minute
        assert key.last_used_at is not None


async def test_bootstrap_key_with_nonempty_table_200(env, monkeypatch):
    client, seed_key, _factory = env
    bootstrap_value = "thm_bootstrap_secret_key"
    monkeypatch.setenv("THEMIS_BOOTSTRAP_KEY", bootstrap_value)
    # Seed an unrelated key to make table non-empty
    await seed_key(["jobs:read"])
    # Request with the bootstrap key should succeed and get full scopes
    resp = await client.get("/protected", headers={"X-Api-Key": bootstrap_value})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_bootstrap_key_wrong_value_401(env, monkeypatch):
    client, seed_key, _factory = env
    bootstrap_value = "thm_bootstrap_secret_key"
    monkeypatch.setenv("THEMIS_BOOTSTRAP_KEY", bootstrap_value)
    # Seed an unrelated key to make table non-empty
    await seed_key(["jobs:read"])
    # Request with a different value should be rejected
    resp = await client.get("/protected", headers={"X-Api-Key": "thm_wrong_key"})
    assert resp.status_code == 401


async def test_no_bootstrap_key_arbitrary_string_401(env, monkeypatch):
    client, seed_key, _factory = env
    # Ensure THEMIS_BOOTSTRAP_KEY is not set
    monkeypatch.delenv("THEMIS_BOOTSTRAP_KEY", raising=False)
    # Seed a key to make table non-empty
    await seed_key(["files:read"])
    # Request with arbitrary string should be rejected
    resp = await client.get("/protected", headers={"X-Api-Key": "thm_arbitrary_key"})
    assert resp.status_code == 401


async def test_table_is_empty_recomputed_live(env, monkeypatch):
    client, seed_key, factory = env
    # Create a key
    raw = await seed_key(["files:read"])
    # Verify table is not empty and request requires key
    resp = await client.get("/protected")
    assert resp.status_code == 401
    # Delete the key directly from DB (bypassing API)
    from sqlalchemy import delete
    async with factory() as s:
        await s.execute(delete(ApiKey).where(ApiKey.key_prefix == raw[:12]))
        await s.commit()
    # Table is now empty again; unauthenticated request should get through (bootstrap)
    resp = await client.get("/protected")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
