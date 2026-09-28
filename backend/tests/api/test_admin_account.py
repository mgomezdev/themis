"""Admin account: first boot, local-login toggle, admin sign-in, offline recovery (log code + CLI)."""
import logging
import re

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.database import get_session
from app.main import app
from app.models import AdminAccount


def _keyless(peer: str = "127.0.0.1") -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(peer, 50000)), base_url="http://test")


@pytest.fixture
async def local(client: AsyncClient, monkeypatch):
    """Keyless client on the local network (ASGITransport's peer is 127.0.0.1)."""
    monkeypatch.setenv("THEMIS_LOCAL_NETWORKS", "127.0.0.0/8")
    async with _keyless() as c:
        yield c


@pytest.fixture
async def remote(client: AsyncClient):
    """Keyless client off the local network (a public peer IP; never inside THEMIS_LOCAL_NETWORKS)."""
    async with _keyless("203.0.113.7") as c:
        yield c


async def _login(c: AsyncClient, username: str, password: str):
    return await c.post("/api/v1/auth/login", json={"email": username, "password": password})


async def _session():
    agen = app.dependency_overrides[get_session]()
    return agen, await agen.__anext__()


# ---- First boot + local toggle ------------------------------------------------------------

async def test_migration_creates_admin_account_on_first_boot(tmp_path):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.migrations import v022_admin_account as m

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE api_keys (id INTEGER PRIMARY KEY)"))
        await m.up(conn)
        await m.up(conn)  # idempotent
        rows = (await conn.execute(text(
            "SELECT id, username, password_hash, allow_local_login FROM admin_account"))).fetchall()
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(api_keys)"))).fetchall()}
    await engine.dispose()
    assert rows == [(1, "admin", None, 1)]
    assert "admin_session" in cols


async def test_fresh_install_local_is_admin_and_remote_is_locked(local: AsyncClient, remote: AsyncClient):
    acct = (await local.get("/api/v1/admin-account")).json()
    assert acct == {"username": "admin", "password_set": False, "allow_local_login": True,
                    "full_access_keys": 1}  # conftest's seeded full-scope key
    assert (await local.get("/api/v1/auth/me")).json()["role"] == "admin"
    assert (await remote.get("/api/v1/auth/me")).json()["role"] is None
    assert (await remote.get("/api/v1/admin-account")).status_code == 401
    # No password yet → admin can't sign in remotely.
    assert (await _login(remote, "admin", "")).status_code == 401


async def test_requiring_login_needs_a_password_first(local: AsyncClient):
    r = await local.patch("/api/v1/admin-account", json={"allow_local_login": False})
    assert r.status_code == 409
    assert (await local.get("/api/v1/admin-account")).json()["allow_local_login"] is True


async def test_turning_off_local_login_makes_lan_sign_in(local: AsyncClient):
    assert (await local.put("/api/v1/admin-account/password", json={"password": "s3cret-pw"})).status_code == 200
    r = await local.patch("/api/v1/admin-account", json={"allow_local_login": False})
    assert r.status_code == 200 and r.json()["allow_local_login"] is False

    # The same LAN client is now just an anonymous visitor.
    assert (await local.get("/api/v1/projects")).status_code == 401
    assert (await local.get("/api/v1/auth/me")).json()["role"] is None

    # …until it signs in as admin.
    r = await _login(local, "admin", "s3cret-pw")
    assert r.status_code == 200
    h = {"X-Api-Key": r.json()["key"]}
    assert (await local.get("/api/v1/projects", headers=h)).status_code == 200
    assert (await local.patch("/api/v1/admin-account", headers=h,
                              json={"allow_local_login": True})).status_code == 200
    assert (await local.get("/api/v1/projects")).status_code == 200


# ---- Admin sign-in ------------------------------------------------------------------------

async def test_admin_login_session(local: AsyncClient, remote: AsyncClient):
    await local.put("/api/v1/admin-account/password", json={"password": "s3cret-pw"})
    assert (await _login(remote, "admin", "wrong")).status_code == 401

    r = await _login(remote, "ADMIN", "s3cret-pw")  # username is case-insensitive
    assert r.status_code == 200 and r.json()["admin"] is True
    h = {"X-Api-Key": r.json()["key"]}
    assert (await remote.get("/api/v1/auth/me", headers=h)).json()["role"] == "admin"
    assert (await remote.get("/api/v1/projects", headers=h)).status_code == 200
    assert (await remote.get("/api/v1/customers", headers=h)).status_code == 200
    # Admin is not a customer.
    assert (await remote.get("/api/v1/customer/projects", headers=h)).status_code == 403
    # Admin sessions don't clutter the API key list.
    names = [k["name"] for k in (await remote.get("/api/v1/api-keys", headers=h)).json()]
    assert "Admin session" not in names


