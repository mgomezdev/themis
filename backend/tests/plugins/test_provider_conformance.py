"""Cross-capability provider conformance (BIZ-301): the host-side contract every capability shares, run over printer, slicing,
notification and inventory providers with fakes registered at runtime (no edit to any core service). See conformance.py and
docs/provider-interfaces.md "Conformance"."""
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from app import plugins
from app.models import PluginConfig
from app.plugins.capabilities import CORE
from app.plugins.capabilities.definition import required_operations
from tests.plugins import conformance as c
from tests.plugins.dummy_plugin import make_manifest

pytestmark = pytest.mark.usefixtures("_isolated_registry")


@pytest.fixture(params=c.KITS, ids=lambda k: k.capability)
def kit(request) -> c.Kit:
    return request.param


async def _register(host, kit, plugin_id="conf_prov", flavour="valid", **kw):
    plugins.register_plugin(c.provider_manifest(kit, plugin_id, flavour, **kw))
    await c.enable(host, kit, plugin_id)


# ---- declaration -----------------------------------------------------------------------------------------------------------

async def test_a_valid_provider_registers_serves_and_is_exercised_without_editing_core(host, kit):
    await _register(host, kit)

    assert c.serving(host, kit, "conf_prov")
    result = await c.invoke(host, kit, "conf_prov")
    assert result.ok and result.reason == "ok"
    assert host.rejections("conf_prov") == {}


async def test_a_provider_missing_a_required_operation_fails_validation_and_serves_nothing(host, kit):
    await _register(host, kit, flavour="incomplete")
    missing = c.missing_operation(kit)

    assert not c.serving(host, kit, "conf_prov")
    reason = host.rejections("conf_prov")[kit.capability]
    if kit.definition.protocol is None:
        assert missing in reason                                  # a duck-typed contract names the operation
    else:
        assert kit.definition.protocol.__name__ in reason         # a Protocol contract names the protocol
    assert missing in required_operations(kit.definition)
    result = await c.invoke(host, kit, "conf_prov")
    assert (result.ok, result.reason) == (False, "inactive")      # a rejected provider is never called


async def test_a_provider_advertising_a_feature_the_capability_does_not_define_is_rejected(host, kit):
    await _register(host, kit, features=frozenset({c.BAD_FEATURE}))

    assert not c.serving(host, kit, "conf_prov")
    assert f"advertises unsupported feature(s) {c.BAD_FEATURE}" in host.rejections("conf_prov")[kit.capability]


async def test_a_provider_claiming_another_contract_version_is_rejected(host, kit):
    await _register(host, kit, version=kit.definition.version + 1)

    assert not c.serving(host, kit, "conf_prov")
    assert f"v{kit.definition.version + 1}" in host.rejections("conf_prov")[kit.capability]


async def test_a_feature_the_capability_defines_is_accepted():
    """The vocabulary check is not a blanket refusal: inventory declares its flags and a provider may claim them."""
    from app.plugins.capabilities.filament_inventory import TRACKS_WEIGHT
    assert TRACKS_WEIGHT in CORE["inventory.filament"].features


# ---- failure, timeout, secret safety ---------------------------------------------------------------------------------------

async def test_a_failing_provider_is_a_neutral_error_that_hides_secrets_and_state_is_scoped_to_the_plugin(host, kit, session_factory):
    await _register(host, kit, flavour="failing")

    result = await c.invoke(host, kit, "conf_prov")

    assert (result.ok, result.reason) == (False, "error")
    assert c.SECRET not in result.error and "***" in result.error                    # the configured secret is masked
    assert "Traceback" not in result.error
    assert host.state("conf_prov")["last_error"] == result.error                     # recorded against the plugin, redacted
    async with session_factory() as s:                                                # and nothing persisted carries the secret
        row = (await s.execute(select(PluginConfig).where(PluginConfig.plugin_id == "conf_prov"))).scalar_one()
        assert c.SECRET not in str(row.state) and c.SECRET not in str(host.redact("conf_prov", result.error))


async def test_a_timing_out_provider_is_a_timeout_outcome_naming_the_limit(host, kit):
    await _register(host, kit, flavour="hanging")

    result = await c.invoke(host, kit, "conf_prov", timeout=0.05)

    assert (result.ok, result.reason) == (False, "timeout")
    assert "timed out after 0.05s" in result.error and c.SECRET not in result.error
    assert "timed out" in host.state("conf_prov")["last_error"]


async def test_a_failing_provider_leaves_unrelated_plugins_serving_and_a_recovered_one_clears_its_error(host, kit):
    plugins.register_plugin(make_manifest("conf_other"))
    await host.update_config("conf_other", enabled=True)
    await host.set_provider("conf_other.ping", "conf_other")
    await _register(host, kit, flavour="failing")

    failed = await c.invoke(host, kit, "conf_prov")
    other = await host.call("conf_other.ping", "ping")

    assert not failed.ok and other.ok and other.value == "pong"                       # the failure stayed inside its plugin
    assert host.state("conf_other").get("last_error") is None
    # recovery: swap in a healthy provider under the same id (a fixed release); the recorded error clears on the next success
    plugins._REGISTRY["conf_prov"] = c.provider_manifest(kit, "conf_prov", "valid")
    await host.update_config("conf_prov", settings={"url": "http://conf.test/2"})
    assert (await c.invoke(host, kit, "conf_prov")).ok
    assert host.state("conf_prov")["last_error"] is None


