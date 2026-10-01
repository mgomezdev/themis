"""BIZ-185: customers see their quote, payments and balance in the portal — only when staff make the price
visible, only their own, and never anything internal."""
import pytest
from httpx import AsyncClient


async def _customer(client: AsyncClient, email="a@example.com") -> dict:
    r = await client.post("/api/v1/customers", json={"name": "A", "email": email, "password": "pw1"})
    assert r.status_code == 201, r.text
    return r.json()


async def _login(client: AsyncClient, email="a@example.com") -> dict:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw1"})
    assert r.status_code == 200, r.text
    return {"X-Api-Key": r.json()["key"]}


async def _project(client, customer_id, price=100.0, visible=False, stage="planning", **extra) -> dict:
    r = await client.post("/api/v1/projects", json={
        "name": "Bench", "customer_id": customer_id, "price": price, "stage": stage, **extra})
    assert r.status_code == 201, r.text
    if visible:
        await client.patch(f"/api/v1/projects/{r.json()['id']}", json={"price_visible": True})
    return r.json()


async def _mine(client, headers, pid) -> dict:
    return (await client.get(f"/api/v1/customer/projects/{pid}", headers=headers)).json()


async def test_nothing_money_related_is_shown_until_staff_make_the_price_visible(client):
    c = await _customer(client)
    p = await _project(client, c["id"], price=250, amount_paid=50)
    h = await _login(client)

    before = await _mine(client, h, p["id"])
    assert before["quote"] is None
    assert not any(k in before for k in ("price", "amount_paid", "payment_status", "price_visible"))

    await client.patch(f"/api/v1/projects/{p['id']}", json={"price_visible": True})
    q = (await _mine(client, h, p["id"]))["quote"]
    assert (q["price"], q["paid"], q["balance"], q["accepted_at"]) == (250.0, 50.0, 200.0, None)

    await client.patch(f"/api/v1/projects/{p['id']}", json={"price_visible": False})   # staff can hide it again
    assert (await _mine(client, h, p["id"]))["quote"] is None


async def test_a_visible_flag_with_no_price_shows_nothing(client):
    c = await _customer(client)
    p = await _project(client, c["id"], price=None, visible=True)
    assert (await _mine(client, await _login(client), p["id"]))["quote"] is None


async def test_the_quote_lists_payments_newest_first_without_internal_notes(client):
    c = await _customer(client)
    p = await _project(client, c["id"], price=100, visible=True)
    await client.post(f"/api/v1/projects/{p['id']}/payments", json={"amount": 30, "received_on": "2026-09-01", "method": "card", "note": "internal: friend discount"})
    await client.post(f"/api/v1/projects/{p['id']}/payments", json={"amount": 20, "received_on": "2026-09-15", "method": "cash"})

    q = (await _mine(client, await _login(client), p["id"]))["quote"]

    assert (q["paid"], q["balance"]) == (50.0, 50.0)
    assert [(x["received_on"], x["amount"], x["method"]) for x in q["payments"]] == [
        ("2026-09-15", 20.0, "cash"), ("2026-09-01", 30.0, "card")]
    assert all(set(x) == {"id", "received_on", "amount", "method"} for x in q["payments"])   # no note, no project internals


async def test_balance_is_zero_once_paid_and_overpayment_is_not_negative(client):
    c = await _customer(client)
    p = await _project(client, c["id"], price=40, visible=True)
    await client.post(f"/api/v1/projects/{p['id']}/payments", json={"amount": 60})
    q = (await _mine(client, await _login(client), p["id"]))["quote"]
    assert (q["paid"], q["balance"]) == (60.0, 0.0)


async def test_internal_figures_never_reach_the_customer(client):
    c = await _customer(client)
    p = await _project(client, c["id"], price=100, visible=True)
    h = await _login(client)
    listed = (await client.get("/api/v1/customer/projects", headers=h)).json()[0]
    detail = await _mine(client, h, p["id"])

    forbidden = {"filament_cost_total", "actual_filament_grams", "actual_seconds", "estimate_filament_grams_total",
                 "amount_paid", "payment_status", "price", "price_visible", "customer", "source_app", "source_user",
                 "order_type", "customer_id", "customer_name", "profit", "expenses"}
    for d in (listed, detail):
        assert not forbidden & set(d)
        assert set(d["quote"]) == {"price", "paid", "balance", "accepted_at", "payments"}
        for job in d["jobs"]:
            assert "filament_cost" not in job


