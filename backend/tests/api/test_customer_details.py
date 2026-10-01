"""Customer contact details, project price, and the customer detail/financial summary."""
from datetime import datetime, timedelta, timezone

from httpx import AsyncClient

from app.api.routes.customers import _financials
from app.database import get_session
from app.main import app
from app.models import Job, Project, UploadedFile


async def _db_session():
    gen = app.dependency_overrides[get_session]()
    return gen, await gen.__anext__()


async def _add_job(project_id: int, status: str, cost: float | None) -> None:
    gen, session = await _db_session()
    now = datetime.now(timezone.utc).isoformat()
    uploaded = UploadedFile(original_filename="m.3mf", stored_path="/x/m.3mf", plates=[], uploaded_at=now)
    session.add(uploaded)
    await session.flush()                                   # jobs.uploaded_file_id is a real FK (the test DB enforces it)
    session.add(Job(uploaded_file_id=uploaded.id, plate_number=1, status=status, created_at=now, updated_at=now,
                    project_id=project_id, filament_cost=cost))
    await session.commit()
    await gen.aclose()


async def test_create_without_password_and_with_contact_details(client: AsyncClient):
    r = await client.post("/api/v1/customers", json={
        "name": "Acme", "email": "ops@acme.test", "phone": " 555-1234 ", "company": "Acme Inc", "notes": "net 30"})
    assert r.status_code == 201, r.text
    c = r.json()
    assert (c["phone"], c["company"], c["notes"]) == ("555-1234", "Acme Inc", "net 30")
    # No password set → can't sign in with an empty one.
    r = await client.post("/api/v1/auth/login", json={"email": "ops@acme.test", "password": ""})
    assert r.status_code in (401, 422)


async def test_patch_contact_details_and_clear(client: AsyncClient):
    c = (await client.post("/api/v1/customers", json={"name": "A", "email": "a@x.test", "phone": "1"})).json()
    r = await client.patch(f"/api/v1/customers/{c['id']}", json={"company": "Co", "phone": ""})
    assert r.status_code == 200
    assert r.json()["company"] == "Co" and r.json()["phone"] is None
    # Unsent fields are left alone.
    r = await client.patch(f"/api/v1/customers/{c['id']}", json={"name": "B"})
    assert r.json()["company"] == "Co"


async def test_project_price_roundtrip_and_customer_name(client: AsyncClient):
    c = (await client.post("/api/v1/customers", json={"name": "Acme", "email": "a@x.test"})).json()
    p = (await client.post("/api/v1/projects", json={"name": "P", "price": 100, "customer_id": c["id"]})).json()
    assert p["price"] == 100 and p["customer_name"] == "Acme"
    p = (await client.patch(f"/api/v1/projects/{p['id']}", json={"amount_paid": 40})).json()
    assert p["price"] == 100  # untouched when not sent
    p = (await client.patch(f"/api/v1/projects/{p['id']}", json={"price": None, "customer_id": None})).json()
    assert p["price"] is None and p["customer_name"] is None


async def test_get_customer_detail_projects_and_financials(client: AsyncClient):
    c = (await client.post("/api/v1/customers", json={"name": "Acme", "email": "a@x.test"})).json()
    other = (await client.post("/api/v1/customers", json={"name": "Other", "email": "o@x.test"})).json()
    done = (await client.post("/api/v1/projects", json={
        "name": "Done", "customer_id": c["id"], "price": 100, "amount_paid": 100, "payment_status": "paid"})).json()
    active = (await client.post("/api/v1/projects", json={
        "name": "Active", "customer_id": c["id"], "price": 50, "amount_paid": 20, "payment_status": "partial"})).json()
    await client.post("/api/v1/projects", json={"name": "Unpriced", "customer_id": c["id"]})
    await client.post("/api/v1/projects", json={"name": "NotMine", "customer_id": other["id"], "price": 999})
    await _add_job(done["id"], "complete", 12.5)
    await _add_job(active["id"], "complete", 3.0)
    await _add_job(active["id"], "queued", None)

    r = await client.get(f"/api/v1/customers/{c['id']}")
    assert r.status_code == 200, r.text
    d = r.json()
    by_name = {p["name"]: p for p in d["projects"]}
    assert set(by_name) == {"Done", "Active", "Unpriced"}
    assert by_name["Done"]["status"] == "completed"
    assert by_name["Active"]["status"] == "active" and by_name["Active"]["outstanding"] == 30
    assert by_name["Unpriced"]["status"] == "pending"
    fin = d["financials"]
    assert fin["unpriced_unpaid"] == 1
    for key in ("30d", "60d", "90d", "all"):
        assert fin["windows"][key] == {"project_count": 3, "revenue": 120, "expenses": 15.5, "profit": 104.5,
                                       "billed": 150, "outstanding": 30,
                                       "expense_breakdown": {"filament": 15.5, "machine": 0, "labour": 0, "parts": 0}}

    lst = {x["id"]: x for x in (await client.get("/api/v1/customers")).json()}
    assert lst[c["id"]]["project_count"] == 3
    assert lst[c["id"]]["active_project_count"] == 2
    assert lst[c["id"]]["outstanding"] == 30
    assert lst[other["id"]]["outstanding"] == 999


async def test_get_customer_404(client: AsyncClient):
    assert (await client.get("/api/v1/customers/999")).status_code == 404


