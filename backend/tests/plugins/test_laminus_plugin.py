"""The Laminus plugin (BIZ-260/BIZ-289): the service URL lives in plugin settings; precedence is saved setting > deprecated
`LAMINUS_SIDECAR_URL` (read by core, never by the plugin) > the plugin's shipped defaults.toml; the connection test names
unconfigured / unavailable / healthy. The suite blanks the shipped URL default unless a test is marked `shipped_defaults`."""
from unittest.mock import MagicMock

import pytest

from app import config
from app.plugins.host import plugin_host
from app.services.providers.laminus.sidecar_client import SidecarError
from app.services.providers.format_provider import FormatProvider
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
    assert isinstance(get_format_provider(), FormatProvider)          # format work needs no server, never None


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
    assert r["ok"] is False and r["state"] == "unconfigured" and "service URL" in r["message"]


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


async def test_a_whitespace_only_saved_url_counts_as_unset(client, monkeypatch):
    monkeypatch.setenv("LAMINUS_SIDECAR_URL", ENV_URL)
    await client.put("/api/v1/plugins/laminus", json={"settings": {"url": "   "}})
    assert configured_server_url() == ENV_URL
    monkeypatch.delenv("LAMINUS_SIDECAR_URL")
    assert configured_server_url() is None


async def test_a_settings_row_without_url_falls_back_to_env(client, monkeypatch):
    """A row created by enabling the plugin (no `url` key stored) must not break URL resolution."""
    await client.put("/api/v1/plugins/laminus", json={"enabled": True})
    assert "url" not in plugin_host.settings("laminus")
    assert configured_server_url() is None
    monkeypatch.setenv("LAMINUS_SIDECAR_URL", ENV_URL)
    assert configured_server_url() == ENV_URL


# --- BIZ-289 precedence: saved setting > deprecated env (via core) > defaults.toml ------------------------------------

DEFAULT_URL = "http://orca:5000"


@pytest.mark.shipped_defaults
async def test_default_only_uses_the_plugins_shipped_defaults_file(client):
    assert configured_server_url() == DEFAULT_URL
    assert get_slicing_provider().identity == DEFAULT_URL


@pytest.mark.shipped_defaults
async def test_env_only_overrides_the_default_and_warns_once(client, monkeypatch, caplog):
    from app.plugins import defaults
    defaults._warned.clear()
    monkeypatch.setenv("LAMINUS_SIDECAR_URL", ENV_URL)
    with caplog.at_level("WARNING", logger="app"):
        assert configured_server_url() == ENV_URL and configured_server_url() == ENV_URL
    assert [r.getMessage() for r in caplog.records].count(
        next(m for m in (r.getMessage() for r in caplog.records) if "LAMINUS_SIDECAR_URL is deprecated" in m)) == 1


@pytest.mark.shipped_defaults
async def test_saved_wins_over_env_and_default_and_clearing_falls_back_through_the_chain(client, monkeypatch):
    monkeypatch.setenv("LAMINUS_SIDECAR_URL", ENV_URL)
    await client.put("/api/v1/plugins/laminus", json={"settings": {"url": SAVED_URL}})
    assert configured_server_url() == SAVED_URL
    await client.put("/api/v1/plugins/laminus", json={"settings": {"url": ""}})
    assert configured_server_url() == ENV_URL
    monkeypatch.delenv("LAMINUS_SIDECAR_URL")
    assert configured_server_url() == DEFAULT_URL


@pytest.mark.shipped_defaults
async def test_disabled_is_unconfigured_even_with_default_and_env(client, monkeypatch):
    monkeypatch.setenv("LAMINUS_SIDECAR_URL", ENV_URL)
    await client.put("/api/v1/plugins/laminus", json={"enabled": False})
    assert configured_server_url() is None and get_slicing_provider() is None


@pytest.mark.shipped_defaults
async def test_a_plugin_instance_is_built_from_the_resolved_url_not_from_the_environment(client, monkeypatch):
    assert plugin_host.build_candidate("laminus").url == DEFAULT_URL
    monkeypatch.setenv("LAMINUS_SIDECAR_URL", ENV_URL)
    assert plugin_host.build_candidate("laminus").url == ENV_URL
    assert plugin_host.build_candidate("laminus", {"url": SAVED_URL}).url == SAVED_URL      # an unsaved draft wins too
    assert plugin_host.build_candidate("laminus", {"url": ""}).url == ENV_URL               # blank draft = unset


