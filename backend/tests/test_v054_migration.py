"""v054: jobs.id is never reused (AUTOINCREMENT). The rebuild keeps every row, default, index and child foreign key."""
import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v054_jobs_autoincrement as mig
from app.migrations.runner import _CREATE_TABLE, _MIGRATIONS, run_migrations
from tests.v032_fixture import build_v032_fixture_db


async def _migrate_to(conn, max_version: int) -> None:
    await conn.execute(text(_CREATE_TABLE))
    applied = {r[0] for r in (await conn.execute(text("SELECT version FROM schema_migrations"))).fetchall()}
    for m in _MIGRATIONS:
        if m.version <= max_version and m.version not in applied:
            await m.up(conn)
            await conn.execute(text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"), {"v": m.version, "n": m.name})


async def _ddl(conn) -> str:
    return (await conn.execute(text("SELECT sql FROM sqlite_master WHERE name='jobs'"))).scalar_one()


def _fk_on(engine):
    """Production connections run with foreign keys ON; so must these, or a rebuild that cascades would go unnoticed."""
    @event.listens_for(engine.sync_engine, "connect")
    def _pragma(dbapi_conn, _record):
        dbapi_conn.cursor().execute("PRAGMA foreign_keys=ON")
    return engine


async def _children(conn) -> list[tuple[str, str]]:
    """Every (table, column) whose foreign key points at jobs."""
    out = []
    for (name,) in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))).fetchall():
        for fk in (await conn.execute(text(f'PRAGMA foreign_key_list("{name}")'))).fetchall():
            if fk[2] == "jobs":
                out.append((name, fk[3]))
    return out


async def _to_plain_pk(conn) -> None:
    """Put jobs back to a plain `INTEGER PRIMARY KEY` (what existing databases have; create_all now emits AUTOINCREMENT) without touching a row."""
    ddl = await _ddl(conn)
    assert "AUTOINCREMENT" in ddl.upper()
    cookie = (await conn.execute(text("PRAGMA schema_version"))).scalar_one()
    await conn.execute(text("PRAGMA writable_schema=ON"))
    await conn.execute(text("UPDATE sqlite_master SET sql=:s WHERE type='table' AND name='jobs'"), {"s": ddl.replace(" PRIMARY KEY AUTOINCREMENT", " PRIMARY KEY")})
    await conn.execute(text("PRAGMA writable_schema=OFF"))
    await conn.execute(text(f"PRAGMA schema_version={int(cookie) + 1}"))
    await conn.execute(text("DELETE FROM sqlite_sequence WHERE name='jobs'"))
    assert "AUTOINCREMENT" not in (await _ddl(conn)).upper()


@pytest.fixture
async def v053_db(tmp_path):
    path = await build_v032_fixture_db(tmp_path / "v032.db")
    engine = _fk_on(create_async_engine(f"sqlite+aiosqlite:///{path}"))
    async with engine.begin() as conn:
        await _migrate_to(conn, 53)
        await _to_plain_pk(conn)       # the production shape before v054 (create_all now emits AUTOINCREMENT)
    yield engine
    await engine.dispose()


async def test_the_rewrite_keeps_every_row_and_every_child_row_and_stops_id_reuse_with_foreign_keys_on(v053_db):
    async with v053_db.begin() as conn:
        assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
        assert "AUTOINCREMENT" not in (await _ddl(conn)).upper()
        before = [dict(r) for r in (await conn.execute(text("SELECT * FROM jobs ORDER BY id"))).mappings().all()]
        children = await _children(conn)
        counts = {t: (await conn.execute(text(f'SELECT COUNT(*) FROM "{t}"'))).scalar_one() for t, _ in children}
        idx_before = {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='jobs'"))).fetchall()}
        assert before and counts["job_printer_configs"] > 0, "the fixture must contain jobs and child rows"
        assert len(children) >= 4                                              # several tables hang off jobs; the CASCADE ones are the dangerous ones
        await run_migrations(conn)
    async with v053_db.begin() as conn:
        assert "AUTOINCREMENT" in (await _ddl(conn)).upper()
        assert [dict(r) for r in (await conn.execute(text("SELECT * FROM jobs ORDER BY id"))).mappings().all()] == before
        for t, _ in children:                                                   # not one child row was cascaded away or nulled
            assert (await conn.execute(text(f'SELECT COUNT(*) FROM "{t}"'))).scalar_one() == counts[t], t
        assert (await conn.execute(text("PRAGMA foreign_key_check"))).fetchall() == []
        assert (await conn.execute(text("PRAGMA integrity_check"))).scalars().all() == ["ok"]
        idx_after = {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='jobs'"))).fetchall()}
        assert idx_before <= idx_after
        top = max(r["id"] for r in before)
        assert (await conn.execute(text("SELECT seq FROM sqlite_sequence WHERE name='jobs'"))).scalar_one() == top
    async with v053_db.begin() as conn:                                         # delete the newest job (its children first): its id must not come back
        for t, col in children:
            await conn.execute(text(f'DELETE FROM "{t}" WHERE "{col}" = :i'), {"i": top})
        await conn.execute(text("DELETE FROM jobs WHERE id=:i"), {"i": top})
        template = dict(before[-1])
        template.pop("id")
        await conn.execute(text(f"INSERT INTO jobs ({', '.join(template)}) VALUES ({', '.join(':' + c for c in template)})"), template)
        assert (await conn.execute(text("SELECT MAX(id) FROM jobs"))).scalar_one() == top + 1


async def test_a_second_connection_sees_the_new_schema(v053_db):
    async with v053_db.begin() as conn:
        await run_migrations(conn)
    async with v053_db.connect() as other:
        assert "AUTOINCREMENT" in (await _ddl(other)).upper()


async def test_up_is_idempotent(v053_db):
    async with v053_db.begin() as conn:
        await mig.up(conn)
        first = await _ddl(conn)
        await mig.up(conn)
        assert await _ddl(conn) == first
        assert first.upper().count("AUTOINCREMENT") == 1


async def test_rewrite_rejects_a_ddl_it_does_not_understand():
    with pytest.raises(RuntimeError):
        mig._rewrite("CREATE TABLE other (id INTEGER)")
    with pytest.raises(RuntimeError):
        mig._rewrite("CREATE TABLE jobs (name TEXT)")


async def test_up_creates_sqlite_sequence_when_no_other_autoincrement_table_exists(tmp_path):
    """SQLite makes sqlite_sequence with the first AUTOINCREMENT table; the schema rewrite bypasses that, so the migration must."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bare.db'}")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE jobs (id INTEGER NOT NULL, name TEXT, PRIMARY KEY (id))"))
        await conn.execute(text("INSERT INTO jobs (id, name) VALUES (3, 'a')"))
        assert (await conn.execute(text("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'"))).first() is None
        await mig.up(conn)
        assert (await conn.execute(text("SELECT seq FROM sqlite_sequence WHERE name='jobs'"))).scalar_one() == 3
        await conn.execute(text("INSERT INTO jobs (name) VALUES ('b')"))
        assert (await conn.execute(text("SELECT MAX(id) FROM jobs"))).scalar_one() == 4
    await engine.dispose()
