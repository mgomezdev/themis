"""Capability resolution modes (BIZ-250): exclusive / routed / choose-one / fan-out each enforce their cardinality and dispatch policy."""
import asyncio
from typing import Protocol, runtime_checkable

import pytest

from app import plugins
from app.plugins import PluginError
from app.plugins.capabilities.definition import CapabilityDef, RoutedCapability
from app.plugins.manifest import HOST_API, PluginManifest, Provide
from tests.plugins.dummy_plugin import DummySettings

EXCL, CHOOSE, FAN = "modes.excl", "modes.choose", "modes.fan"


@runtime_checkable
class Ping(Protocol):
    def ping(self, value: str) -> str: ...


H_EXCL: RoutedCapability[Ping] = RoutedCapability(EXCL)
H_CHOOSE: RoutedCapability[Ping] = RoutedCapability(CHOOSE)
H_FAN: RoutedCapability[Ping] = RoutedCapability(FAN)
DEFS = (CapabilityDef(EXCL, 1, "Excl", mode="exclusive"), CapabilityDef(CHOOSE, 1, "Choose", mode="choose_one", protocol=Ping),
        CapabilityDef(FAN, 1, "Fan", mode="fan_out", protocol=Ping))


class Fake:
    def __init__(self, settings: DummySettings) -> None:
        self.label = settings.url

    def ping(self, value: str) -> str:
        return f"{self.label}:{value}"


class Broken(Fake):
    def ping(self, value: str) -> str:
        raise RuntimeError("boom")


class Slow(Fake):
    async def ping(self, value: str) -> str:      # type: ignore[override]
        await asyncio.sleep(60)
        return "never"


def manifest(pid: str, factory=Fake, *, defines: bool = False) -> PluginManifest:
    return PluginManifest(id=pid, name=pid, version="1.0.0", host_api=HOST_API, settings_model=DummySettings, factory=factory,
                          provides={c: Provide() for c in (EXCL, CHOOSE, FAN)}, defines=DEFS if defines else ())


@pytest.fixture(autouse=True)
def _register():
    plugins.register_plugin(manifest("modes", defines=True))
    for pid in ("p_a", "p_b"):
        plugins.register_plugin(manifest(pid))
    plugins.register_plugin(manifest("p_broken", Broken))
    plugins.register_plugin(manifest("p_slow", Slow))
    plugins.register_plugin(manifest("p_slow2", Slow))


async def enable(host, *ids):
    for pid in ids:
        await host.update_config(pid, enabled=True, settings={"url": pid})


# --- exclusive -------------------------------------------------------------------------------------------------

async def test_exclusive_serves_exactly_one_provider_and_a_second_selection_replaces_it(host):
    await host.start()
    await enable(host, "p_a", "p_b")
    await host.set_provider(EXCL, "p_a")
    await host.set_provider(EXCL, "p_b")

    assert host.active(EXCL).manifest.id == "p_b"
    assert host.selected(EXCL) == "p_b"
    assert host.active_for(EXCL, "p_a") is None and host.active_providers(EXCL) == []     # exclusive never builds the others for it
    assert host.resolve(EXCL).outcome == "not_choose_one"
    assert await host.fan_out(H_EXCL, lambda p: p.ping("e")) == {}


# --- choose-one -----------------------------------------------------------------------------------------------

async def test_choose_one_uses_the_capability_default_when_a_resource_has_no_preference(host):
    await host.start()
    await enable(host, "p_a", "p_b")
    await host.set_provider(CHOOSE, "p_a")

    r = host.resolve(CHOOSE)
    out = await host.call_choose(H_CHOOSE, lambda p: p.ping("x"))

    assert (r.plugin_id, r.outcome) == ("p_a", "default")
    assert out.value == "p_a:x"


async def test_choose_one_valid_preference_overrides_the_default(host):
    await host.start()
    await enable(host, "p_a", "p_b")
    await host.set_provider(CHOOSE, "p_a")

    out = await host.call_choose(H_CHOOSE, lambda p: p.ping("x"), preferred="p_b")

    assert out.value == "p_b:x"
    assert host.resolve(CHOOSE, preferred="p_b").outcome == "preferred"


