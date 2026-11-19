from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import runner, v075_library_catalog as migration


async def test_catalog_migration_is_idempotent_and_reverses_without_removing_local_files(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text('CREATE TABLE uploaded_files (id INTEGER PRIMARY KEY, original_filename TEXT)'))
            await conn.execute(text("INSERT INTO uploaded_files VALUES (1, 'kept.stl')"))
            await migration.up(conn)
            await conn.execute(text("""INSERT INTO library_catalog_entries
                (id, provider_plugin_id, source_file_id, source_url, descriptor, last_seen_sweep, missing, updated_at, cached_file_id)
                VALUES ('entry', 'source', 'remote', 'https://source.test/remote', '{}', 'sweep', 0, 'now', 1)"""))
            await migration.up(conn)
            assert (await conn.execute(text('SELECT id, cached_file_id FROM library_catalog_entries'))).all() == [('entry', 1)]
            await migration.down(conn)
            await migration.down(conn)
            assert (await conn.execute(text('SELECT * FROM uploaded_files'))).all() == [(1, 'kept.stl')]
    finally:
        await engine.dispose()


def test_catalog_migration_is_registered():
    assert runner.v075_library_catalog is migration
    assert migration in runner._MIGRATIONS
