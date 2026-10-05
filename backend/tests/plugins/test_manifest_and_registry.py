import pytest

from app import plugins
from app.plugins import PluginError
from tests.plugins.dummy_plugin import DummySettings, make_manifest, migration


def test_a_valid_manifest_registers_and_is_found_by_id_and_kind():
    m = make_manifest()
    plugins.register_plugin(m)
    assert plugins.get_plugin("dummy_one") is m
    assert plugins.plugins_of_kind("dummy_kind") == [m] and plugins.plugins_of_kind("other") == []


def test_registering_a_different_plugin_under_a_taken_id_is_refused_but_the_same_manifest_is_idempotent():
    m = make_manifest()
    plugins.register_plugin(m)
    plugins.register_plugin(m)
    with pytest.raises(PluginError, match="already registered"):
        plugins.register_plugin(make_manifest())


@pytest.mark.parametrize("over,match", [
    ({"plugin_id": "Bad-Id"}, "must match"),
    ({"plugin_id": "ab"}, "must match"),
    ({"host_api": 99}, "host_api"),
    ({"secret_fields": frozenset({"nope"})}, "not in its settings model"),
    ({"factory": None}, "callable"),
])
def test_a_malformed_or_incompatible_manifest_is_rejected(over, match):
    with pytest.raises(PluginError, match=match):
        make_manifest(**over)


def test_a_migration_without_down_is_rejected_and_an_unknown_tab_renderer_too():
    mod = migration(1, "select 1", "select 1")
    del mod.down
    with pytest.raises(PluginError, match="down"):
        make_manifest(migrations=(mod,))
    from app.plugins import UiContribution, UiTab
    with pytest.raises(PluginError, match="renderer"):
        make_manifest(ui=UiContribution(tabs=(UiTab("t", "T", renderer="react"),)))


def test_the_reserved_permissions_key_is_parsed_and_ignored():
    assert make_manifest(permissions=("net",)).permissions == ("net",)


def test_load_bundled_skips_a_plugin_that_fails_to_import_and_reports_it(monkeypatch):
    monkeypatch.setattr(plugins, "BUNDLED_MODULES", ("no.such.module",))
    assert plugins.load_bundled() == ["no.such.module"]      # boots anyway
    assert plugins.registered_plugins() == []


def test_settings_model_is_what_the_manifest_validates_with():
    assert make_manifest().settings_model is DummySettings