def test_paid_without_amount_counts_price_as_revenue():
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    p = Project(id=1, name="x", created_at="2026-08-30T00:00:00Z", price=80, amount_paid=None, payment_status="paid")
    w = _financials([p], {}, now)["windows"]["all"]
    assert (w["revenue"], w["outstanding"]) == (80, 0)


def test_financial_windows_bucket_by_project_created_at():
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)

    def proj(pid: int, days_ago: int, paid: float) -> Project:
        return Project(id=pid, name=str(pid), created_at=(now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       amount_paid=paid, price=paid + 10, payment_status="partial")

    projects = [proj(1, 10, 1), proj(2, 45, 2), proj(3, 75, 4), proj(4, 200, 8)]
    jobs = {3: [Job(filament_cost=1.0)]}
    w = _financials(projects, jobs, now)["windows"]
    assert [w[k]["revenue"] for k in ("30d", "60d", "90d", "all")] == [1, 3, 7, 15]
    assert [w[k]["expenses"] for k in ("30d", "60d", "90d", "all")] == [0, 0, 1, 1]
    assert [w[k]["outstanding"] for k in ("30d", "60d", "90d", "all")] == [10, 20, 30, 40]


async def test_has_password_and_no_password_login_rejected(client: AsyncClient):
    c = (await client.post("/api/v1/customers", json={"name": "A", "email": "a@x.test"})).json()
    assert c["has_password"] is False
    for pw in ("", "anything"):
        r = await client.post("/api/v1/auth/login", json={"email": "a@x.test", "password": pw})
        assert r.status_code in (401, 422)
    r = await client.patch(f"/api/v1/customers/{c['id']}", json={"password": "pw12345"})
    assert r.json()["has_password"] is True
    r = await client.post("/api/v1/auth/login", json={"email": "a@x.test", "password": "pw12345"})
    assert r.status_code == 200


async def test_delete_customer_keeps_projects_unlinked_with_label(client: AsyncClient):
    c = (await client.post("/api/v1/customers", json={"name": "Acme", "email": "a@x.test", "password": "pw"})).json()
    p = (await client.post("/api/v1/projects", json={"name": "P", "customer_id": c["id"]})).json()
    headers = {"X-Api-Key": (await client.post("/api/v1/auth/login",
                                               json={"email": "a@x.test", "password": "pw"})).json()["key"]}
    r = await client.delete(f"/api/v1/customers/{c['id']}")
    assert r.status_code == 200 and r.json()["projects_unlinked"] == 1
    assert (await client.get(f"/api/v1/customers/{c['id']}")).status_code == 404
    proj = (await client.get(f"/api/v1/projects/{p['id']}")).json()
    assert proj["customer_id"] is None and proj["customer"] == "Acme"
    # Their portal session is gone too.
    assert (await client.get("/api/v1/customer/projects", headers=headers)).status_code == 401
    assert (await client.delete(f"/api/v1/customers/{c['id']}")).status_code == 404


async def test_unlinked_projects_suggestions_and_link(client: AsyncClient):
    acme = (await client.post("/api/v1/customers", json={"name": "Acme Corp", "email": "ops@acme.test",
                                                         "company": "Acme Inc"})).json()
    await client.post("/api/v1/customers", json={"name": "Dup", "email": "d1@x.test"})
    await client.post("/api/v1/customers", json={"name": "dup", "email": "d2@x.test"})
    by_name = (await client.post("/api/v1/projects", json={"name": "P1", "customer": "  acme   corp "})).json()
    by_company = (await client.post("/api/v1/projects", json={"name": "P2", "customer": "ACME INC"})).json()
    ambiguous = (await client.post("/api/v1/projects", json={"name": "P3", "customer": "Dup"})).json()
    unknown = (await client.post("/api/v1/projects", json={"name": "P4", "customer": "Stranger"})).json()
    await client.post("/api/v1/projects", json={"name": "Internal"})
    await client.post("/api/v1/projects", json={"name": "Linked", "customer": "Acme Corp", "customer_id": acme["id"]})

    rows = {r["project_id"]: r for r in (await client.get("/api/v1/customers/unlinked-projects")).json()}
    assert set(rows) == {by_name["id"], by_company["id"], ambiguous["id"], unknown["id"]}
    assert rows[by_name["id"]]["suggested_customer_id"] == acme["id"]
    assert rows[by_company["id"]]["suggested_customer_id"] == acme["id"]
    assert rows[ambiguous["id"]]["suggested_customer_id"] is None
    assert rows[unknown["id"]]["suggested_customer_id"] is None

    r = await client.post("/api/v1/customers/link-projects", json={"links": [
        {"project_id": by_name["id"], "customer_id": acme["id"]},
        {"project_id": by_company["id"], "customer_id": acme["id"]}]})
    assert r.status_code == 200 and r.json() == {"linked": 2}
    p = (await client.get(f"/api/v1/projects/{by_name['id']}")).json()
    assert p["customer_id"] == acme["id"] and p["order_type"] == "customer"
    assert {r["project_id"] for r in (await client.get("/api/v1/customers/unlinked-projects")).json()} \
        == {ambiguous["id"], unknown["id"]}

    r = await client.post("/api/v1/customers/link-projects",
                          json={"links": [{"project_id": unknown["id"], "customer_id": 999}]})
    assert r.status_code == 404
    # Never silently moves a project off another customer.
    other = (await client.post("/api/v1/customers", json={"name": "Other", "email": "o@x.test"})).json()
    r = await client.post("/api/v1/customers/link-projects",
                          json={"links": [{"project_id": by_name["id"], "customer_id": other["id"]}]})
    assert r.status_code == 409
