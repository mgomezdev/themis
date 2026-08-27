"""Clear the absolute stored_path column for library-indexed files.

stored_path used to hold an absolute filesystem path, which is not portable — the
library root differs between local dev (repo data/) and the container (/data), so a
path written by one context 404s when read back in another. relative_path (joined with
the current library root at read time) is now the only pointer persisted for a row that
has been indexed into the library. stored_path is retained only for legacy pre-index
rows (relative_path == '') so migrate_legacy_uploads() can still locate them.
"""
from __future__ import annotations
from sqlalchemy import text

version = 16
name = "clear_stored_path"


async def up(conn) -> None:
    await conn.execute(text(
        "UPDATE uploaded_files SET stored_path = '' WHERE relative_path != ''"
    ))


async def down(conn) -> None:
    pass  # stored_path values were absolute paths from a possibly different host/container; not recoverable.
