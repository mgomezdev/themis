"""Retire Orders for customer work (BIZ-186): every customer order becomes a customer-account-reportable project.

Customer orders carried their own customer name, amount paid and payment status, none of which reached a customer's
page or financial summary. Each customer order that has no linked project yet becomes a project (same customer,
amount, payment status, due date, hold) with its jobs re-pointed at it. The order row stays — it is the internal
grouping of those jobs, as projects already use it — and nothing is deleted.

* `projects.converted_from_order_id` records the provenance (and is what `down` keys on).
* `amount_paid` becomes one opening payment, dated the order's creation day, mirroring v024.
* The customer account is linked only when exactly one account has that name (case-insensitive); otherwise the
  free-text `customer` is kept and staff can link it.
* An order's `parts` have no file to build project items from, so they are kept as text in the project's notes.
"""
from __future__ import annotations
import json
from sqlalchemy import text

version = 32
name = "orders_to_projects"

OPENING_NOTE = "Opening balance (migrated from order)"


def _parts_text(raw) -> str:
    try:
        parts = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except ValueError:
        return ""
    lines = []
    for p in parts if isinstance(parts, list) else []:
        if isinstance(p, dict) and p.get("name"):
            extra = ", ".join(str(x) for x in (p.get("material"), f"x{p.get('qty', 1)}") if x)
            lines.append(f"- {p['name']} ({extra})")
    return "Parts from the original order:\n" + "\n".join(lines) if lines else ""


async def up(conn) -> None:
    cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(projects)"))).fetchall()}
    if "converted_from_order_id" not in cols:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN converted_from_order_id INTEGER"))

    orders = (await conn.execute(text("""
        SELECT id, customer, title, due_date, notes, on_hold, parts, created_at, updated_at, amount_paid, payment_status
        FROM orders
        WHERE order_type = 'customer'
          AND NOT EXISTS (SELECT 1 FROM projects WHERE projects.order_id = orders.id
                                                     OR projects.converted_from_order_id = orders.id)
        ORDER BY id
    """))).mappings().all()
    for o in orders:
        matches = (await conn.execute(
            text("SELECT id FROM customers WHERE lower(name) = lower(:n)"), {"n": (o["customer"] or "").strip()}
        )).fetchall()
        notes = "\n\n".join(x for x in (o["notes"] or "", _parts_text(o["parts"])) if x) or None
        paid = o["amount_paid"] if (o["amount_paid"] or 0) > 0 else None
        result = await conn.execute(text("""
            INSERT INTO projects (name, customer, order_type, on_hold, due_date, notes, order_id, created_at, updated_at,
                                  amount_paid, payment_status, stage, customer_id, converted_from_order_id)
            VALUES (:name, :customer, 'customer', :on_hold, :due, :notes, :oid, :created, :updated,
                    :paid, :status, 'queued', :cid, :oid)
        """), {
            "name": o["title"], "customer": o["customer"], "on_hold": bool(o["on_hold"]), "due": o["due_date"],
            "notes": notes, "oid": o["id"], "created": o["created_at"], "updated": o["updated_at"],
            "paid": paid, "status": o["payment_status"] or "unpaid",
            "cid": matches[0][0] if len(matches) == 1 else None,
        })
        project_id = result.lastrowid
        await conn.execute(text("UPDATE jobs SET project_id = :p WHERE order_id = :o AND project_id IS NULL"),
                           {"p": project_id, "o": o["id"]})
        if paid:
            await conn.execute(text("""
                INSERT INTO project_payments (project_id, amount, received_on, method, note, created_at)
                VALUES (:p, :amt, COALESCE(NULLIF(substr(:created, 1, 10), ''), date('now')), 'other', :note, :created)
            """), {"p": project_id, "amt": paid, "created": o["created_at"] or "", "note": OPENING_NOTE})


async def down(conn) -> None:
    ids = [r[0] for r in (await conn.execute(text(
        "SELECT id FROM projects WHERE converted_from_order_id IS NOT NULL"))).fetchall()]
    for pid in ids:
        await conn.execute(text("UPDATE jobs SET project_id = NULL WHERE project_id = :p"), {"p": pid})
        await conn.execute(text("DELETE FROM project_payments WHERE project_id = :p"), {"p": pid})
        await conn.execute(text("DELETE FROM projects WHERE id = :p"), {"p": pid})
    await conn.execute(text("ALTER TABLE projects DROP COLUMN converted_from_order_id"))
