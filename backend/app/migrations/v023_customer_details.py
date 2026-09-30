"""Customer contact details and a quoted project price (for outstanding-balance reporting)."""
from __future__ import annotations
from sqlalchemy import text

version = 23
name = "customer_details"


async def _cols(conn, table: str) -> set[str]:
    return {row[1] for row in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def up(conn) -> None:
    customer_cols = await _cols(conn, "customers")
    for col in ("phone", "company"):
        if col not in customer_cols:
            await conn.execute(text(f"ALTER TABLE customers ADD COLUMN {col} VARCHAR(255)"))
    if "notes" not in customer_cols:
        await conn.execute(text("ALTER TABLE customers ADD COLUMN notes TEXT"))

    if "price" not in await _cols(conn, "projects"):
        await conn.execute(text("ALTER TABLE projects ADD COLUMN price REAL"))


async def down(conn) -> None:
    await conn.execute(text("ALTER TABLE projects DROP COLUMN price"))
    await conn.execute(text("ALTER TABLE customers DROP COLUMN notes"))
    await conn.execute(text("ALTER TABLE customers DROP COLUMN company"))
    await conn.execute(text("ALTER TABLE customers DROP COLUMN phone"))
