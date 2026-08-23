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
        yield client, seed_key

    await engine.dispose()


async def test_no_key_empty_table_passes_through(env):
    client, _seed_key = env
    resp = await client.get("/protected")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_no_key_nonempty_table_401(env):
    client, seed_key = env
    await seed_key(["jobs:read"])  # some unrelated key exists, table non-empty
    resp = await client.get("/protected")
    assert resp.status_code == 401


async def test_bad_key_401(env):
    client, seed_key = env
    await seed_key(["files:read"])
    resp = await client.get("/protected", headers={"X-Api-Key": "thm_not-a-real-key"})
    assert resp.status_code == 401


async def test_valid_key_missing_scope_403(env):
    client, seed_key = env
    raw = await seed_key(["jobs:read"])
    resp = await client.get("/protected", headers={"X-Api-Key": raw})
    assert resp.status_code == 403


async def test_valid_key_with_scope_200(env):
    client, seed_key = env
    raw = await seed_key(["files:read"])
    resp = await client.get("/protected", headers={"X-Api-Key": raw})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_disabled_key_401(env):
    client, seed_key = env
    raw = await seed_key(["files:read"], enabled=False)
    resp = await client.get("/protected", headers={"X-Api-Key": raw})
    assert resp.status_code == 401


async def test_key_via_query_param(env):
    client, seed_key = env
    raw = await seed_key(["printers:read"])
    # ?key= works on allowlisted routes (ending in /snapshot)
    resp = await client.get(f"/printers/printer1/snapshot?key={raw}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_key_via_query_param_not_allowed_on_generic_route(env):
    client, seed_key = env
    raw = await seed_key(["files:read"])
    # ?key= is blocked on non-allowlisted routes
    resp = await client.get(f"/protected?key={raw}")
    assert resp.status_code == 401
