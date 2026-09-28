"""Customer accounts, customer portal scoping, project stages, local-network admin."""
from datetime import datetime, timezone
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.auth import is_local
from app.main import app
from app.models import Job


async def _customer(client: AsyncClient, email="a@example.com", password="pw1") -> dict:
    r = await client.post("/api/v1/customers", json={"name": "A", "email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


async def _login(client: AsyncClient, email="a@example.com", password="pw1") -> dict:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"X-Api-Key": r.json()["key"]}


def test_is_local_default_and_env(monkeypatch):
    monkeypatch.delenv("THEMIS_LOCAL_NETWORKS", raising=False)
    assert is_local("192.168.1.20")
    assert not is_local("10.0.0.5")
    assert not is_local("127.0.0.1")
    assert not is_local(None)
    assert not is_local("not-an-ip")
    monkeypatch.setenv("THEMIS_LOCAL_NETWORKS", "10.0.0.0/8, 100.64.0.0/10")
    assert is_local("100.100.1.1")
    assert not is_local("192.168.1.20")


async def test_local_client_is_admin_without_key(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("THEMIS_LOCAL_NETWORKS", "127.0.0.0/8")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/v1/projects")).status_code == 200
        me = (await anon.get("/api/v1/auth/me")).json()
    assert me == {"local": True, "role": "admin", "customer": None}


async def test_remote_client_without_key_is_rejected(client: AsyncClient):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/v1/projects")).status_code == 401
        assert (await anon.get("/api/v1/auth/me")).json()["role"] is None


async def test_login_rejects_bad_password_and_disabled(client: AsyncClient):
    c = await _customer(client)
    r = await client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "nope"})
    assert r.status_code == 401
    await client.patch(f"/api/v1/customers/{c['id']}", json={"enabled": False})
    r = await client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "pw1"})
    assert r.status_code == 401


async def test_disable_revokes_existing_session(client: AsyncClient):
    c = await _customer(client)
    h = await _login(client)
    assert (await client.get("/api/v1/customer/projects", headers=h)).status_code == 200
    await client.patch(f"/api/v1/customers/{c['id']}", json={"enabled": False})
    assert (await client.get("/api/v1/customer/projects", headers=h)).status_code == 401


async def test_customer_session_cannot_use_staff_routes_and_is_hidden_from_key_list(client: AsyncClient):
    await _customer(client)
    h = await _login(client)
    assert (await client.get("/api/v1/projects", headers=h)).status_code == 403
    assert (await client.post("/api/v1/jobs", headers=h, json={})).status_code == 403
    me = (await client.get("/api/v1/auth/me", headers=h)).json()
    assert me["role"] == "customer" and me["customer"]["email"] == "a@example.com"
    names = [k["name"] for k in (await client.get("/api/v1/api-keys")).json()]
    assert not any(n.startswith("Customer session") for n in names)


async def test_staff_key_cannot_use_customer_portal(client: AsyncClient):
    assert (await client.get("/api/v1/customer/projects")).status_code == 403


async def test_customer_sees_only_own_projects_and_jobs(client: AsyncClient):
    a = await _customer(client, "a@example.com")
    b = await _customer(client, "b@example.com")
    pa = (await client.post("/api/v1/projects", json={"name": "A-proj", "customer_id": a["id"]})).json()
    pb = (await client.post("/api/v1/projects", json={"name": "B-proj", "customer_id": b["id"]})).json()
    await client.post("/api/v1/projects", json={"name": "unassigned"})

    h = await _login(client, "a@example.com")
    listed = (await client.get("/api/v1/customer/projects", headers=h)).json()
    assert [p["name"] for p in listed] == ["A-proj"]
    assert (await client.get(f"/api/v1/customer/projects/{pb['id']}", headers=h)).status_code == 404
    assert (await client.get(f"/api/v1/customer/projects/{pa['id']}", headers=h)).json()["jobs"] == []


