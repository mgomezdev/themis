"""End-to-end API flows for customer accounts, through the real app, DB and auth stack.

"Admin" here is a local-network client (THEMIS_LOCAL_NETWORKS) with no key at all — the
deployment's actual admin path. Customers present their login session key."""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_session
from app.main import app
from app.models import Job, UploadedFile


@pytest.fixture
async def admin(client: AsyncClient, monkeypatch):
    """Keyless client on the local network (ASGITransport's peer is 127.0.0.1)."""
    monkeypatch.setenv("THEMIS_LOCAL_NETWORKS", "127.0.0.0/8")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _create_customer(admin: AsyncClient, name: str, email: str, password: str) -> dict:
    r = await admin.post("/api/v1/customers", json={"name": name, "email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


async def _login(email: str, password: str) -> AsyncClient:
    async with _anon() as anon:
        r = await anon.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"X-Api-Key": r.json()["key"]})


def _anon() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_job(project_id: int | None) -> int:
    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    now = datetime.now(timezone.utc).isoformat()
    f = UploadedFile(original_filename="m.3mf", stored_path="/x/m.3mf", plates=[], uploaded_at=now)
    session.add(f)
    await session.flush()
    j = Job(uploaded_file_id=f.id, plate_number=1, status="queued", queue_position=1.0,
            project_id=project_id, created_at=now, updated_at=now)
    session.add(j)
    await session.commit()
    await agen.aclose()
    return j.id


async def test_admin_creates_customers_who_log_in_and_see_only_their_projects_and_jobs(admin: AsyncClient):
    # Admin creates two customer accounts.
    alice = await _create_customer(admin, "Alice", "alice@example.com", "alice-pw")
    bob = await _create_customer(admin, "Bob", "bob@example.com", "bob-pw")
    assert {c["email"] for c in (await admin.get("/api/v1/customers")).json()} == {
        "alice@example.com", "bob@example.com"}

    # Admin creates projects for each customer, plus an internal one.
    async def project(name: str, customer_id: int | None) -> dict:
        r = await admin.post("/api/v1/projects", json={"name": name, "customer_id": customer_id})
        assert r.status_code == 201, r.text
        return r.json()

    a1 = await project("Alice Widgets", alice["id"])
    a2 = await project("Alice Brackets", alice["id"])
    b1 = await project("Bob Hinges", bob["id"])
    internal = await project("Internal Jig", None)
    alice_job = await _seed_job(a1["id"])
    bob_job = await _seed_job(b1["id"])
    internal_job = await _seed_job(internal["id"])

    # Alice logs in and sees only her projects and only their jobs.
    async with await _login("alice@example.com", "alice-pw") as ac:
        me = (await ac.get("/api/v1/auth/me")).json()
        assert me["role"] == "customer" and me["customer"]["id"] == alice["id"]

        listed = (await ac.get("/api/v1/customer/projects")).json()
        assert {p["name"] for p in listed} == {"Alice Widgets", "Alice Brackets"}
        job_ids = {j["id"] for p in listed for j in p["jobs"]}
        assert job_ids == {alice_job}
        assert bob_job not in job_ids and internal_job not in job_ids

        assert (await ac.get(f"/api/v1/customer/projects/{a2['id']}")).status_code == 200
        for other in (b1, internal):
            assert (await ac.get(f"/api/v1/customer/projects/{other['id']}")).status_code == 404
        # The staff project/job APIs stay closed to her.
        assert (await ac.get("/api/v1/projects")).status_code == 403
        assert (await ac.get(f"/api/v1/projects/{b1['id']}")).status_code == 403
        assert (await ac.get("/api/v1/jobs")).status_code == 403

    # Bob sees only his.
    async with await _login("bob@example.com", "bob-pw") as bc:
        listed = (await bc.get("/api/v1/customer/projects")).json()
        assert [p["name"] for p in listed] == ["Bob Hinges"]
        assert [j["id"] for j in listed[0]["jobs"]] == [bob_job]


