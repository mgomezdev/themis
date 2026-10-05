"""Access to the active inventory provider, by capability. The only place core reaches the plugin host for inventory."""
from __future__ import annotations

import logging

from ...plugins.host import CallResult, plugin_host
from ...plugins.kinds.filament_inventory import KIND, FilamentInventoryProvider

logger = logging.getLogger("app")


class CapabilityUnavailable(Exception):
    """A route/feature needs a provider or capability that is not available (HTTP 409 `capability_unavailable`)."""

    def __init__(self, capability: str | None = None) -> None:
        super().__init__(f"capability_unavailable: {capability or KIND}")
        self.kind, self.capability = KIND, capability


def provider_id() -> str | None:
    a = plugin_host.active(KIND)
    return a.manifest.id if a else None


def active_provider() -> FilamentInventoryProvider | None:
    a = plugin_host.active(KIND)
    return a.instance if a else None


def has(capability: str) -> bool:
    return plugin_host.has(KIND, capability)


def require(capability: str | None = None) -> str:
    """The active provider's id, or raise `CapabilityUnavailable` (no provider / lacks `capability`)."""
    pid = provider_id()
    if pid is None or (capability is not None and not has(capability)):
        raise CapabilityUnavailable(capability)
    return pid


async def call(method: str, *args, timeout: float | None = None, **kwargs) -> CallResult:
    """A contained call to the active provider (never raises; see `PluginHost.call`)."""
    if timeout is None:
        return await plugin_host.call(KIND, method, *args, **kwargs)
    return await plugin_host.call(KIND, method, *args, timeout=timeout, **kwargs)


def describe_failure(result: CallResult) -> tuple[str, str]:
    """(error_code, message) for a failed call, as the sync status shows them."""
    pid = provider_id()
    exc = result.exception
    code = getattr(exc, "code", None) or (type(exc).__name__ if exc is not None else result.reason)
    text = str(exc) if exc is not None else (result.error or "")
    return str(code), (plugin_host.redact(pid, text) if pid else text)
