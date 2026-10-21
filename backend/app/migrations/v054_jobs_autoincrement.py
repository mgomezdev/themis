"""jobs.id is never reused (job events + details design, spec §1).

`jobs` was created by `Base.metadata.create_all` with a plain `INTEGER PRIMARY KEY`, so SQLite hands out `max(id)+1` again after the
newest row is deleted: an id found in an old event, webhook or log could then mean a different job. Rebuild the table with
`AUTOINCREMENT` (the `sqlite_sequence` row is seeded from the copied ids). The live DDL is read from `sqlite_master` and only the
primary key is rewritten, so columns added by earlier migrations and every default/foreign key survive. Idempotent.
`down()` is a no-op: AUTOINCREMENT is harmless to keep."""
from __future__ import annotations

import re

from sqlalchemy import text

version = 54
name = "jobs_autoincrement"
reshapes_schema = True   # rebuilds a table (tests/test_migrations.py skips it when it looks for a remove-only rollback)

_CREATE = re.compile(r'CREATE\s+TABLE\s+(?:"jobs"|`jobs`|\[jobs\]|jobs)\s*\(', re.IGNORECASE)
_ID_COLUMN = re.compile(r'(?P<id>(?<![\w"])"?id"?\s+INTEGER)(?P<nn>\s+NOT\s+NULL)?(?P<pk>\s+PRIMARY\s+KEY)?', re.IGNORECASE)
_TABLE_PK = re.compile(r',\s*PRIMARY\s+KEY\s*\(\s*"?id"?\s*\)', re.IGNORECASE)


def _rewrite(ddl: str) -> str:
    """The `jobs` DDL as `jobs_new` with `id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT`."""
    if not _CREATE.search(ddl):
        raise RuntimeError("jobs DDL has an unexpected shape (no CREATE TABLE jobs); not rewriting it")
    out = _CREATE.sub("CREATE TABLE jobs_new (", ddl, count=1)
    out = _TABLE_PK.sub("", out, count=1)
    out, n = _ID_COLUMN.subn(r"\g<id> NOT NULL PRIMARY KEY AUTOINCREMENT", out, count=1)
    if n != 1:
        raise RuntimeError("jobs DDL has no 'id INTEGER' column; not rewriting it")
    return out


async def up(conn) -> None:
    row = (await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"))).first()
    if row is None or "AUTOINCREMENT" in (row[0] or "").upper():
        return
    ddl = _rewrite(row[0])
    cols = [r[1] for r in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()]
    extras = (await conn.execute(text("SELECT sql FROM sqlite_master WHERE tbl_name='jobs' AND type IN ('index','trigger') "
                                      "AND sql IS NOT NULL"))).fetchall()
    # Children reference jobs(id): enforcement waits for the commit (the pragma is a no-op outside a transaction, where
    # `foreign_keys=OFF` is not allowed to change), by which time the rebuilt table holds every id again.
    await conn.execute(text("PRAGMA defer_foreign_keys=ON"))
    await conn.execute(text("DROP TABLE IF EXISTS jobs_new"))
    await conn.execute(text(ddl))
    quoted = ", ".join(f'"{c}"' for c in cols)
    await conn.execute(text(f"INSERT INTO jobs_new ({quoted}) SELECT {quoted} FROM jobs"))
    await conn.execute(text("DROP TABLE jobs"))
    await conn.execute(text("ALTER TABLE jobs_new RENAME TO jobs"))
    for (sql,) in extras:
        await conn.execute(text(sql))
    broken = (await conn.execute(text("PRAGMA foreign_key_check"))).fetchall()
    if broken:
        raise RuntimeError(f"jobs rebuild left {len(broken)} dangling foreign key(s): {[tuple(b) for b in broken[:5]]}")


async def down(conn) -> None:
    return None
