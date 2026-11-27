"""idempotency_keys.claim_token (GH #246): which request owns an in-progress claim, so a request whose stale claim was taken
over can no longer release, complete or mark partial the claim the retry now holds."""
from sqlalchemy import text

version = 84
name = "idempotency_claim_token"


async def up(conn):
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(idempotency_keys)"))).fetchall()}
    if "claim_token" not in cols:
        await conn.execute(text("ALTER TABLE idempotency_keys ADD COLUMN claim_token TEXT"))


async def down(conn):
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(idempotency_keys)"))).fetchall()}
    if "claim_token" in cols:
        await conn.execute(text("ALTER TABLE idempotency_keys DROP COLUMN claim_token"))
