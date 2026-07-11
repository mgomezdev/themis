"""CLI: python -m app.migrations.migrate up|down"""
import asyncio
import sys
from sqlalchemy import text
from ..database import engine
from .runner import run_migrations, rollback_last

async def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        if cmd == "up":
            await run_migrations(conn)
            print("Done.")
        elif cmd == "down":
            await rollback_last(conn)
        else:
            print("Usage: python -m app.migrations.migrate up|down", file=sys.stderr)
            sys.exit(1)

asyncio.run(main())
