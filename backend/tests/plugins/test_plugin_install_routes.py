"""Plugin install / upgrade / rollback / uninstall / restart API (BIZ-223): admin-session only, audit-logged, restart batched."""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text, update

from app.api.routes import plugin_install
from app.database import get_session
from app.main import app
from app.models import ApiKey, AuditLog, ExtensionSlot, InstalledPlugin, Job, PluginConfig
from app.auth import SCOPES
from app.plugins import _REGISTRY, installer
from app.services.api_key_service import generate_key, hash_key
from tests.plugins import pkg_builder as pb
from tests.plugins.dummy_plugin import make_manifest, migration
from tests.plugins.test_installer import SHA1, SHA2, FakeGithub, assert_clean, tgz
from tests.waiting import wait_until


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("THEMIS_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return tmp_path / "data"


@pytest.fixture
async def admin(client, session_factory):
    """A signed-in admin (an admin login session key); `client` itself holds a plain full-scope API key."""
    raw, prefix = generate_key()
    async with session_factory() as s:
        key = ApiKey(name="admin session", key_prefix=prefix, key_hash=hash_key(raw), scopes=sorted(SCOPES), enabled=True,
                     created_at="2026-01-01T00:00:00", admin_session=True)
        s.add(key)
        await s.commit()
        key_id = key.id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-Api-Key": raw}) as c:
        c.key_id = key_id
        yield c


def upload(b: bytes, name="p.zip"):
    return {"file": (name, b, "application/zip")}


async def audit_rows(session_factory):
    async with session_factory() as s:
        return [(r.action, r.target, r.actor) for r in (await s.execute(select(AuditLog).order_by(AuditLog.id))).scalars()]


# ---- the admin gate ------------------------------------------------------------------------------------------------

ENDPOINTS = [
    ("post", "/api/v1/plugins/install", {"files": upload(b"x")}),
    ("post", "/api/v1/plugins/install-from-github", {"json": {"repo_url": "https://github.com/a/b"}}),
    ("post", "/api/v1/plugins/install/" + "0" * 32 + "/commit", {}),
    ("delete", "/api/v1/plugins/install/" + "0" * 32, {}),
    ("get", "/api/v1/plugins/acme_inv/updates", {}),
    ("post", "/api/v1/plugins/acme_inv/upgrade", {}),
    ("post", "/api/v1/plugins/acme_inv/rollback", {}),
    ("delete", "/api/v1/plugins/acme_inv", {}),
    ("get", "/api/v1/system/restart", {}),
    ("post", "/api/v1/system/restart", {}),
]


@pytest.mark.parametrize("method,path,kw", ENDPOINTS, ids=[f"{m} {p.split('/api/v1/')[1][:28]}" for m, p, _ in ENDPOINTS])
async def test_api_keys_cannot_install_code_or_restart_even_with_every_scope(client, session_factory, method, path, kw):
    """`client` holds a key with EVERY scope (settings:write included) — still refused: only an interactive admin may."""
    r = await getattr(client, method)(path, **kw)
    assert r.status_code == 403 and "Admin sign-in required" in r.text
    assert await audit_rows(session_factory) == []


async def test_a_scoped_key_without_settings_write_is_refused_for_the_scope_too(client, session_factory):
    raw, prefix = generate_key()
    async with session_factory() as s:
        s.add(ApiKey(name="ro", key_prefix=prefix, key_hash=hash_key(raw), scopes=["settings:read"], enabled=True,
                     created_at="2026-01-01T00:00:00", admin_session=True))
        await s.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-Api-Key": raw}) as c:
        assert (await c.post("/api/v1/system/restart")).status_code == 403


# ---- install ----------------------------------------------------------------------------------------------------------

async def test_preview_then_commit_installs_and_stays_pending_until_restart(admin, session_factory, data_dir):
    r = await admin.post("/api/v1/plugins/install?preview=true", files=upload(pb.make_zip(pb.files("acme_inv"))))
    assert r.status_code == 200, r.text
    pv = r.json()["preview"]
    assert (pv["id"], pv["version"], pv["publisher"], pv["source"], pv["source_url"]) == ("acme_inv", "1.0.0", "Acme", "upload", "p.zip")
    assert len(pv["archive_sha256"]) == 64
    async with session_factory() as s:
        assert (await s.execute(select(InstalledPlugin))).first() is None          # a preview installs nothing
    assert await audit_rows(session_factory) == []

    r = await admin.post(f"/api/v1/plugins/install/{pv['token']}/commit")
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["restart_required"]) == ("pending_restart", True)
    assert (await admin.post(f"/api/v1/plugins/install/{pv['token']}/commit")).status_code == 400      # the token is spent
    assert await audit_rows(session_factory) == [("plugin.install", "acme_inv", f"session:{admin.key_id}")]

    listing = (await admin.get("/api/v1/plugins")).json()
    p = next(p for p in listing["plugins"] if p["id"] == "acme_inv")
    assert (p["source"], p["loaded"], p["enabled"], p["install"]["status"], p["install"]["publisher"]) == ("upload", False, False, "pending_restart", "Acme")
    assert listing["pending"] == [{"plugin_id": "acme_inv", "name": "Acme inventory", "version": "1.0.0", "change": "install"}]
    bundled = next(p for p in listing["plugins"] if p["id"] == "spoolman")
    assert (bundled["source"], bundled["loaded"], bundled["install"]) == ("bundled", True, None)