async def test_admin_assigning_and_unassigning_a_project_controls_customer_visibility(admin: AsyncClient):
    alice = await _create_customer(admin, "Alice", "alice@example.com", "pw")
    p = (await admin.post("/api/v1/projects", json={"name": "Loose"})).json()

    async with await _login("alice@example.com", "pw") as ac:
        assert (await ac.get("/api/v1/customer/projects")).json() == []

        r = await admin.patch(f"/api/v1/projects/{p['id']}", json={"customer_id": alice["id"]})
        assert r.status_code == 200 and r.json()["customer_id"] == alice["id"]
        assert [x["id"] for x in (await ac.get("/api/v1/customer/projects")).json()] == [p["id"]]

        await admin.patch(f"/api/v1/projects/{p['id']}", json={"customer_id": None})
        assert (await ac.get("/api/v1/customer/projects")).json() == []


async def test_admin_promotes_customer_draft_and_customer_sees_each_stage(admin: AsyncClient):
    alice = await _create_customer(admin, "Alice", "alice@example.com", "pw")
    async with await _login("alice@example.com", "pw") as ac:
        draft = (await ac.post("/api/v1/customer/projects",
                               json={"name": "Custom Part", "notes": "need 5"})).json()
        assert draft["stage"] == "draft"

        async def customer_stage() -> str:
            return (await ac.get(f"/api/v1/customer/projects/{draft['id']}")).json()["stage"]

        # Admin sees the request, owned by Alice, as a draft; jobs can't be made yet.
        staff_view = next(p for p in (await admin.get("/api/v1/projects")).json() if p["id"] == draft["id"])
        assert staff_view["stage"] == "draft" and staff_view["customer_id"] == alice["id"]
        assert (await admin.post(f"/api/v1/projects/{draft['id']}/generate", json={})).status_code == 409

        # draft → planning
        r = await admin.post(f"/api/v1/projects/{draft['id']}/promote", json={"stage": "planning"})
        assert r.status_code == 200 and r.json()["stage"] == "planning"
        assert await customer_stage() == "planning"

        # planning → queued (wakes the queue engine so jobs get picked up)
        with patch("app.api.routes.projects.queue_engine") as qe:
            r = await admin.post(f"/api/v1/projects/{draft['id']}/promote", json={"stage": "queued"})
        assert r.status_code == 200 and r.json()["stage"] == "queued"
        qe.wake.assert_called_once()
        assert await customer_stage() == "queued"

        # No going back.
        r = await admin.post(f"/api/v1/projects/{draft['id']}/promote", json={"stage": "planning"})
        assert r.status_code == 409


async def test_customer_cannot_promote_generate_reassign_or_manage_accounts(admin: AsyncClient):
    await _create_customer(admin, "Alice", "alice@example.com", "pw")
    async with await _login("alice@example.com", "pw") as ac:
        own = (await ac.post("/api/v1/customer/projects", json={"name": "Mine"})).json()
        pid = own["id"]
        assert (await ac.post(f"/api/v1/projects/{pid}/promote", json={"stage": "planning"})).status_code == 403
        assert (await ac.post(f"/api/v1/projects/{pid}/generate", json={})).status_code == 403
        assert (await ac.patch(f"/api/v1/projects/{pid}", json={"customer_id": None})).status_code == 403
        assert (await ac.post("/api/v1/jobs", json={})).status_code == 403
        assert (await ac.get("/api/v1/customers")).status_code == 403
        assert (await ac.post("/api/v1/customers",
                              json={"name": "X", "email": "x@example.com", "password": "x"})).status_code == 403
        # Still a draft — nothing the customer tried moved it.
        assert (await ac.get(f"/api/v1/customer/projects/{pid}")).json()["stage"] == "draft"


async def test_login_only_works_for_accounts_the_admin_created(admin: AsyncClient):
    async with _anon() as anon:
        r = await anon.post("/api/v1/auth/login", json={"email": "new@example.com", "password": "pw"})
        assert r.status_code == 401
        await _create_customer(admin, "New", "New@Example.com", "pw")
        # Email match is case-insensitive.
        r = await anon.post("/api/v1/auth/login", json={"email": "NEW@example.com", "password": "pw"})
        assert r.status_code == 200 and r.json()["customer"]["email"] == "new@example.com"
