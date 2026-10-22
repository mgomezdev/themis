import pytest

from app import plugins
from app.plugins import PluginError
from app.plugins.capabilities.definition import CapabilityDef
from app.plugins.manifest import Provide, Requirement
from app.plugins.package import check_matches, parse_toml
from tests.plugins.dummy_plugin import DummySettings, make_manifest, migration


def test_a_valid_manifest_registers_and_is_found_by_id_and_capability():
    m = make_manifest()
    plugins.register_plugin(m)
    assert plugins.get_plugin("dummy_one") is m
    assert plugins.providers_of("dummy_one.ping") == [m] and plugins.providers_of("other.thing") == []


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


def test_a_component_tab_declares_its_component_and_required_feature():
    from app.plugins import UiContribution, UiTab
    ok = make_manifest(ui=UiContribution(tabs=(UiTab("t", "T", "component", component="x", requires="F"),)))
    assert (ok.ui.tabs[0].component, ok.ui.tabs[0].requires) == ("x", "F")


def test_the_reserved_permissions_key_is_parsed_and_ignored():
    assert make_manifest(permissions=("net",)).permissions == ("net",)


def test_load_bundled_skips_a_plugin_that_fails_to_import_and_reports_it(monkeypatch):
    monkeypatch.setattr(plugins, "BUNDLED_MODULES", ("no.such.module",))
    assert plugins.load_bundled() == ["no.such.module"]      # boots anyway
    assert plugins.registered_plugins() == []


def test_settings_model_is_what_the_manifest_validates_with():
    assert make_manifest().settings_model is DummySettings


def test_bundled_plugins_are_discovered_by_directory_and_the_kind_contracts_are_not_one():
    discovered = plugins._discover_bundled()
    assert "app.plugins.capabilities" not in discovered and not any(m.rsplit(".", 1)[-1].startswith("_") for m in discovered)
    assert {"app.plugins.spoolman", "app.plugins.local_inventory"} <= set(discovered)
    assert plugins.load_bundled() == []                              # every discovered package really exports a MANIFEST


TOML = 'id = "acme_inv"\nname = "A"\nversion = "1.0.0"\nhost_api = 1\nentry = "acme_inv:MANIFEST"\n'


def test_requirement_parse():
    assert Requirement.parse("a.b") == Requirement("a.b", 1)
    assert Requirement.parse("a.b@3") == Requirement("a.b", 3)
    for bad in ("a", "a.b@", "a.b@x", "a.b@0", "A.b"):
        with pytest.raises(PluginError):
            Requirement.parse(bad)


def test_defined_capability_id_must_start_with_plugin_id_and_not_shadow_core():
    d = lambda i: CapabilityDef(i, 1, "x")
    make_manifest("dummy_one", defines=(d("dummy_one.thing"),))
    with pytest.raises(PluginError, match="must start with 'dummy_one.'"):
        make_manifest("dummy_one", defines=(d("other.thing"),))
    with pytest.raises(PluginError, match="core"):
        make_manifest("inventory", provides={}, defines=(d("inventory.filament"),))     # a plugin id that is a core prefix


def test_provides_validation():
    with pytest.raises(PluginError, match="capability id"):
        make_manifest(provides={"nodot": Provide()})
    with pytest.raises(PluginError, match="version"):
        make_manifest(provides={"a.b": Provide(version=0)})


def test_a_plugin_cannot_require_what_it_provides():
    with pytest.raises(PluginError, match="itself"):
        make_manifest("dummy_one", requires=(Requirement("dummy_one.ping"),))


def test_kind_is_gone():
    assert not hasattr(make_manifest(), "kind") and not hasattr(make_manifest(), "capabilities")


def test_registry_catalog_and_providers():
    a = make_manifest("plug_a", cap="plug_a.ping")
    b = make_manifest("plug_b", cap="plug_b.ping", provides={"plug_a.ping": Provide()}, defines=())
    plugins.register_plugin(a)
    plugins.register_plugin(b)
    cat = plugins.capability_catalog()
    assert "inventory.filament" in cat and "plug_a.ping" in cat and "plug_b.ping" not in cat
    assert [m.id for m in plugins.providers_of("plug_a.ping")] == ["plug_a", "plug_b"]
    assert plugins.definer_of("plug_a.ping") == "plug_a" and plugins.definer_of("inventory.filament") is None


def test_toml_parses_capability_lists_and_rejects_kind():
    t = parse_toml(TOML + 'provides = ["inventory.filament@1"]\nrequires = ["x.y@2"]\noptional = ["z.w"]\ndefines = ["acme_inv.reports"]\n')
    assert (t.provides, t.requires, t.optional, t.defines) == ((("inventory.filament", 1),), (("x.y", 2),), (("z.w", 1),), ("acme_inv.reports",))
    with pytest.raises(PluginError, match="unknown keys"):
        parse_toml(TOML + 'kind = "filament_inventory"\n')
    with pytest.raises(PluginError, match="defines"):
        parse_toml(TOML + 'defines = ["acme_inv.reports@2"]\n')


def test_check_matches_compares_capability_lists():
    m = make_manifest("acme_inv", cap="acme_inv.ping")
    ok = parse_toml(TOML + 'provides = ["acme_inv.ping@1"]\ndefines = ["acme_inv.ping"]\n')
    check_matches(ok, m)
    bad = parse_toml(TOML + 'provides = ["acme_inv.ping@2"]\ndefines = ["acme_inv.ping"]\n')
    with pytest.raises(PluginError, match="provides"):
        check_matches(bad, m)
