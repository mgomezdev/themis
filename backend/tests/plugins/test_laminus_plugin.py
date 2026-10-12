"""The Laminus plugin (BIZ-260): the service URL lives in plugin settings, `LAMINUS_SIDECAR_URL` is only the compatible
default, and the connection test names unconfigured / unavailable / healthy."""
from unittest.mock import MagicMock

import pytest

from app import config
from app.services.providers.laminus.sidecar_client import SidecarError
from app.services.providers.slicing import configured_server_url, get_format_provider, get_slicing_provider

ENV_URL = "http://env-laminus:5000"
SAVED_URL = "http://saved-laminus:5000"
HEALTH = "app.services.providers.laminus.sidecar_client.LaminusSidecarClient.health"


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("LAMINUS_SIDECAR_URL", raising=False)


async def test_the_plugin_is_bundled_and_listed(client):
    (p,) = [p for p in (await client.get("/api/v1/plugins")).json()["plugins"] if p["id"] == "laminus"]
    assert (p["source"], p["enabled"], p["provides"]) == ("bundled", True, [])
    assert "url" in (await client.get("/api/v1/plugins/laminus")).json()["settings_schema"]["properties"]


async def test_unconfigured_without_saved_url_or_env(client):
    assert configured_server_url() is None
    assert get_slicing_provider() is None
    assert get_format_provider().identity == ""          # format work needs no server


async def test_env_var_is_the_default_when_nothing_is_saved(client, monkeypatch):
    monkeypatch.setenv("LAMINUS_SIDECAR_URL", ENV_URL + "/")
    assert configured_server_url() == ENV_URL
    assert get_slicing_provider().identity == ENV_URL


async def test_a_saved_url_wins_over_env_and_applies_without_restart(client, monkeypatch):
    monkeypatch.setenv("LAMINUS_SIDECAR_URL", ENV_URL)
    resp = await client.put("/api/v1/plugins/laminus", json={"settings": {"url": SAVED_URL + "/"}})
    assert resp.status_code == 200
    assert configured_server_url() == SAVED_URL
    assert get_slicing_provider().identity == SAVED_URL
    await client.put("/api/v1/plugins/laminus", json={"settings": {"url": ""}})      # cleared: back to the env default
    assert configured_server_url() == ENV_URL


async def test_disabling_the_plugin_makes_slicing_unconfigured_even_with_env(client, monkeypatch):
    monkeypatch.setenv("LAMINUS_SIDECAR_URL", ENV_URL)
    await client.put("/api/v1/plugins/laminus", json={"enabled": False})
    assert configured_server_url() is None and get_slicing_provider() is None
    await client.put("/api/v1/plugins/laminus", json={"enabled": True})
    assert configured_server_url() == ENV_URL


async def test_a_url_that_is_not_http_is_rejected_and_not_saved(client):
    bad = await client.put("/api/v1/plugins/laminus", json={"settings": {"url": "orca:5000"}})
    assert bad.status_code == 422 and "http" in bad.json()["detail"]
    assert (await client.get("/api/v1/plugins/laminus")).json()["settings"].get("url") in (None, "")


async def test_connection_test_reports_unconfigured(client):
    r = (await client.post("/api/v1/plugins/laminus/test", json={})).json()
    assert r["ok"] is False and r["state"] == "unconfigured" and "LAMINUS_SIDECAR_URL" in r["message"]


async def test_connection_test_reports_unavailable_with_the_reason(client, monkeypatch):
    monkeypatch.setattr(HEALTH, MagicMock(side_effect=SidecarError("health check request failed: refused")))
    r = (await client.post("/api/v1/plugins/laminus/test", json={"settings": {"url": SAVED_URL}})).json()
    assert r["ok"] is False and r["state"] == "unavailable" and "refused" in r["message"]


async def test_connection_test_reports_healthy_for_an_unsaved_draft_url_and_saves_nothing(client, monkeypatch):
    health = MagicMock(return_value={"status": "ok", "version": "1.2.3"})
    monkeypatch.setattr(HEALTH, health)
    r = (await client.post("/api/v1/plugins/laminus/test", json={"settings": {"url": SAVED_URL}})).json()
    assert r == {"ok": True, "state": "healthy", "version": "1.2.3"}
    assert configured_server_url() is None


async def test_connection_test_uses_the_env_default_when_no_draft_url(client, monkeypatch):
    monkeypatch.setenv("LAMINUS_SIDECAR_URL", ENV_URL)
    monkeypatch.setattr(HEALTH, MagicMock(return_value={"status": "ok"}))
    assert (await client.post("/api/v1/plugins/laminus/test", json={})).json()["state"] == "healthy"


def test_config_py_no_longer_names_laminus_or_the_unused_orca_paths():
    """Only the thumbnail_regen executable remains (until BIZ-284); the sidecar URL and Orca config dir are gone."""
    assert not hasattr(config, "get_laminus_sidecar_url") and not hasattr(config, "get_orca_config_dir")
    assert hasattr(config, "get_orca_executable")