async def test_choose_one_rejects_an_ineligible_preference_without_falling_back(host):
    await host.start()
    await enable(host, "p_a", "p_b")
    await host.set_provider(CHOOSE, "p_a")

    r = host.resolve(CHOOSE, preferred="p_b", eligible=lambda pid: pid == "p_a")
    out = await host.call_choose(H_CHOOSE, lambda p: p.ping("x"), preferred="p_b", eligible=lambda pid: pid == "p_a")

    assert (r.plugin_id, r.outcome) == (None, "preference_ineligible")
    assert (out.ok, out.reason) == (False, "inactive")


async def test_choose_one_blocks_when_the_preferred_provider_is_disabled_instead_of_picking_another(host):
    await host.start()
    await enable(host, "p_a", "p_b")
    await host.set_provider(CHOOSE, "p_a")
    await host.update_config("p_b", enabled=False)

    r = host.resolve(CHOOSE, preferred="p_b")

    assert (r.plugin_id, r.outcome) == (None, "preference_dormant")


async def test_choose_one_blocks_when_the_default_is_disabled_or_ineligible(host):
    await host.start()
    await enable(host, "p_a", "p_b")
    await host.set_provider(CHOOSE, "p_a")

    assert host.resolve(CHOOSE, eligible=lambda pid: False).outcome == "default_ineligible"
    await host.update_config("p_a", enabled=False)
    assert host.resolve(CHOOSE).outcome == "default_unavailable"


async def test_choose_one_without_any_default_is_an_explicit_no_provider_outcome(host):
    await host.start()
    await enable(host, "p_a", "p_b")
    await host.set_provider(CHOOSE, None)           # an explicit "no default" (a lone enabled provider is otherwise auto-selected)

    assert host.resolve(CHOOSE).outcome == "no_provider"


async def test_selection_ui_offers_only_eligible_providers(host):
    await host.start()
    await enable(host, "p_a", "p_b")

    assert host.eligible_providers(CHOOSE, lambda pid: pid != "p_b") == ["p_a"]


async def test_resolve_refuses_other_modes(host):
    await host.start()

    assert host.resolve(EXCL).outcome == "not_choose_one"


# --- fan-out --------------------------------------------------------------------------------------------------

async def test_fan_out_reaches_every_provider_and_one_failure_or_timeout_does_not_block_the_rest(host):
    await host.start()
    await enable(host, "p_a", "p_b", "p_broken", "p_slow")

    out = await host.fan_out(H_FAN, lambda p: p.ping("e"), timeout=0.2)

    assert {k: v.ok for k, v in out.items()} == {"p_a": True, "p_b": True, "p_broken": False, "p_slow": False}
    assert out["p_a"].value == "p_a:e" and out["p_b"].value == "p_b:e"
    assert out["p_broken"].reason == "error" and out["p_slow"].reason == "timeout"


async def test_fan_out_cannot_be_given_a_single_provider(host):
    await host.start()
    await enable(host, "p_a")

    with pytest.raises(PluginError):
        await host.set_provider(FAN, "p_a")
    assert host.active(FAN) is None


async def test_fan_out_skips_disabled_providers_and_is_empty_with_none(host):
    await host.start()
    assert await host.fan_out(H_FAN, lambda p: p.ping("e")) == {}
    await enable(host, "p_a", "p_b")
    await host.update_config("p_b", enabled=False)

    assert list(await host.fan_out(H_FAN, lambda p: p.ping("e"))) == ["p_a"]


async def test_fan_out_calls_providers_concurrently(host):
    await host.start()
    await enable(host, "p_slow", "p_slow2")

    t = asyncio.get_running_loop().time()
    await host.fan_out(H_FAN, lambda p: p.ping("e"), timeout=0.3)

    assert asyncio.get_running_loop().time() - t < 0.5            # sequential would take ~0.6s


async def test_fan_out_only_applies_to_fan_out_capabilities(host):
    await host.start()
    await enable(host, "p_a")

    assert await host.fan_out(H_CHOOSE, lambda p: p.ping("e")) == {}
