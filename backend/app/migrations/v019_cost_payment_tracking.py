"""Add filament cost tracking to jobs and payment tracking to orders/projects,
so future profit/loss reporting can compare cost against what a customer paid."""
from __future__ import annotations
from sqlalchemy import text

version = 19
name = "cost_payment_tracking"


async def up(conn) -> None:
    jobs_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    if "filament_cost" not in jobs_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN filament_cost REAL"))

    orders_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(orders)"))).fetchall()}
    if "amount_paid" not in orders_cols:
        await conn.execute(text("ALTER TABLE orders ADD COLUMN amount_paid REAL"))
    if "payment_status" not in orders_cols:
        await conn.execute(text(
            "ALTER TABLE orders ADD COLUMN payment_status VARCHAR(20) NOT NULL DEFAULT 'unpaid'"
        ))

    projects_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(projects)"))).fetchall()}
    if "amount_paid" not in projects_cols:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN amount_paid REAL"))
    if "payment_status" not in projects_cols:
        await conn.execute(text(
            "ALTER TABLE projects ADD COLUMN payment_status VARCHAR(20) NOT NULL DEFAULT 'unpaid'"
        ))


async def down(conn) -> None:
    await conn.execute(text("ALTER TABLE projects DROP COLUMN payment_status"))
    await conn.execute(text("ALTER TABLE projects DROP COLUMN amount_paid"))
    await conn.execute(text("ALTER TABLE orders DROP COLUMN payment_status"))
    await conn.execute(text("ALTER TABLE orders DROP COLUMN amount_paid"))
    await conn.execute(text("ALTER TABLE jobs DROP COLUMN filament_cost"))
