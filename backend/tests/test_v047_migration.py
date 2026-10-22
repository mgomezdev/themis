"""Migration v047 (BIZ-172): external_ref + its partial unique index, idempotency keys, webhook destinations (legacy webhook migrated)."""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v047_project_hooks as mig


@pytest.fixture
async def conn():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c:
        await c.execute(text("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, source_app VARCHAR(50))"))
        await c.execute(text("CREATE TABLE webhook_config (id INTEGER PRIMARY KEY, url TEXT, secret TEXT, events JSON)"))
        yield c
    await engine.dispose()


async def _names(c, kind):
    return {r[0] for r in (await c.execute(text(f"SELECT name FROM sqlite_master WHERE type='{kind}'"))).fetchall()}


async def test_up_adds_the_column_index_and_tables_and_is_idempotent(conn):
    await mig.up(conn)
    await mig.up(conn)
    assert "external_ref" in {r[1] for r in (await conn.execute(text("PRAGMA table_info(projects)"))).fetchall()}
    assert {"idempotency_keys", "webhook_destinations"} <= await _names(conn, "table")
    assert "ux_projects_source_external_ref" in await _names(conn, "index")


async def test_the_index_is_unique_per_source_app_and_ignores_null_refs(conn):
    await mig.up(conn)
    ins = "INSERT INTO projects (name, source_app, external_ref) VALUES (:n, :s, :r)"
    await conn.execute(text(ins), {"n": "a", "s": "shop", "r": "1"})
    await conn.execute(text(ins), {"n": "b", "s": "other", "r": "1"})           # another source app: fine
    await conn.execute(text(ins), {"n": "c", "s": "shop", "r": None})
    await conn.execute(text(ins), {"n": "d", "s": "shop", "r": None})           # NULL refs never collide
    with pytest.raises(IntegrityError):
        await conn.execute(text(ins), {"n": "e", "s": "shop", "r": "1"})


async def test_a_configured_legacy_webhook_becomes_the_default_destination(conn):
    await conn.execute(text("INSERT INTO webhook_config (id, url, secret, events) VALUES (1, 'https://old.test/h', 's', :e)"),
                       {"e": json.dumps(["job.complete"])})
    await mig.up(conn)
    row = (await conn.execute(text("SELECT name, url, secret, events, enabled FROM webhook_destinations"))).fetchone()
    assert (row[0], row[1], row[2], json.loads(row[3]), row[4]) == ("default", "https://old.test/h", "s", ["job.complete"], 1)


@pytest.mark.parametrize("legacy", [None, "INSERT INTO webhook_config (id, url, secret, events) VALUES (1, NULL, NULL, '[]')"])
async def test_an_unconfigured_legacy_webhook_creates_no_destination(conn, legacy):
    if legacy:
        await conn.execute(text(legacy))
    await mig.up(conn)
    assert (await conn.execute(text("SELECT count(*) FROM webhook_destinations"))).scalar() == 0


async def test_down_removes_everything_it_added(conn):
    await mig.up(conn)
    await mig.down(conn)
    assert "external_ref" not in {r[1] for r in (await conn.execute(text("PRAGMA table_info(projects)"))).fetchall()}
    assert not ({"idempotency_keys", "webhook_destinations"} & await _names(conn, "table"))
    assert "ux_projects_source_external_ref" not in await _names(conn, "index")
