"""Reusable conformance pattern for capability providers (BIZ-301; docs/provider-interfaces.md "Conformance").

What every capability shares is the *host-side* contract, so that is what is checked here, once, for every core capability:
declaration (contract version, feature vocabulary, required operations), neutral failure and timeout outcomes, secret-safe
diagnostics, and lifecycle registration. What genuinely differs per capability (the meaning of each operation) stays in that
capability's own suite (`test_filament_inventory_contract.py`, `test_routed_capability.py`, the notify/slicing/printer suites).

To put a new capability under the shared checks, add a `Kit` to `KITS`: a factory for a well-behaved provider and the name of one
operation to exercise. Nothing here knows a vendor; every provider is a fake registered at runtime, with no edit to core."""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from app.plugins.capabilities import CORE
from app.plugins.capabilities.definition import CapabilityDef, required_operations
from app.plugins.manifest import HOST_API, PluginManifest, Provide

SECRET = "s3cr3t-conformance-token"
BAD_FEATURE = "NOT_A_FEATURE_OF_ANY_CAPABILITY"


class ConfSettings(BaseModel):
    url: str = ""
    token: str | None = None


def stub_class(d: CapabilityDef, *, omit: str | None = None) -> type:
    """A provider class with every required operation of `d` (taken from its Protocol: properties stay properties, coroutine
    functions stay coroutine functions) and nothing else, optionally leaving one out."""
    ns: dict[str, Any] = {}
    for name in required_operations(d):
        if name == omit:
            continue
        member = inspect.getattr_static(d.protocol, name, None) if d.protocol is not None else None
        if isinstance(member, property):
            ns[name] = property(lambda self: False)
        elif inspect.iscoroutinefunction(member):
            async def _async(self, *a, **k): return None
            ns[name] = _async
        else:
            def _sync(self, *a, **k): return None
            ns[name] = _sync
    return type(f"Stub_{d.id.replace('.', '_')}", (), ns)


@dataclass(frozen=True)
class Kit:
    capability: str
    operation: str                      # one operation exercised through the host (a method, async or not)
    valid: Callable[[], Any]            # a well-behaved provider instance

    @property
    def definition(self) -> CapabilityDef:
        return CORE[self.capability]

    @property
    def exclusive(self) -> bool:
        return self.definition.mode == "exclusive"

    def id(self) -> str:
        return self.capability


def _inventory() -> Any:
    from tests.fake_providers import FakeInventoryProvider
    return FakeInventoryProvider()


KITS: tuple[Kit, ...] = (
    Kit("inventory.filament", "list_materials", _inventory),
    Kit("printer.client", "request_status_update", lambda: stub_class(CORE["printer.client"])()),
    Kit("notify.channel", "test_connection", lambda: stub_class(CORE["notify.channel"])()),
    Kit("gcode.produce", "gcode_producer", lambda: stub_class(CORE["gcode.produce"])()),
)


# ---- flavours: the provider behaviours the host must contain ---------------------------------------------------------------

def _replace_operation(kit: Kit, instance: Any, behaviour: Callable[[], Any]) -> Any:
    """Shadow the operation on the instance, keeping its sync/async nature so the host dispatches it the same way."""
    original = getattr(instance, kit.operation)
    if inspect.iscoroutinefunction(original):
        async def op(*a, **k): return behaviour()
    else:
        def op(*a, **k): return behaviour()
    setattr(instance, kit.operation, op)
    return instance


def _failing(kit: Kit, settings: ConfSettings) -> Any:
    def boom():
        raise RuntimeError(f"upstream rejected credentials token={settings.token} (internal path /srv/app/secret.py)")
    return _replace_operation(kit, kit.valid(), boom)


def _hanging(kit: Kit, settings: ConfSettings) -> Any:
    instance = kit.valid()
    original = getattr(instance, kit.operation)
    if inspect.iscoroutinefunction(original):
        async def op(*a, **k): await asyncio.sleep(60)
    else:
        def op(*a, **k): time.sleep(0.4)                       # a blocked worker thread cannot be cancelled: keep it short
    setattr(instance, kit.operation, op)
    return instance


def missing_operation(kit: Kit) -> str:
    """The operation an `incomplete` provider lacks (the last required one, alphabetically)."""
    return required_operations(kit.definition)[-1]


def _incomplete(kit: Kit) -> Any:
    base = type(kit.valid())
    ns = {n: inspect.getattr_static(base, n) for n in required_operations(kit.definition) if n != missing_operation(kit)}
    return type("Incomplete", (), ns)()


def provider_manifest(kit: Kit, plugin_id: str, flavour: str = "valid", *, version: int | None = None,
                      features: frozenset[str] = frozenset()) -> PluginManifest:
    """A fake plugin providing `kit.capability` in the given flavour (valid | failing | hanging | incomplete)."""

    def factory(settings: ConfSettings) -> Any:
        if flavour == "failing":
            return _failing(kit, settings)
        if flavour == "hanging":
            return _hanging(kit, settings)
        if flavour == "incomplete":
            return _incomplete(kit)
        return kit.valid()

    return PluginManifest(
        id=plugin_id, name=plugin_id, version="1.0.0", host_api=HOST_API, settings_model=ConfSettings,
        secret_fields=frozenset({"token"}), factory=factory,
        provides={kit.capability: Provide(version=version or kit.definition.version, features=features)})


# ---- driving a provider through the host, whatever the capability's mode -------------------------------------------------

async def enable(host, kit: Kit, plugin_id: str) -> None:
    await host.update_config(plugin_id, enabled=True, settings={"url": "http://conf.test"}, secrets={"token": SECRET})
    if kit.exclusive:
        await host.set_provider(kit.capability, plugin_id)


def serving(host, kit: Kit, plugin_id: str) -> bool:
    if kit.exclusive:
        active = host.active(kit.capability)
        return active is not None and active.manifest.id == plugin_id
    return host.active_for(kit.capability, plugin_id) is not None


async def invoke(host, kit: Kit, plugin_id: str, *, timeout: float = 5.0):
    """Call the kit's operation on `plugin_id` the way core would for this capability's mode; never raises."""
    if kit.exclusive:
        return await host.call(kit.capability, kit.operation, timeout=timeout)
    return await host.call_for(kit.capability, plugin_id, lambda p: getattr(p, kit.operation)(), timeout=timeout)


def printer_job_field_gaps(manifest: PluginManifest) -> list[str]:
    """Printer-plugin conformance (docs/printer-interface.md): the minimum job fields (`print_start_time`, `print_end_time`, `result`) each
    declared in `job_fields` or listed in `unsupported_job_fields`. Returns the missing ones; checked, never enforced at load."""
    from app.plugins.job_fields import missing_min_fields
    return missing_min_fields(manifest)
