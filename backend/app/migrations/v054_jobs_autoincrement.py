"""jobs.id is never reused (job events + details design, spec §1).

`jobs` was created by `Base.metadata.create_all` with a plain `INTEGER PRIMARY KEY`, so SQLite hands out `max(id)+1` again after the
newest row is deleted: an id found in an old event, webhook or log could then mean a different job. The table needs `AUTOINCREMENT`.

The obvious fix, rebuilding the table (create new, copy, drop, rename), is unsafe here: production connections run with
`foreign_keys=ON`, `DROP TABLE jobs` performs an implicit `DELETE FROM jobs` first, and that fires `ON DELETE CASCADE` / `SET NULL` on every
child table (`PRAGMA defer_foreign_keys` only defers the check, not the actions; `PRAGMA foreign_keys=OFF` is a no-op inside the migration
transaction). So the table is not touched: only its stored CREATE statement is rewritten (`PRAGMA writable_schema`, the same on-disk
format, the schema cookie bumped so every connection re-reads it) and the `sqlite_sequence` row is seeded from the current maximum id.
Every row, index, trigger and child row stays exactly where it is. Idempotent. `down()` is a no-op: AUTOINCREMENT is harmless to keep."""
from __future__ import annotations

import re

from sqlalchemy import text

version = 54
name = "jobs_autoincrement"
reshapes_schema = True   # rewrites a table's definition (tests/test_migrations.py skips it when it looks for a remove-only rollback)

_CREATE = re.compile(r'CREATE\s+TABLE\s+(?:"jobs"|`jobs`|\[jobs\]|jobs)\s*\(', re.IGNORECASE)
_ID_COLUMN = re.compile(r'(?P<id>(?<![\w"])"?id"?\s+INTEGER)(?P<nn>\s+NOT\s+NULL)?(?P<pk>\s+PRIMARY\s+KEY)?', re.IGNORECASE)
_TABLE_PK = re.compile(r',\s*PRIMARY\s+KEY\s*\(\s*"?id"?\s*\)', re.IGNORECASE)


def _rewrite(ddl: str) -> str:
    """The `jobs` DDL with `id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT` (the table keeps its name)."""
    if not _CREATE.search(ddl):
        raise RuntimeError("jobs DDL has an unexpected shape (no CREATE TABLE jobs); not rewriting it")
    out = _TABLE_PK.sub("", ddl, count=1)
    out, n = _ID_COLUMN.subn(r"\g<id> NOT NULL PRIMARY KEY AUTOINCREMENT", out, count=1)
    if n != 1:
        raise RuntimeError("jobs DDL has no 'id INTEGER' column; not rewriting it")
    return out


async def up(conn) -> None:
    row = (await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"))).first()
    if row is None or "AUTOINCREMENT" in (row[0] or "").upper():
        return
    ddl = _rewrite(row[0])
    cookie = (await conn.execute(text("PRAGMA schema_version"))).scalar_one()
    await conn.execute(text("PRAGMA writable_schema=ON"))
    try:
        await conn.execute(text("UPDATE sqlite_master SET sql = :sql WHERE type = 'table' AND name = 'jobs'"), {"sql": ddl})
    finally:
        await conn.execute(text("PRAGMA writable_schema=OFF"))
    await conn.execute(text(f"PRAGMA schema_version = {int(cookie) + 1}"))
    await conn.execute(text("DELETE FROM sqlite_sequence WHERE name = 'jobs'"))
    await conn.execute(text("INSERT INTO sqlite_sequence (name, seq) SELECT 'jobs', COALESCE(MAX(id), 0) FROM jobs"))
    check = (await conn.execute(text("PRAGMA integrity_check"))).scalars().all()
    if check != ["ok"]:
        raise RuntimeError(f"jobs schema rewrite failed the integrity check: {check[:3]}")
    if "AUTOINCREMENT" not in ((await conn.execute(text("SELECT sql FROM sqlite_master WHERE name='jobs'"))).scalar_one() or "").upper():
        raise RuntimeError("this SQLite refuses to rewrite its schema (writable_schema is disabled); jobs.id was left as it was")


async def down(conn) -> None:
    return None