async def test_customer_draft_lifecycle(client: AsyncClient):
    await _customer(client)
    h = await _login(client)
    d = (await client.post("/api/v1/customer/projects", headers=h,
                           json={"name": "Widget", "notes": "10 please"})).json()
    assert d["stage"] == "draft"
    r = await client.patch(f"/api/v1/customer/projects/{d['id']}", headers=h, json={"notes": "12 please"})
    assert r.json()["notes"] == "12 please"

    # Staff sees it as a draft assigned to the customer; generate is refused in draft.
    staff_view = (await client.get(f"/api/v1/projects/{d['id']}")).json()
    assert staff_view["stage"] == "draft" and staff_view["customer_id"] is not None
    gen = await client.post(f"/api/v1/projects/{d['id']}/generate", json={})
    assert gen.status_code == 409

    # Forward-only promotion.
    assert (await client.post(f"/api/v1/projects/{d['id']}/promote", json={"stage": "queued"})).status_code == 409
    assert (await client.post(f"/api/v1/projects/{d['id']}/promote", json={"stage": "planning"})).json()["stage"] == "planning"
    assert (await client.post(f"/api/v1/projects/{d['id']}/promote", json={"stage": "draft"})).status_code == 409
    with patch("app.api.routes.projects.queue_engine") as mock_qe:
        r = await client.post(f"/api/v1/projects/{d['id']}/promote", json={"stage": "queued"})
    assert r.json()["stage"] == "queued"
    mock_qe.wake.assert_called_once()

    # No longer editable by the customer once promoted.
    r = await client.patch(f"/api/v1/customer/projects/{d['id']}", headers=h, json={"notes": "x"})
    assert r.status_code == 409


async def test_customer_upload_to_draft_adds_item(client: AsyncClient, tmp_path, monkeypatch):
    monkeypatch.setenv("THEMIS_DATA_DIR", str(tmp_path))
    await _customer(client)
    h = await _login(client)
    d = (await client.post("/api/v1/customer/projects", headers=h, json={"name": "Widget"})).json()
    r = await client.post(f"/api/v1/customer/projects/{d['id']}/files", headers=h,
                          files={"file": ("part.stl", b"solid x\nendsolid x\n", "application/octet-stream")})
    assert r.status_code == 201, r.text
    assert [i["filename"] for i in r.json()["items"]] == ["part.stl"]


async def test_staff_projects_default_to_queued(client: AsyncClient):
    p = (await client.post("/api/v1/projects", json={"name": "Internal"})).json()
    assert p["stage"] == "queued" and p["customer_id"] is None


async def test_customer_job_view_is_narrow(client: AsyncClient):
    from app.api.routes.customer_portal import _job_dict
    now = datetime.now(timezone.utc).isoformat()
    j = Job(id=1, uploaded_file_id=1, plate_number=1, status="printing", created_at=now,
            updated_at=now, filament_cost=3.5, block_reason="x", assigned_printer_id=2)
    d = _job_dict(j)
    assert "filament_cost" not in d and "block_reason" not in d and "assigned_printer_id" not in d


async def test_migration_grants_customer_scopes_to_admin_keys(tmp_path):
    import json
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.migrations import v021_customer_accounts as m

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE projects (id INTEGER PRIMARY KEY)"))
        await conn.execute(text("CREATE TABLE api_keys (id INTEGER PRIMARY KEY, scopes TEXT)"))
        await conn.execute(text("INSERT INTO api_keys VALUES (1, :a), (2, :b)"),
                           [{"a": json.dumps(["apikeys:write"]), "b": json.dumps(["jobs:read"])}][0])
        await m.up(conn)
        rows = dict((await conn.execute(text("SELECT id, scopes FROM api_keys"))).fetchall())
    await engine.dispose()
    assert json.loads(rows[1]) == ["apikeys:write", "customers:read", "customers:write"]
    assert json.loads(rows[2]) == ["jobs:read"]
