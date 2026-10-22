"""v044: machine eligibility for pre-sliced G-code (BIZ-263). `file_machine_eligibility` rows (file -> printer-model UUID),
`uploaded_files.eligibility_known`, `jobs.eligibility_confirmed`. Existing cached slices (`sliced_versions`) are backfilled when
their Orca machine preset maps to exactly one registered printer model; every other legacy G-code file stays explicitly unknown."""
from __future__ import annotations

from sqlalchemy import text

version = 44
name = "file_machine_eligibility"


async def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS file_machine_eligibility (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES uploaded_files(id) ON DELETE CASCADE,
            model_uuid VARCHAR(36) NOT NULL REFERENCES printer_models(id),
            source VARCHAR(16) NOT NULL DEFAULT 'manual',
            UNIQUE (file_id, model_uuid)
        )"""))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_file_machine_eligibility_file_id ON file_machine_eligibility (file_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_file_machine_eligibility_model_uuid ON file_machine_eligibility (model_uuid)"))
    if "eligibility_known" not in await _columns(conn, "uploaded_files"):
        await conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN eligibility_known BOOLEAN NOT NULL DEFAULT 0"))
    if "eligibility_confirmed" not in await _columns(conn, "jobs"):
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN eligibility_confirmed BOOLEAN NOT NULL DEFAULT 0"))

    # Backfill from cached slices: machine_preset -> the printers currently on that preset -> their model UUIDs. Only an
    # unambiguous mapping (exactly one distinct model) is recorded; anything else is left unknown for a human to settle.
    versions = (await conn.execute(text("SELECT file_id, machine_preset FROM sliced_versions"))).fetchall()
    for file_id, preset in versions:
        models = {r[0] for r in (await conn.execute(text(
            "SELECT DISTINCT model_uuid FROM printers WHERE current_orca_printer_profile = :p AND model_uuid IS NOT NULL"),
            {"p": preset})).fetchall()}
        unmatched = (await conn.execute(text(
            "SELECT COUNT(*) FROM printers WHERE current_orca_printer_profile = :p AND model_uuid IS NULL"), {"p": preset})).scalar()
        if len(models) != 1 or unmatched:
            continue
        known = (await conn.execute(text("SELECT eligibility_known FROM uploaded_files WHERE id = :i"), {"i": file_id})).scalar()
        if known:
            continue
        await conn.execute(text(
            "INSERT OR IGNORE INTO file_machine_eligibility (file_id, model_uuid, source) VALUES (:f, :m, 'backfill')"),
            {"f": file_id, "m": next(iter(models))})
        await conn.execute(text("UPDATE uploaded_files SET eligibility_known = 1 WHERE id = :i"), {"i": file_id})


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS file_machine_eligibility"))
    cols = await _columns(conn, "uploaded_files")
    if "eligibility_known" in cols:
        await conn.execute(text("ALTER TABLE uploaded_files DROP COLUMN eligibility_known"))
    if "eligibility_confirmed" in await _columns(conn, "jobs"):
        await conn.execute(text("ALTER TABLE jobs DROP COLUMN eligibility_confirmed"))
