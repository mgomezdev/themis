"""Metadata-first external library catalog, separate from the local file index."""
from sqlalchemy import text

version = 75
name = "library_catalog"


async def up(conn):
    await conn.execute(text('''CREATE TABLE IF NOT EXISTS library_catalog_entries (
        id VARCHAR(36) PRIMARY KEY, provider_plugin_id VARCHAR(64) NOT NULL,
        source_file_id VARCHAR(1024) NOT NULL, source_url VARCHAR(2048) NOT NULL UNIQUE,
        descriptor JSON NOT NULL, last_seen_sweep VARCHAR(36) NOT NULL,
        missing BOOLEAN NOT NULL DEFAULT 0, updated_at VARCHAR(40) NOT NULL,
        cached_file_id INTEGER REFERENCES uploaded_files(id) ON DELETE SET NULL,
        UNIQUE(provider_plugin_id, source_file_id))'''))
    await conn.execute(text('''CREATE TABLE IF NOT EXISTS library_catalog_sync_leases (
        provider_plugin_id VARCHAR(64) PRIMARY KEY, owner VARCHAR(36) NOT NULL, expires_at FLOAT NOT NULL)'''))
    await conn.execute(text('CREATE INDEX IF NOT EXISTS ix_library_catalog_provider ON library_catalog_entries(provider_plugin_id)'))


async def down(conn):
    await conn.execute(text('DROP TABLE IF EXISTS library_catalog_sync_leases'))
    await conn.execute(text('DROP TABLE IF EXISTS library_catalog_entries'))
