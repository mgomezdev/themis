"""job_model_targets.model_uuid becomes nullable (BIZ-357): NULL = an "Any printer" target. SQLite can't relax NOT NULL in
place, so the table is rebuilt from its own current DDL (whatever columns earlier migrations added), rows and ids kept.
Idempotent. `down` drops the Any-printer targets (and the configs materialized from them) and restores NOT NULL."""
from __future__ import annotations

import re

from sqlalchemy import text

version = 58
name = "any_printer_target"
reshapes_schema = True    # relaxes a constraint: rollback_last generic checks (schema only shrinks) do not apply

_NOT_NULL = re.compile(r"(\bmodel_uuid\s+VARCHAR\(36\))\s+NOT NULL", re.I)


async def _ddl(conn) -> str | None:
    return (await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='job_model_targets'"))).scalar()


async def _rebuild(conn, ddl: str) -> None:
    cols = ", ".join(r[1] for r in (await conn.execute(text("PRAGMA table_info(job_model_targets)"))).fetchall())
    await conn.execute(text("ALTER TABLE job_model_targets RENAME TO job_model_targets_old"))
    await conn.execute(text(ddl))
    await conn.execute(text(f"INSERT INTO job_model_targets ({cols}) SELECT {cols} FROM job_model_targets_old"))
    await conn.execute(text("DROP TABLE job_model_targets_old"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_model_targets_job_id ON job_model_targets (job_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_model_targets_model_uuid ON job_model_targets (model_uuid)"))


async def up(conn) -> None:
    ddl = await _ddl(conn)
    if ddl is None or not _NOT_NULL.search(ddl):
        return
    await _rebuild(conn, _NOT_NULL.sub(r"\1", ddl, count=1))


async def down(conn) -> None:
    ddl = await _ddl(conn)
    if ddl is None or _NOT_NULL.search(ddl):
        return
    any_ids = "SELECT id FROM job_model_targets WHERE model_uuid IS NULL"
    await conn.execute(text(f"DELETE FROM job_printer_configs WHERE model_target_id IN ({any_ids})"))
    await conn.execute(text("DELETE FROM job_model_targets WHERE model_uuid IS NULL"))
    await _rebuild(conn, re.sub(r"(\bmodel_uuid\s+VARCHAR\(36\))", r"\1 NOT NULL", ddl, count=1, flags=re.I))
