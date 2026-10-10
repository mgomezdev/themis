"""Routed capabilities (BIZ-250): every enabled provider serves them, a call goes only to the provider bound to its resource."""
import asyncio
import threading
from typing import Protocol, runtime_checkable

import pytest

from app import plugins
from app.plugins import PluginError
from app.plugins.capabilities.definition import CapabilityDef, RoutedCapability
from app.plugins.manifest import HOST_API, PluginManifest, Provide
from tests.plugins.dummy_plugin import DummySettings

CAP = "routed_host.ping"


@runtime_checkable
class PingProvider(Protocol):
    def ping(self, value: str) -> str: ...


PING: RoutedCapability[PingProvider] = RoutedCapability(CAP)
DEFINITION = CapabilityDef(CAP, 1, "Ping", mode="routed", protocol=PingProvider)


class FakeRouted:
    def __init__(self, settings: DummySettings) -> None:
        self.label = settings.url

    def ping(self, value: str) -> str:
        return f"{self.label}:{value}"

    async def aping(self, value: str) -> str:
        await asyncio.sleep(0)
        return f"{self.label}:async:{value}"

    def broken(self) -> str:
        raise RuntimeError("provider exploded")

    async def hang(self) -> str:
        await asyncio.sleep(60)
        return "never"

    def thread_id(self) -> int:
        return threading.get_ident()


class NotPing:                       # satisfies no part of PingProvider
    def __init__(self, settings: DummySettings) -> None:
        pass


def manifest(pid: str, *, defines: bool = False, factory=FakeRouted) -> PluginManifest:
    return PluginManifest(
        id=pid, name=pid, version="1.0.0", host_api=HOST_API, settings_model=DummySettings, factory=factory,
        provides={CAP: Provide()}, defines=(DEFINITION,) if defines else ())


@pytest.fixture(autouse=True)
def _register():
    plugins.register_plugin(manifest("routed_host", defines=True))
    plugins.register_plugin(manifest("routed_b"))
    plugins.register_plugin(manifest("routed_c"))


async def enable(host, *ids):
    for pid in ids:
        await host.update_config(pid, enabled=True, settings={"url": f"http://{pid}"})


async def test_every_enabled_provider_serves_a_routed_capability(host):
    await host.start()
    await enable(host, "routed_b", "routed_c")

    assert set(host.active_providers(CAP)) == {"routed_b", "routed_c"}
    assert host.status(CAP).state == "serving"


async def test_a_routed_capability_has_no_single_active_provider_and_cannot_be_selected(host):
    await host.start()
    await enable(host, "routed_b")

    assert host.active(CAP) is None
    assert host.selected(CAP) is None
    with pytest.raises(PluginError):
        await host.set_provider(CAP, "routed_b")


async def test_each_call_reaches_only_the_provider_bound_to_it(host):
    await host.start()
    await enable(host, "routed_b", "routed_c")

    b = await host.call_for(PING, "routed_b", lambda p: p.ping("x"))
    c = await host.call_for(PING, "routed_c", lambda p: p.ping("y"))

    assert (b.ok, b.value) == (True, "http://routed_b:x")
    assert (c.ok, c.value) == (True, "http://routed_c:y")


async def test_a_call_to_a_provider_that_is_not_active_is_refused(host):
    await host.start()
    await enable(host, "routed_b")

    r = await host.call_for(PING, "routed_c", lambda p: p.ping("x"))

    assert (r.ok, r.reason) == (False, "inactive")


async def test_disabling_one_provider_leaves_the_others_serving(host):
    await host.start()
    await enable(host, "routed_b", "routed_c")
    await host.update_config("routed_b", enabled=False)

    assert (await host.call_for(PING, "routed_c", lambda p: p.ping("z"))).value == "http://routed_c:z"
    assert (await host.call_for(PING, "routed_b", lambda p: p.ping("z"))).reason == "inactive"


async def test_a_provider_that_does_not_satisfy_the_protocol_is_rejected_and_the_others_keep_serving(host):
    plugins.register_plugin(manifest("routed_bad", factory=NotPing))
    await host.start()
    await enable(host, "routed_b", "routed_bad")

    assert host.active_for(CAP, "routed_bad") is None
    assert "PingProvider" in host._rejected["routed_bad"][CAP]
    assert host.active_providers(CAP) == ["routed_b"]


async def test_a_failing_call_is_contained_to_its_provider(host):
    await host.start()
    await enable(host, "routed_b", "routed_c")

    failed = await host.call_for(PING, "routed_b", lambda p: p.broken())
    ok = await host.call_for(PING, "routed_c", lambda p: p.ping("still fine"))

    assert (failed.ok, failed.reason) == (False, "error")
    assert "provider exploded" in failed.error
    assert ok.value == "http://routed_c:still fine"


async def test_a_hanging_call_times_out_without_raising(host):
    await host.start()
    await enable(host, "routed_b")

    r = await host.call_for(PING, "routed_b", lambda p: p.hang(), timeout=0.05)

    assert (r.ok, r.reason) == (False, "timeout")


async def test_an_async_method_is_awaited_and_a_blocking_one_runs_off_the_event_loop(host):
    await host.start()
    await enable(host, "routed_b")

    awaited = await host.call_for(PING, "routed_b", lambda p: p.aping("y"))
    thread = await host.call_for(PING, "routed_b", lambda p: p.thread_id())

    assert awaited.value == "http://routed_b:async:y"
    assert thread.value != threading.get_ident()
