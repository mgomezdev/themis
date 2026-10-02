from app import version


async def test_health_reports_status_version_and_sha(client, monkeypatch):
    monkeypatch.setenv("THEMIS_GIT_SHA", "abc1234def")
    version.get_git_sha.cache_clear()
    try:
        response = await client.get("/api/v1/health")
    finally:
        version.get_git_sha.cache_clear()
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["git_sha"] == "abc1234def"
    assert body["version"] == version.get_version()


def test_git_sha_falls_back_to_git_then_unknown(monkeypatch):
    monkeypatch.delenv("THEMIS_GIT_SHA", raising=False)
    version.get_git_sha.cache_clear()
    try:
        monkeypatch.setattr(version.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no git")))
        assert version.get_git_sha() == "unknown"
    finally:
        version.get_git_sha.cache_clear()


def test_git_sha_blank_env_is_ignored(monkeypatch):
    monkeypatch.setenv("THEMIS_GIT_SHA", "  ")
    version.get_git_sha.cache_clear()
    try:
        class R:
            stdout = "deadbeef\n"
        monkeypatch.setattr(version.subprocess, "run", lambda *a, **k: R())
        assert version.get_git_sha() == "deadbeef"
    finally:
        version.get_git_sha.cache_clear()
