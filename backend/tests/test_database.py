"""Coverage for the SQLite connect-time PRAGMAs in app.database.

Exercises _set_sqlite_pragmas directly against a throwaway in-memory engine rather
than the app's module-level `engine` (bound to the real data dir at import time) -
this keeps the test hermetic while still running the exact function that's registered
as the real engine's connect listener.
"""
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import _set_sqlite_pragmas


async def test_busy_timeout_pragma_set_on_connect():
    """Without a busy_timeout, a writer that finds the DB locked (the queue loop vs.
    a request handler committing concurrently) fails immediately with
    'database is locked' instead of waiting for the other transaction."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listens_for(engine.sync_engine, "connect")(_set_sqlite_pragmas)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA busy_timeout"))
            assert result.scalar() == 5000
    finally:
        await engine.dispose()


async def test_foreign_keys_pragma_still_set_on_connect():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listens_for(engine.sync_engine, "connect")(_set_sqlite_pragmas)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA foreign_keys"))
            assert result.scalar() == 1
    finally:
        await engine.dispose()