def test_the_laminus_defaults_file_ships_and_is_packaged():
    import tomllib
    from pathlib import Path
    from app.plugins.defaults import load_defaults
    root = Path(__file__).resolve().parents[2]
    d = load_defaults(root / "app" / "plugins" / "laminus")
    assert d.settings["url"] == DEFAULT_URL and d.legacy_env == {"url": "LAMINUS_SIDECAR_URL"}
    assert "plugins/*/defaults.toml" in tomllib.loads((root / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]["app"]


def test_compose_works_out_of_the_box_without_the_env_var():
    """The default URL's host/port is the compose `orca` service, and compose no longer sets the plugin's env var."""
    from pathlib import Path
    from urllib.parse import urlparse
    import yaml
    repo = Path(__file__).resolve().parents[3]
    compose = yaml.safe_load((repo / "docker-compose.yml").read_text())
    u = urlparse(DEFAULT_URL)
    assert u.hostname in compose["services"]
    assert f":{u.port}/" in " ".join(compose["services"][u.hostname]["healthcheck"]["test"])
    for name in ("docker-compose.yml", "docker-compose.dev.yml", "Dockerfile"):
        assert "LAMINUS_SIDECAR_URL" not in (repo / name).read_text(), name


# --- BIZ-288 connection-level options (timeouts) ----------------------------------------------------------------------

async def _configure(client, **settings):
    r = await client.put("/api/v1/plugins/laminus", json={"settings": {"url": SAVED_URL, **settings}})
    assert r.status_code == 200, r.text


@pytest.mark.shipped_defaults
async def test_timeout_options_default_to_the_shipped_values_and_reach_the_adapter(client):
    p = get_slicing_provider()
    assert (p._request_timeout_s, p._poll_timeout_s) == (630.0, 620.0)
    assert plugin_host.build_candidate("laminus").health_timeout_s == 5.0


async def test_saved_timeouts_reach_the_adapter_and_the_http_client_without_restart(client):
    await _configure(client, request_timeout_s=900, poll_timeout_s=800)
    p = get_slicing_provider()
    assert (p._request_timeout_s, p._poll_timeout_s) == (900, 800)
    assert p._client()._client.timeout.read == 900                 # the httpx client really carries it
    await _configure(client, request_timeout_s=None, poll_timeout_s=None)    # cleared: back to the defaults
    p = get_slicing_provider()
    assert (p._request_timeout_s, p._poll_timeout_s) == (630.0, 620.0)


async def test_the_poll_ceiling_option_is_what_the_slice_waits_for(client, monkeypatch, tmp_path):
    from app.services.providers.laminus.sidecar_client import LaminusSidecarClient
    from app.services.providers.slicing import SliceSpec
    seen = {}
    monkeypatch.setattr(LaminusSidecarClient, "slice_start", lambda self, *a, **k: "j1")
    monkeypatch.setattr(LaminusSidecarClient, "poll_status",
                        lambda self, job_id, poll_interval=2.0, timeout=None: seen.update(timeout=timeout) or {"sliced_file": "o.gcode"})
    monkeypatch.setattr(LaminusSidecarClient, "download", lambda self, job_id, dest: dest)
    await _configure(client, poll_timeout_s=123)
    src = tmp_path / "m.3mf"
    src.write_bytes(b"x")
    get_slicing_provider().slice(SliceSpec(source_file=src, plate=1, machine_ref="m", process_ref="p", filament_refs=["f"]), tmp_path)
    assert seen["timeout"] == 123


async def test_the_health_timeout_option_is_used_by_the_connection_test(client, monkeypatch):
    health = MagicMock(return_value={"status": "ok"})
    monkeypatch.setattr("app.services.providers.laminus.adapter.LaminusSlicingProvider.health", health)
    await client.post("/api/v1/plugins/laminus/test", json={"settings": {"url": SAVED_URL, "health_timeout_s": 2.5}})
    assert health.call_args.args == (2.5,)
    await client.post("/api/v1/plugins/laminus/test", json={"settings": {"url": SAVED_URL}})
    assert health.call_args.args == (5.0,)


@pytest.mark.parametrize("field", ["health_timeout_s", "request_timeout_s", "poll_timeout_s"])
async def test_non_positive_timeouts_are_rejected_and_not_saved(client, field):
    bad = await client.put("/api/v1/plugins/laminus", json={"settings": {"url": SAVED_URL, field: 0}})
    assert bad.status_code == 422
    assert (await client.get("/api/v1/plugins/laminus")).json()["settings"][field] != 0


def test_the_defaults_file_ships_the_timeouts_matching_the_adapter_defaults():
    from pathlib import Path
    from app.plugins.defaults import load_defaults
    from app.services.providers.laminus.connection import LaminusSettings
    from app.services.providers.laminus.sidecar_client import DEFAULT_POLL_TIMEOUT_S, DEFAULT_REQUEST_TIMEOUT_S
    d = load_defaults(Path(__file__).resolve().parents[2] / "app" / "plugins" / "laminus").settings
    assert d["request_timeout_s"] == DEFAULT_REQUEST_TIMEOUT_S and d["poll_timeout_s"] == DEFAULT_POLL_TIMEOUT_S
    assert set(d) <= set(LaminusSettings.model_fields)


async def test_the_manifest_asks_for_the_persistent_connection_pill(client):
    plugins = {p["id"]: p for p in (await client.get("/api/v1/plugins")).json()["plugins"]}
    assert plugins["laminus"]["ui"]["connection_status"] is True
    assert plugins["spoolman"]["ui"]["connection_status"] is False


async def test_null_timeouts_fall_back_to_the_defaults_in_the_client_and_the_health_call(client, monkeypatch):
    nulls = {"health_timeout_s": None, "request_timeout_s": None, "poll_timeout_s": None}
    monkeypatch.setattr(plugin_host, "effective_settings", lambda pid, saved=None: {"url": SAVED_URL, **nulls})
    p = get_slicing_provider()
    assert (p._request_timeout_s, p._poll_timeout_s) == (630.0, 620.0)
    assert p._client()._client.timeout.read == 630.0
    conn = plugin_host.build_candidate("laminus")
    assert (conn.health_timeout_s, conn.request_timeout_s, conn.poll_timeout_s) == (5.0, 630.0, 620.0)
    health = MagicMock(return_value={"status": "ok"})
    monkeypatch.setattr("app.services.providers.laminus.adapter.LaminusSlicingProvider.health", health)
    await conn.test_connection()
    assert health.call_args.args == (5.0,)


async def test_invalid_saved_settings_make_slicing_unconfigured_not_a_crash(client, monkeypatch, caplog):
    monkeypatch.setattr(plugin_host, "effective_settings", lambda pid, saved=None: {"url": SAVED_URL, "poll_timeout_s": -5})
    with caplog.at_level("WARNING"):
        assert get_slicing_provider() is None
    assert "Laminus settings are invalid" in caplog.text
