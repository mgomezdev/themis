"""Plate thumbnails as filecache keys (BIZ-286): `uploaded_files.plates[].thumbnail_path` used to hold an ABSOLUTE filecache
path (it pinned the data dir of the machine that wrote it); it now holds the store key `<file_id>/thumbnails/<name>`.
Only the basename was ever used to serve a thumbnail, so old and new forms read back identically and this is a pure
normalisation. `down` is a no-op: the previous code reads either form, so there is nothing to undo (and no absolute path
to rebuild)."""
from __future__ import annotations
import json

from sqlalchemy import text

version = 49
data_only = True        # changes rows, not schema (tests/test_migrations.py skips such a migration when it looks for a schema rollback)
name = "thumbnail_keys"


def _key(file_id: int, path: str) -> str:
    return f"{file_id}/thumbnails/{path.replace(chr(92), '/').rsplit('/', 1)[-1]}"


async def up(conn) -> None:
    rows = (await conn.execute(text("SELECT id, plates FROM uploaded_files WHERE plates IS NOT NULL"))).fetchall()
    for file_id, raw in rows:
        try:
            plates = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        if not isinstance(plates, list):
            continue
        changed = False
        for plate in plates:
            path = plate.get("thumbnail_path") if isinstance(plate, dict) else None
            if isinstance(path, str) and path and path != _key(file_id, path):
                plate["thumbnail_path"] = _key(file_id, path)
                changed = True
        if changed:
            await conn.execute(text("UPDATE uploaded_files SET plates = :p WHERE id = :i"),
                               {"p": json.dumps(plates), "i": file_id})


async def down(conn) -> None:
    return None
