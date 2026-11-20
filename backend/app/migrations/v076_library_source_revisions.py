"""Historical source attribution; existing files and catalog rows are untouched."""

from sqlalchemy import text

version = 76
name = "library_source_revisions"


async def up(conn):
    await conn.execute(
        text("""CREATE TABLE IF NOT EXISTS library_source_revisions (
        file_id INTEGER PRIMARY KEY REFERENCES uploaded_files(id) ON DELETE CASCADE,
        entry_id VARCHAR(36) NOT NULL REFERENCES library_catalog_entries(id) ON DELETE CASCADE,
        revision VARCHAR(1024), descriptor JSON NOT NULL, downloaded_at VARCHAR(40) NOT NULL)""")
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_library_source_revisions_entry_id ON library_source_revisions(entry_id)"
        )
    )


async def down(conn):
    await conn.execute(text("DROP TABLE IF EXISTS library_source_revisions"))