async def test_direct_install_and_a_discarded_preview(admin, session_factory, data_dir):
    r = await admin.post("/api/v1/plugins/install", files=upload(pb.make_tgz(pb.files("acme_inv")), "p.tgz"))
    assert r.status_code == 200 and r.json()["id"] == "acme_inv"
    pv = (await admin.post("/api/v1/plugins/install?preview=true", files=upload(pb.make_zip(pb.files("other_inv"))))).json()["preview"]
    assert (await admin.delete(f"/api/v1/plugins/install/{pv['token']}")).status_code == 204
    assert (await admin.post(f"/api/v1/plugins/install/{pv['token']}/commit")).status_code == 400
    assert not (data_dir / "plugins/other_inv").exists()


async def test_a_rejected_upload_is_a_400_with_the_reason_and_leaves_nothing(admin, session_factory, data_dir):
    r = await admin.post("/api/v1/plugins/install", files=upload(pb.make_zip({**pb.files(), "../evil.py": "x"})))
    assert r.status_code == 400 and "unsafe path" in r.json()["detail"]
    r = await admin.post("/api/v1/plugins/install", files=upload(pb.make_zip(pb.files("spoolman"))))
    assert r.status_code == 400 and "reserved" in r.json()["detail"]
    assert_clean(data_dir)
    assert await audit_rows(session_factory) == []


async def test_upload_of_a_newer_version_is_an_upgrade_with_rollback(admin, session_factory, data_dir):
    await admin.post("/api/v1/plugins/install", files=upload(pb.make_zip(pb.files("acme_inv", "1.0.0"))))
    r = await admin.post("/api/v1/plugins/install", files=upload(pb.make_zip(pb.files("acme_inv", "1.1.0"))))
    assert (r.json()["version"], r.json()["install"]["previous_version"]) == ("1.1.0", "1.0.0")
    assert (await admin.get("/api/v1/plugins")).json()["pending"][0]["change"] == "update"
    r = await admin.post("/api/v1/plugins/acme_inv/rollback")
    assert (r.status_code, r.json()["version"], r.json()["install"]["previous_version"]) == (200, "1.0.0", "1.1.0")
    assert [a for a, *_ in await audit_rows(session_factory)] == ["plugin.install", "plugin.upgrade", "plugin.rollback"]
    assert (await admin.post("/api/v1/plugins/spoolman/rollback")).status_code == 400


async def test_github_install_check_updates_and_upgrade(admin, session_factory, data_dir, monkeypatch):
    gh = FakeGithub(monkeypatch, {SHA1: tgz("1.0.0"), SHA2: tgz("1.1.0")})
    r = await admin.post("/api/v1/plugins/install-from-github", json={"repo_url": "https://github.com/acme/inv", "ref": "main", "preview": True})
    assert r.status_code == 200 and r.json()["preview"]["commit_sha"] == SHA1
    r = await admin.post("/api/v1/plugins/install-from-github", json={"repo_url": "https://github.com/acme/inv", "ref": "main"})
    assert r.json()["install"]["commit_sha"] == SHA1 and r.json()["install"]["can_check_updates"] is True
    assert (await admin.get("/api/v1/plugins/acme_inv/updates")).json()["update_available"] is False
    gh.heads["main"] = SHA2
    assert (await admin.get("/api/v1/plugins/acme_inv/updates")).json() == {"update_available": True, "ref": "main", "current_commit": SHA1, "latest_commit": SHA2}
    pv = (await admin.post("/api/v1/plugins/acme_inv/upgrade", json={"preview": True})).json()["preview"]
    assert (pv["version"], pv["commit_sha"]) == ("1.1.0", SHA2)                       # the new version is shown before confirming
    r = await admin.post(f"/api/v1/plugins/install/{pv['token']}/commit")
    assert (r.json()["version"], r.json()["install"]["commit_sha"]) == ("1.1.0", SHA2)
    r = await admin.post("/api/v1/plugins/install-from-github", json={"repo_url": "https://evil.example/a/b"})
    assert r.status_code == 400


async def test_upgrade_and_updates_are_github_only(admin, data_dir):
    await admin.post("/api/v1/plugins/install", files=upload(pb.make_zip(pb.files("acme_inv"))))
    assert (await admin.get("/api/v1/plugins/acme_inv/updates")).status_code == 400
    assert (await admin.post("/api/v1/plugins/acme_inv/upgrade")).status_code == 400


