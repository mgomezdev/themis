from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v068_project_group_overrides as migration
from app.migrations.runner import _MIGRATIONS


async def test_group_override_migration_is_registered_idempotent_and_preserves_groups():
    assert migration in _MIGRATIONS
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE project_groups (id INTEGER PRIMARY KEY, name TEXT)"))
        await conn.execute(text("INSERT INTO project_groups VALUES (1, 'Existing group')"))
        await migration.up(conn)
        await migration.up(conn)
        assert (await conn.execute(text("SELECT name, overrides FROM project_groups"))).one() == ("Existing group", None)
        await migration.down(conn)
        await migration.down(conn)
        assert "overrides" not in {row[1] for row in (await conn.execute(text("PRAGMA table_info(project_groups)"))).all()}
    await engine.dispose()
