"""Offline admin recovery CLI — needs only a shell on the host running Themis.

    docker compose exec themis python -m app.admin reset-password
    docker compose exec themis python -m app.admin allow-local-login

(run from the directory holding the compose file; `themis` is the compose *service* name — the
container itself is e.g. `themis-themis-1`, so plain `docker exec` needs `docker ps` to find it)

reset-password prints a new random admin password to this terminal (never to the log).
allow-local-login re-enables "local network devices are admin without signing in".
"""
from __future__ import annotations
import argparse
import asyncio
import secrets
import sys


async def _run(command: str) -> str:
    from .auth import get_admin_account
    from .database import SessionLocal, init_db
    from .services import admin_account as admin_svc

    await init_db()  # applies pending migrations; no-op on an up-to-date DB
    async with SessionLocal() as session:
        acct = await get_admin_account(session)
        if command == "reset-password":
            password = secrets.token_urlsafe(12)
            await admin_svc.set_password(session, acct, password)
            await session.commit()
            return (f"Admin password reset. Username: {acct.username}  Password: {password}\n"
                    "All admin sessions were signed out. Change it under Settings → Admin account.")
        acct.allow_local_login = True
        await session.commit()
        return "Local-network devices are admin without signing in again."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.admin", description="Themis admin recovery")
    parser.add_argument("command", choices=["reset-password", "allow-local-login"])
    args = parser.parse_args(argv)
    print(asyncio.run(_run(args.command)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