async def test_password_change_signs_out_other_admin_sessions_but_not_the_caller(
        local: AsyncClient, remote: AsyncClient):
    await local.put("/api/v1/admin-account/password", json={"password": "password-one"})
    h1 = {"X-Api-Key": (await _login(remote, "admin", "password-one")).json()["key"]}
    h2 = {"X-Api-Key": (await _login(remote, "admin", "password-one")).json()["key"]}

    r = await remote.put("/api/v1/admin-account/password", headers=h1, json={"password": "password-two"})
    assert r.status_code == 200
    assert (await remote.get("/api/v1/projects", headers=h1)).status_code == 200
    assert (await remote.get("/api/v1/projects", headers=h2)).status_code == 401
    assert (await _login(remote, "admin", "password-one")).status_code == 401
    assert (await _login(remote, "admin", "password-two")).status_code == 200


async def test_customer_cannot_manage_admin_account(local: AsyncClient, remote: AsyncClient):
    await local.post("/api/v1/customers", json={"name": "A", "email": "a@example.com", "password": "pw"})
    h = {"X-Api-Key": (await _login(remote, "a@example.com", "pw")).json()["key"]}
    assert (await remote.get("/api/v1/admin-account", headers=h)).status_code == 403
    assert (await remote.put("/api/v1/admin-account/password", headers=h,
                             json={"password": "long-enough"})).status_code == 403
    assert (await remote.patch("/api/v1/admin-account", headers=h,
                               json={"allow_local_login": True})).status_code == 403


# ---- Offline recovery: one-time code in the server log -----------------------------------

def _logged_code(caplog) -> str:
    codes = re.findall(r"RECOVERY CODE: ([A-Z0-9]{5}-[A-Z0-9]{5})", caplog.text)
    assert codes, caplog.text
    return codes[-1]


async def test_recovery_code_resets_password_from_a_remote_only_fresh_install(remote: AsyncClient, caplog):
    caplog.set_level(logging.WARNING, logger="app.admin")
    r = await remote.post("/api/v1/auth/recover")
    assert r.status_code == 202 and "log" in r.json()["detail"]
    code = _logged_code(caplog)
    assert code not in r.text  # never returned over HTTP

    r = await remote.post("/api/v1/auth/recover/confirm", json={"code": code.lower(), "password": "fresh-password"})
    assert r.status_code == 200
    assert (await _login(remote, "admin", "fresh-password")).status_code == 200
    # Single use.
    r = await remote.post("/api/v1/auth/recover/confirm", json={"code": code, "password": "again-password"})
    assert r.status_code == 400


async def test_recovery_code_is_not_replaced_while_live(remote: AsyncClient, caplog):
    caplog.set_level(logging.WARNING, logger="app.admin")
    await remote.post("/api/v1/auth/recover")
    code = _logged_code(caplog)
    caplog.clear()
    await remote.post("/api/v1/auth/recover")  # e.g. someone else spamming the button
    assert "RECOVERY CODE" not in caplog.text
    r = await remote.post("/api/v1/auth/recover/confirm", json={"code": code, "password": "long-enough"})
    assert r.status_code == 200


async def test_recovery_code_burns_after_five_wrong_guesses(remote: AsyncClient, caplog):
    caplog.set_level(logging.WARNING, logger="app.admin")
    await remote.post("/api/v1/auth/recover")
    code = _logged_code(caplog)
    for _ in range(5):
        r = await remote.post("/api/v1/auth/recover/confirm", json={"code": "AAAAA-AAAAA", "password": "long-enough"})
        assert r.status_code == 400
    r = await remote.post("/api/v1/auth/recover/confirm", json={"code": code, "password": "long-enough"})
    assert r.status_code == 400
    # A burnt code can be replaced by a fresh one.
    caplog.clear()
    await remote.post("/api/v1/auth/recover")
    assert "RECOVERY CODE" in caplog.text


async def test_expired_recovery_code_is_rejected(remote: AsyncClient, caplog):
    caplog.set_level(logging.WARNING, logger="app.admin")
    await remote.post("/api/v1/auth/recover")
    code = _logged_code(caplog)
    agen, session = await _session()
    await session.execute(update(AdminAccount).values(recovery_code_expires_at="2000-01-01T00:00:00"))
    await session.commit()
    await agen.aclose()
    r = await remote.post("/api/v1/auth/recover/confirm", json={"code": code, "password": "long-enough"})
    assert r.status_code == 400