async def test_a_customer_cannot_see_or_accept_someone_elses_quote(client):
    a, b = await _customer(client, "a@example.com"), await _customer(client, "b@example.com")
    pb = await _project(client, b["id"], visible=True)
    ha = await _login(client, "a@example.com")

    assert (await client.get(f"/api/v1/customer/projects/{pb['id']}", headers=ha)).status_code == 404
    assert (await client.post(f"/api/v1/customer/projects/{pb['id']}/quote/accept", headers=ha)).status_code == 404
    assert (await client.get("/api/v1/customer/projects", headers=ha)).json() == []
    assert (await client.get(f"/api/v1/projects/{pb['id']}")).json()["quote_accepted_at"] is None   # nothing was recorded
    assert a["id"] != b["id"]


async def test_staff_keys_cannot_use_the_portal_accept_route(client):
    c = await _customer(client)
    p = await _project(client, c["id"], visible=True)
    assert (await client.post(f"/api/v1/customer/projects/{p['id']}/quote/accept")).status_code == 403


async def test_accepting_records_when_and_moves_a_draft_to_planning_and_is_idempotent(client):
    c = await _customer(client)
    p = await _project(client, c["id"], price=100, visible=True, stage="draft")
    h = await _login(client)

    r = await client.post(f"/api/v1/customer/projects/{p['id']}/quote/accept", headers=h)
    assert r.status_code == 200
    first = r.json()
    assert first["stage"] == "planning" and first["quote"]["accepted_at"]

    again = (await client.post(f"/api/v1/customer/projects/{p['id']}/quote/accept", headers=h)).json()
    assert again["quote"]["accepted_at"] == first["quote"]["accepted_at"]            # unchanged
    staff = (await client.get(f"/api/v1/projects/{p['id']}")).json()
    assert (staff["stage"], staff["quote_accepted_at"]) == ("planning", first["quote"]["accepted_at"])


async def test_accepting_does_not_move_a_project_that_is_already_further_along(client):
    c = await _customer(client)
    p = await _project(client, c["id"], visible=True, stage="queued")
    await client.post(f"/api/v1/customer/projects/{p['id']}/quote/accept", headers=await _login(client))
    assert (await client.get(f"/api/v1/projects/{p['id']}")).json()["stage"] == "queued"


@pytest.mark.parametrize("visible, price", [(False, 100), (True, None)])
async def test_there_is_nothing_to_accept_without_a_visible_priced_quote(client, visible, price):
    c = await _customer(client)
    p = await _project(client, c["id"], price=price, visible=visible, stage="draft")
    r = await client.post(f"/api/v1/customer/projects/{p['id']}/quote/accept", headers=await _login(client))
    assert r.status_code == 409
    assert (await client.get(f"/api/v1/projects/{p['id']}")).json()["quote_accepted_at"] is None


async def test_changing_the_price_after_acceptance_clears_the_acceptance(client):
    c = await _customer(client)
    p = await _project(client, c["id"], price=100, visible=True)
    h = await _login(client)
    await client.post(f"/api/v1/customer/projects/{p['id']}/quote/accept", headers=h)

    await client.patch(f"/api/v1/projects/{p['id']}", json={"price": 100})              # same price: still accepted
    assert (await _mine(client, h, p["id"]))["quote"]["accepted_at"]

    await client.patch(f"/api/v1/projects/{p['id']}", json={"price": 150})              # different price: must re-accept
    q = (await _mine(client, h, p["id"]))["quote"]
    assert (q["price"], q["accepted_at"]) == (150.0, None)


async def test_staff_see_the_flag_and_acceptance_on_the_project(client):
    c = await _customer(client)
    p = await _project(client, c["id"], visible=True)
    got = (await client.get(f"/api/v1/projects/{p['id']}")).json()
    assert (got["price_visible"], got["quote_accepted_at"]) == (True, None)


async def test_the_public_share_page_never_exposes_money_even_when_the_quote_is_visible(client):
    c = await _customer(client)
    p = await _project(client, c["id"], price=500, visible=True, amount_paid=200)
    token = (await client.put(f"/api/v1/projects/{p['id']}/share")).json()["token"]

    public = (await client.get(f"/api/v1/public/projects/{token}")).json()

    forbidden = {"price", "amount_paid", "payment_status", "price_visible", "quote", "quote_accepted_at",
                 "filament_cost_total", "customer_id"}
    assert not forbidden & set(public)
