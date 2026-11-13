"""Rollback test fixtures to an explicit schema version, independent of newer migrations."""
from sqlalchemy import text

from app.migrations.runner import rollback_last


async def rollback_to(conn, version):
    while (await conn.execute(text("SELECT max(version) FROM schema_migrations"))).scalar() > version:
        await rollback_last(conn)