async def test_recovery_signs_out_existing_admin_sessions(local: AsyncClient, remote: AsyncClient, caplog):
    caplog.set_level(logging.WARNING, logger="app.admin")
    await local.put("/api/v1/admin-account/password", json={"password": "old-password"})
    h = {"X-Api-Key": (await _login(remote, "admin", "old-password")).json()["key"]}
    await remote.post("/api/v1/auth/recover")
    await remote.post("/api/v1/auth/recover/confirm", json={"code": _logged_code(caplog), "password": "new-password"})
    assert (await remote.get("/api/v1/projects", headers=h)).status_code == 401


# ---- Offline recovery: CLI ----------------------------------------------------------------

async def test_cli_reset_password_and_allow_local_login(local: AsyncClient, remote: AsyncClient, monkeypatch):
    from app import admin as cli
    import app.database as database

    await local.put("/api/v1/admin-account/password", json={"password": "forgotten"})
    await local.patch("/api/v1/admin-account", json={"allow_local_login": False})
    old = {"X-Api-Key": (await _login(remote, "admin", "forgotten")).json()["key"]}

    # Point the CLI at the test DB.
    agen = app.dependency_overrides[get_session]()
    test_session = await agen.__anext__()
    factory = lambda: _SessionCtx(test_session)  # noqa: E731

    async def _no_migrations():
        return None

    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(database, "init_db", _no_migrations)

    out = await cli._run("reset-password")
    new_pw = re.search(r"Password: (\S+)", out).group(1)
    assert "Password:" in out
    assert (await _login(remote, "admin", "forgotten")).status_code == 401
    assert (await _login(remote, "admin", new_pw)).status_code == 200
    assert (await remote.get("/api/v1/projects", headers=old)).status_code == 401

    assert (await local.get("/api/v1/projects")).status_code == 401
    await cli._run("allow-local-login")
    assert (await local.get("/api/v1/projects")).status_code == 200
    await agen.aclose()


class _SessionCtx:
    """`async with SessionLocal() as s` stand-in wrapping an existing test session."""
    def __init__(self, s):
        self.s = s

    async def __aenter__(self):
        return self.s

    async def __aexit__(self, *exc):
        return False



# ---- Hardening ----------------------------------------------------------------------------

async def test_admin_password_minimum_length(local: AsyncClient, remote: AsyncClient, caplog):
    for pw in ("short", "        "):
        r = await local.put("/api/v1/admin-account/password", json={"password": pw})
        assert r.status_code == 422
    assert (await local.get("/api/v1/admin-account")).json()["password_set"] is False

    caplog.set_level(logging.WARNING, logger="app.admin")
    await remote.post("/api/v1/auth/recover")
    r = await remote.post("/api/v1/auth/recover/confirm", json={"code": _logged_code(caplog), "password": "short"})
    assert r.status_code == 422


async def test_failed_logins_are_throttled_per_client(local: AsyncClient, remote: AsyncClient):
    await local.put("/api/v1/admin-account/password", json={"password": "right-password"})
    for _ in range(10):
        assert (await _login(remote, "admin", "wrong-guess")).status_code == 401
    # Even the right password is refused while throttled…
    assert (await _login(remote, "admin", "right-password")).status_code == 429
    # …but only for that address.
    async with _keyless("198.51.100.9") as other:
        assert (await _login(other, "admin", "right-password")).status_code == 200


async def test_scoped_staff_key_cannot_manage_admin_account(client: AsyncClient, local: AsyncClient):
    """`client` carries a full-scope staff API key (id set, not an admin session)."""
    assert (await client.get("/api/v1/admin-account")).status_code == 403
    assert (await client.put("/api/v1/admin-account/password",
                             json={"password": "hijacked-pw"})).status_code == 403
    assert (await local.get("/api/v1/admin-account")).json()["password_set"] is False


async def test_full_access_keys_count_excludes_login_sessions(local: AsyncClient, remote: AsyncClient):
    await local.put("/api/v1/admin-account/password", json={"password": "s3cret-pw"})
    before = (await local.get("/api/v1/admin-account")).json()["full_access_keys"]
    await _login(remote, "admin", "s3cret-pw")  # admin session: not counted
    await local.post("/api/v1/api-keys", json={"name": "narrow", "scopes": ["files:read"]})  # not full access
    assert (await local.get("/api/v1/admin-account")).json()["full_access_keys"] == before
    await local.post("/api/v1/api-keys", json={"name": "Browser", "scopes": ["apikeys:write"]})
    assert (await local.get("/api/v1/admin-account")).json()["full_access_keys"] == before + 1


async def test_admin_username_cannot_be_a_customer_email(local: AsyncClient):
    r = await local.post("/api/v1/customers", json={"name": "X", "email": "admin", "password": "pw"})
    assert r.status_code == 422
