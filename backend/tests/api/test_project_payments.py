"""BIZ-183: individual dated payments per project; amount paid / status derived from them; customer history
and cash-basis revenue."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.migrations import v024_project_payments
from app.models import Project, ProjectPayment
from app.services.payments import OPENING_NOTE

TODAY = datetime.now(timezone.utc).date()


def day(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


async def _project(client, price=100, **extra) -> dict:
    r = await client.post("/api/v1/projects", json={"name": "P", "price": price, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def _pay(client, pid, amount, received_on=None, **extra) -> dict:
    body = {"amount": amount, **({"received_on": received_on} if received_on else {}), **extra}
    r = await client.post(f"/api/v1/projects/{pid}/payments", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _get(client, pid) -> dict:
    return (await client.get(f"/api/v1/projects/{pid}")).json()


async def test_deposit_then_final_payment_flip_status_and_keep_dated_history(client):
    p = await _project(client, price=100)

    await _pay(client, p["id"], 30, day(-10), method="card", note="deposit")
    mid = await _get(client, p["id"])
    assert (mid["amount_paid"], mid["payment_status"]) == (30.0, "partial")

    await _pay(client, p["id"], 70, day(-1), method="bank_transfer")
    done = await _get(client, p["id"])
    assert (done["amount_paid"], done["payment_status"]) == (100.0, "paid")

    rows = (await client.get(f"/api/v1/projects/{p['id']}/payments")).json()
    assert [(r["received_on"], r["amount"], r["method"], r["note"]) for r in rows] == [
        (day(-1), 70.0, "bank_transfer", None), (day(-10), 30.0, "card", "deposit")]  # newest first


async def test_payment_default_date_is_today_and_defaults_method_other(client):
    p = await _project(client)
    pay = await _pay(client, p["id"], 5)
    assert pay["received_on"] == TODAY.isoformat() and pay["method"] == "other"


async def test_overpayment_is_paid(client):
    p = await _project(client, price=50)
    await _pay(client, p["id"], 80)
    assert (await _get(client, p["id"]))["payment_status"] == "paid"


async def test_hand_entered_amount_is_kept_as_an_opening_payment_when_the_first_payment_is_added(client):
    p = await _project(client, price=100)
    await client.patch(f"/api/v1/projects/{p['id']}", json={"amount_paid": 25, "payment_status": "partial"})

    await _pay(client, p["id"], 10, day(-1))

    rows = (await client.get(f"/api/v1/projects/{p['id']}/payments")).json()
    assert sorted((r["amount"], r["method"]) for r in rows) == [(10.0, "other"), (25.0, "other")]
    assert [r["note"] for r in rows if r["amount"] == 25.0] == [OPENING_NOTE]
    assert (await _get(client, p["id"]))["amount_paid"] == 35.0


async def test_creating_a_project_with_an_amount_paid_records_it_as_a_payment(client):
    p = await _project(client, price=100, amount_paid=100, payment_status="unpaid")

    assert (p["amount_paid"], p["payment_status"]) == (100.0, "paid")   # derived, whatever status was sent
    rows = (await client.get(f"/api/v1/projects/{p['id']}/payments")).json()
    assert [(r["amount"], r["received_on"]) for r in rows] == [(100.0, TODAY.isoformat())]


async def test_no_price_means_received_money_reads_partial(client):
    p = await _project(client, price=None)
    await _pay(client, p["id"], 10)
    assert (await _get(client, p["id"]))["payment_status"] == "partial"


async def test_changing_the_price_rederives_the_status(client):
    p = await _project(client, price=100)
    await _pay(client, p["id"], 100)
    assert (await _get(client, p["id"]))["payment_status"] == "paid"

    after = (await client.patch(f"/api/v1/projects/{p['id']}", json={"price": 250})).json()
    assert (after["amount_paid"], after["payment_status"]) == (100.0, "partial")


async def test_editing_and_deleting_payments_rederive(client):
    p = await _project(client, price=100)
    a = await _pay(client, p["id"], 100)
    b = await _pay(client, p["id"], 20)

    r = await client.patch(f"/api/v1/projects/{p['id']}/payments/{b['id']}",
                           json={"amount": 5, "method": "cash", "received_on": day(-3), "note": "  tip  "})
    assert r.status_code == 200
    assert (r.json()["amount"], r.json()["method"], r.json()["received_on"], r.json()["note"]) == (5.0, "cash", day(-3), "tip")
    assert (await _get(client, p["id"]))["amount_paid"] == 105.0

    await client.delete(f"/api/v1/projects/{p['id']}/payments/{a['id']}")
    mid = await _get(client, p["id"])
    assert (mid["amount_paid"], mid["payment_status"]) == (5.0, "partial")

    await client.delete(f"/api/v1/projects/{p['id']}/payments/{b['id']}")
    last = await _get(client, p["id"])
    assert (last["amount_paid"], last["payment_status"]) == (None, "unpaid")


async def test_patch_note_can_be_cleared(client):
    p = await _project(client)
    pay = await _pay(client, p["id"], 5, note="x")
    r = await client.patch(f"/api/v1/projects/{p['id']}/payments/{pay['id']}", json={"note": ""})
    assert r.json()["note"] is None


async def test_manual_fields_are_rejected_once_payments_exist_but_work_before(client):
    p = await _project(client)
    # Legacy/API path with no payment rows: still editable by hand.
    ok = await client.patch(f"/api/v1/projects/{p['id']}", json={"amount_paid": 10, "payment_status": "partial"})
    assert ok.status_code == 200 and ok.json()["amount_paid"] == 10.0

    await _pay(client, p["id"], 40)
    for body in ({"amount_paid": 1}, {"payment_status": "paid"}):
        r = await client.patch(f"/api/v1/projects/{p['id']}", json=body)
        assert r.status_code == 409 and "payment" in r.json()["detail"]
    assert (await _get(client, p["id"]))["amount_paid"] == 50.0   # 10 adopted + 40; derived value untouched

    # Other fields are still editable.
    assert (await client.patch(f"/api/v1/projects/{p['id']}", json={"name": "Renamed"})).status_code == 200


@pytest.mark.parametrize("body", [
    {"amount": 0}, {"amount": -5}, {"amount": 5, "method": "bitcoin"}, {"amount": 5, "received_on": "not-a-date"},
    {"amount": 5, "received_on": day(30)},
])
async def test_invalid_payments_are_422_and_change_nothing(client, body):
    p = await _project(client)
    assert (await client.post(f"/api/v1/projects/{p['id']}/payments", json=body)).status_code == 422
    assert (await client.get(f"/api/v1/projects/{p['id']}/payments")).json() == []


async def test_404s_and_cross_project_payment_ids(client):
    a, b = await _project(client), await _project(client)
    pay = await _pay(client, a["id"], 5)

    assert (await client.get("/api/v1/projects/9999/payments")).status_code == 404
    assert (await client.post("/api/v1/projects/9999/payments", json={"amount": 5})).status_code == 404
    assert (await client.patch(f"/api/v1/projects/{b['id']}/payments/{pay['id']}", json={"amount": 1})).status_code == 404
    assert (await client.delete(f"/api/v1/projects/{b['id']}/payments/{pay['id']}")).status_code == 404
    assert (await _get(client, a["id"]))["amount_paid"] == 5.0


async def test_deleting_a_project_removes_its_payments(client, session_factory):
    p = await _project(client)
    await _pay(client, p["id"], 5)
    assert (await client.delete(f"/api/v1/projects/{p['id']}")).status_code in (200, 204)
    async with session_factory() as s:
        assert (await s.execute(select(ProjectPayment))).scalars().all() == []


# ---- customer history + revenue -------------------------------------------------------------

async def _customer(client, email="a@x.test") -> dict:
    return (await client.post("/api/v1/customers", json={"name": "Acme", "email": email})).json()


async def test_customer_payment_history_spans_projects_newest_first_with_project_name(client):
    c = await _customer(client)
    p1 = await _project(client, customer_id=c["id"], name="Bench")
    p2 = await _project(client, customer_id=c["id"], name="Shelf")
    other = await _project(client)  # someone else's
    await _pay(client, p1["id"], 10, day(-20))
    await _pay(client, p2["id"], 20, day(-2))
    await _pay(client, other["id"], 99, day(-1))

    rows = (await client.get(f"/api/v1/customers/{c['id']}/payments")).json()

    assert [(r["project_name"], r["amount"], r["received_on"]) for r in rows] == [
        ("Shelf", 20.0, day(-2)), ("Bench", 10.0, day(-20))]
    assert (await client.get("/api/v1/customers/9999/payments")).status_code == 404


async def test_revenue_follows_the_date_received_not_the_project_start(client, session_factory):
    c = await _customer(client)
    old = await _project(client, customer_id=c["id"], price=500)
    async with session_factory() as s:  # a project started 4 months ago...
        proj = await s.get(Project, old["id"])
        proj.created_at = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        await s.commit()
    await _pay(client, old["id"], 40, day(-100))   # deposit long ago
    await _pay(client, old["id"], 60, day(-5))     # ...paid last week

    w = (await client.get(f"/api/v1/customers/{c['id']}")).json()["financials"]["windows"]

    assert w["30d"]["revenue"] == 60.0     # only what was *received* in the window
    assert w["90d"]["revenue"] == 60.0
    assert w["all"]["revenue"] == 100.0
    assert w["30d"]["project_count"] == 0  # project_count/billed/expenses still follow the project's own date


async def test_projects_without_payment_rows_keep_legacy_revenue_dating(client, session_factory):
    c = await _customer(client)
    legacy = await _project(client, customer_id=c["id"], price=80)
    await client.patch(f"/api/v1/projects/{legacy['id']}", json={"payment_status": "paid"})   # "paid", no amount, no rows
    paid = await _project(client, customer_id=c["id"], price=50)
    await _pay(client, paid["id"], 10, day(-1))

    w = (await client.get(f"/api/v1/customers/{c['id']}")).json()["financials"]["windows"]["30d"]

    assert w["revenue"] == 90.0   # 80 (marked paid, counted at the project's date) + 10 (payment)


# ---- migration back-fill --------------------------------------------------------------------

async def test_migration_turns_existing_amount_paid_into_one_opening_payment(session_factory):
    async with session_factory() as s:
        for name, paid in (("paid some", 50.0), ("paid none", None), ("zero", 0.0)):
            s.add(Project(name=name, created_at="2026-03-05T10:00:00+00:00", updated_at="2026-04-01T00:00:00+00:00",
                          amount_paid=paid, payment_status="partial" if paid else "unpaid"))
        await s.commit()
        conn = await s.connection()
        await conn.execute(text("DROP TABLE project_payments"))
        await v024_project_payments.up(conn)
        await v024_project_payments.up(conn)   # idempotent: a second run adds nothing
        await s.commit()

    async with session_factory() as s:
        pays = (await s.execute(select(ProjectPayment))).scalars().all()
    assert [(p.amount, p.received_on, p.method, p.note) for p in pays] == [
        (50.0, "2026-03-05", "other", v024_project_payments.OPENING_NOTE)]