# ---- lifecycle -------------------------------------------------------------------------------------------------------------

async def test_disabling_or_removing_a_provider_stops_every_call_and_reregistering_restores_it(host, kit):
    await _register(host, kit)
    assert (await c.invoke(host, kit, "conf_prov")).ok

    await host.update_config("conf_prov", enabled=False)
    off = await c.invoke(host, kit, "conf_prov")
    assert (off.ok, off.reason) == (False, "inactive") and not c.serving(host, kit, "conf_prov")

    await host.update_config("conf_prov", enabled=True)
    assert c.serving(host, kit, "conf_prov")
    manifest = plugins._REGISTRY.pop("conf_prov")                                       # the plugin is uninstalled
    await host.reload()
    gone = await c.invoke(host, kit, "conf_prov")
    assert (gone.ok, gone.reason) == (False, "inactive") and not c.serving(host, kit, "conf_prov")

    plugins.register_plugin(manifest)                                                   # reinstalled: config row was kept
    await host.reload()
    assert c.serving(host, kit, "conf_prov") and (await c.invoke(host, kit, "conf_prov")).ok


# ---- every bundled provider honours the same declaration rules --------------------------------------------------------------

def test_every_bundled_provider_declares_a_matching_version_and_a_known_feature_vocabulary():
    plugins.load_bundled()
    catalog = plugins.capability_catalog()
    checked = 0
    for m in plugins.registered_plugins():
        for cap, provide in m.provides.items():
            d = catalog[cap]
            assert provide.version == d.version, f"{m.id} provides {cap} v{provide.version}, catalog has v{d.version}"
            if cap in CORE:
                assert provide.features <= d.features, f"{m.id}: unsupported feature(s) {sorted(provide.features - d.features)} for {cap}"
            checked += 1
    assert checked >= 6                                                                  # notify channels, slicing, inventory


async def test_every_bundled_provider_that_can_be_built_passes_host_validation(session_factory):
    from app.plugins.host import plugin_host
    plugins.load_bundled()
    built = []
    for m in plugins.registered_plugins():
        if not m.provides or any(f.is_required() for k, f in m.settings_model.model_fields.items()):
            continue                                                                     # needs configuration before it can be built
        await plugin_host.update_config(m.id, enabled=True)
        assert plugin_host.rejections(m.id) == {}, f"{m.id}: {plugin_host.rejections(m.id)}"
        if plugin_host.build_error(m.id) is None:
            built.append(m.id)
    assert {"local_inventory", "laminus"} <= set(built)


def test_every_bundled_printer_plugin_satisfies_the_printer_client_contract():
    """Vendor printer plugins serve `printer.client` through their client class (the factory), not a `provides` entry: the
    class must carry every operation of the capability's contract and describe its connection form."""
    from app.services.printer_client_factory import printer_client_plugins
    plugins.load_bundled()
    found = printer_client_plugins()
    contract = required_operations(CORE["printer.client"])
    assert {m.id for m, _ in found} >= {"bambu", "elegoo_centauri", "snapmaker", "moonraker", "mock"}
    types_seen = set()
    for m, cls in found:
        lacking = [op for op in contract if not hasattr(cls, op)]
        assert lacking == [], f"{m.id}: client class lacks {lacking}"
        assert cls.printer_type and cls.printer_type not in types_seen, f"{m.id}: printer_type missing or shared"
        types_seen.add(cls.printer_type)
        assert m.manufacturers and all(f.name for f in cls.connection_fields()), f"{m.id}: no models or unnamed connection field"


# ---- the documented tables match the catalog -------------------------------------------------------------------------------

def test_the_conformance_tables_in_the_docs_match_the_capability_catalog():
    docs = (Path(__file__).resolve().parents[3] / "docs" / "provider-interfaces.md").read_text(encoding="utf-8")
    section = docs.split("## Conformance", 1)[1]
    rows = {m.group(1): m for m in re.finditer(r"^\| `([a-z.]+)` \| (\w+) \| (.*?) \| (.*?) \|$", section, re.M)}
    assert set(rows) == set(CORE), "every core capability needs a row in the Conformance table"
    for cap, row in rows.items():
        d = CORE[cap]
        assert row.group(2) == d.mode
        documented = set(re.findall(r"`(\w+)`", row.group(3)))
        assert documented == set(required_operations(d)), f"{cap}: documented required operations differ from the contract"
        flags = set(re.findall(r"`([A-Z_]+)`", row.group(4)))
        assert flags == set(d.features), f"{cap}: documented feature flags differ from the definition"
