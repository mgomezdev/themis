"""BIZ-289: the core -> plugin channel (PluginContext), plugin-owned defaults.toml, deprecated env read by core, and the
mock plugin's enablement. Precedence for the enabled flag: saved row > core override (deprecated env) > defaults.toml > manifest."""
import pytest
from pydantic import BaseModel

from app import config
from app.plugins import PluginManifest, register_plugin
from app.plugins.context import PluginContext, wants_context
from app.plugins.defaults import DEFAULTS_NAME, defaults_of, load_defaults
from app.plugins.host import plugin_host
from app.plugins.manifest import HOST_API


class _S(BaseModel):
    color: str = "model-default"


def test_load_defaults_parses_the_three_tables(tmp_path):
    (tmp_path / DEFAULTS_NAME).write_text('enabled = true\n[settings]\ncolor = "red"\n[legacy_env]\ncolor = "OLD_COLOR"\n')
    d = load_defaults(tmp_path)
    assert (d.enabled, d.settings, d.legacy_env) == (True, {"color": "red"}, {"color": "OLD_COLOR"})


@pytest.mark.parametrize("body", ["not toml [", 'enabled = "yes"', "settings = 3", '[legacy_env]\nx = 1'])
def test_a_broken_defaults_file_is_ignored_not_fatal(tmp_path, body, caplog):
    (tmp_path / DEFAULTS_NAME).write_text(body)
    assert load_defaults(tmp_path) == load_defaults(None)
    assert "Ignoring unreadable" in caplog.text


def test_no_file_means_no_defaults(tmp_path):
    assert load_defaults(tmp_path).settings == {} and load_defaults(None).enabled is None


def _register(tmp_path, factory, defaults_body=None, plugin_id="ctxdemo"):
    root = tmp_path / plugin_id
    root.mkdir()
    if defaults_body:
        (root / DEFAULTS_NAME).write_text(defaults_body)
    m = PluginManifest(id=plugin_id, name="Ctx", version="1.0.0", host_api=HOST_API, settings_model=_S, factory=factory)
    register_plugin(m, root)
    return m, root


async def test_a_factory_taking_a_context_gets_core_defined_values_and_defaults_are_merged(client, tmp_path, monkeypatch):
    monkeypatch.setenv("THEMIS_DATA_DIR", str(tmp_path / "data"))
    seen = {}

    def factory(settings, context):
        seen["settings"], seen["context"] = settings, context
        return object()

    _register(tmp_path, factory, '[settings]\ncolor = "shipped"\n')
    plugin_host.build_candidate("ctxdemo")
    ctx = seen["context"]
    assert isinstance(ctx, PluginContext) and ctx.plugin_id == "ctxdemo"
    assert ctx.data_dir == tmp_path / "data" / "plugin-data" / "ctxdemo" and ctx.plugin_dir == tmp_path / "ctxdemo"
    assert seen["settings"].color == "shipped"                                   # defaults.toml beats the model default
    plugin_host.build_candidate("ctxdemo", {"color": "saved"})
    assert seen["settings"].color == "saved"                                     # saved beats defaults.toml


async def test_a_one_argument_factory_still_works(client, tmp_path):
    got = []
    _register(tmp_path, lambda settings: got.append(settings) or object(), plugin_id="oneargs")
    plugin_host.build_candidate("oneargs")
    assert got[0].color == "model-default"
    assert wants_context(lambda a: a) is False and wants_context(lambda a, b: a) is True


async def test_legacy_env_declared_in_defaults_toml_is_read_by_core_and_below_saved(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.plugins.defaults.is_bundled", lambda pid: True)       # [legacy_env] is honoured for bundled plugins only
    seen = []
    _register(tmp_path, lambda s: seen.append(s.color) or object(), '[settings]\ncolor = "shipped"\n[legacy_env]\ncolor = "CTXDEMO_OLD"\n',
              plugin_id="envdemo")
    plugin_host.build_candidate("envdemo")
    monkeypatch.setenv("CTXDEMO_OLD", "from-env")
    plugin_host.build_candidate("envdemo")
    plugin_host.build_candidate("envdemo", {"color": "saved"})
    assert seen == ["shipped", "from-env", "saved"]


async def test_mock_plugin_is_off_by_default_and_core_reads_the_deprecated_env(client, monkeypatch):
    plugin_host._configs.pop("mock", None)                  # no saved row: the default applies
    monkeypatch.delenv("THEMIS_MOCK_PRINTERS", raising=False)
    assert plugin_host.is_enabled("mock") is False
    monkeypatch.setenv("THEMIS_MOCK_PRINTERS", "1")
    assert plugin_host.is_enabled("mock") is True
    monkeypatch.setenv("THEMIS_MOCK_PRINTERS", "no")
    assert plugin_host.is_enabled("mock") is False


async def test_saved_enabled_state_wins_over_the_mock_env(client, monkeypatch):
    monkeypatch.setenv("THEMIS_MOCK_PRINTERS", "1")
    await client.put("/api/v1/plugins/mock", json={"enabled": False})
    assert plugin_host.is_enabled("mock") is False
    monkeypatch.delenv("THEMIS_MOCK_PRINTERS")
    await client.put("/api/v1/plugins/mock", json={"enabled": True})
    assert plugin_host.is_enabled("mock") is True


def test_mock_defaults_file_is_off_and_maps_the_deprecated_env():
    d = defaults_of("mock")
    assert d.enabled is False and d.legacy_env == {"enabled": "THEMIS_MOCK_PRINTERS"}


def test_config_read_env_is_the_single_environment_reader(monkeypatch):
    monkeypatch.setenv("X_BIZ289", "v")
    assert config.read_env("X_BIZ289") == "v" and config.read_env("X_BIZ289_MISSING") is None
    assert config.get_app_data_dir() == config._resolve_data_dir()


async def test_an_installed_plugin_cannot_make_core_read_an_environment_variable(client, tmp_path, monkeypatch):
    """Third-party code must not be able to exfiltrate e.g. a secret through `[legacy_env]`: it is bundled-only."""
    monkeypatch.setenv("SECRET_TOKEN_BIZ289", "s3cr3t")
    seen = []
    _register(tmp_path, lambda s: seen.append(s.color) or object(), '[legacy_env]\ncolor = "SECRET_TOKEN_BIZ289"\n', plugin_id="thief")
    plugin_host.build_candidate("thief")
    assert seen == ["model-default"]