# ---- uninstall -----------------------------------------------------------------------------------------------------------

async def test_bundled_plugins_cannot_be_uninstalled(admin, session_factory):
    r = await admin.delete("/api/v1/plugins/spoolman")
    assert r.status_code == 409 and "disabled, not uninstalled" in r.text
    assert (await admin.delete("/api/v1/plugins/nope_one")).status_code == 400


async def test_uninstall_keeps_data_by_default_and_remove_data_drops_it(admin, session_factory, data_dir):
    async with session_factory() as s:
        s.add(PluginConfig(plugin_id="acme_inv", enabled=True, settings={"url": "x"}))
        s.add(InstalledPlugin(plugin_id="acme_inv", version="1.0.0", source="upload", archive_sha256="x", installed_at="t", status="active"))
        await s.execute(text("CREATE TABLE acme_inv_things (id INTEGER PRIMARY KEY)"))
        await s.execute(text("INSERT INTO acme_inv_things VALUES (1)"))
        await s.commit()
    _REGISTRY["acme_inv"] = make_manifest("acme_inv", migrations=(migration(
        1, "CREATE TABLE IF NOT EXISTS acme_inv_things (id INTEGER PRIMARY KEY)", "DROP TABLE IF EXISTS acme_inv_things"),))
    async with session_factory() as s:
        await s.execute(text("INSERT INTO plugin_schema_versions (plugin_id, version, name) VALUES ('acme_inv', 1, 'm')"))
        await s.commit()
    r = await admin.delete("/api/v1/plugins/acme_inv")                          # data kept
    assert (r.status_code, r.json()["status"]) == (200, "pending_removal")
    async with session_factory() as s:
        assert (await s.execute(text("SELECT count(*) FROM acme_inv_things"))).scalar() == 1
        assert await s.get(PluginConfig, "acme_inv") is not None
        assert (await s.get(PluginConfig, "acme_inv")).enabled is False          # stopped now; the code goes at the restart
    r = await admin.delete("/api/v1/plugins/acme_inv?remove_data=true")         # explicit choice
    assert r.status_code == 200
    async with session_factory() as s:
        assert (await s.execute(text("SELECT 1 FROM sqlite_master WHERE name='acme_inv_things'"))).first() is None
        assert await s.get(PluginConfig, "acme_inv") is None
        assert (await s.execute(text("SELECT count(*) FROM plugin_schema_versions WHERE plugin_id='acme_inv'"))).scalar() == 0
    assert [a for a, *_ in await audit_rows(session_factory)] == ["plugin.uninstall", "plugin.uninstall"]


# ---- restart ---------------------------------------------------------------------------------------------------------------

@pytest.fixture
def exited(monkeypatch):
    calls = []
    monkeypatch.setattr(plugin_install, "exit_process", lambda: calls.append(1))
    return calls


async def test_restart_status_lists_every_pending_change(admin, data_dir):
    await admin.post("/api/v1/plugins/install", files=upload(pb.make_zip(pb.files("acme_inv"))))
    await admin.post("/api/v1/plugins/install", files=upload(pb.make_zip(pb.files("other_inv"))))
    body = (await admin.get("/api/v1/system/restart")).json()
    assert sorted(p["plugin_id"] for p in body["pending"]) == ["acme_inv", "other_inv"] and body["printing"] == []


async def test_restart_exits_after_responding_and_is_audit_logged(admin, session_factory, exited):
    r = await admin.post("/api/v1/system/restart")
    assert (r.status_code, r.json()) == (200, {"restarting": True})
    await wait_until(lambda: exited, what="process exit")
    assert await audit_rows(session_factory) == [("system.restart", None, f"session:{admin.key_id}")]


async def test_restart_warns_when_printing_and_needs_force(admin, session_factory, exited, create_job, create_printer):
    pid = await create_printer(name="Bench P1S")
    job_id = await create_job(printer_id=pid)
    async with session_factory() as s:
        await s.execute(update(Job).where(Job.id == job_id).values(status="printing", assigned_printer_id=pid))
        await s.commit()
    assert (await admin.get("/api/v1/system/restart")).json()["printing"] == ["Bench P1S"]
    r = await admin.post("/api/v1/system/restart")
    assert r.status_code == 409 and r.json()["detail"] == {"error": "printing", "printers": ["Bench P1S"]}
    assert exited == [] and await audit_rows(session_factory) == []
    assert (await admin.post("/api/v1/system/restart", json={"force": True})).status_code == 200
    await wait_until(lambda: exited, what="process exit")
    async with session_factory() as s:
        assert (await s.execute(select(AuditLog))).scalars().one().detail == {"printing": ["Bench P1S"]}
