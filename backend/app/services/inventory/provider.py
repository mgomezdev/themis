"""Access to the active inventory provider, by capability. The only place core reaches the plugin host for inventory."""
from __future__ import annotations

import logging

from ...plugins.host import CallResult, plugin_host
from ...plugins.capabilities.filament_inventory import CAPABILITY, FilamentInventoryProvider

logger = logging.getLogger("app")


class CapabilityUnavailable(Exception):
    """A route/feature needs a provider or feature that is not available (HTTP 409 `capability_unavailable`)."""

    def __init__(self, feature: str | None = None) -> None:
        super().__init__(f"capability_unavailable: {feature or CAPABILITY}")
        self.capability_id, self.feature = CAPABILITY, feature


def provider_id() -> str | None:
    a = plugin_host.active(CAPABILITY)
    return a.manifest.id if a else None


def active_provider() -> FilamentInventoryProvider | None:
    return plugin_host.part(CAPABILITY)


def has(feature: str) -> bool:
    return plugin_host.has(CAPABILITY, feature)


def require(feature: str | None = None) -> str:
    """The active provider's id, or raise `CapabilityUnavailable` (no provider / lacks `feature`)."""
    pid = provider_id()
    if pid is None or (feature is not None and not has(feature)):
        raise CapabilityUnavailable(feature)
    return pid


async def call(method: str, *args, timeout: float | None = None, **kwargs) -> CallResult:
    """A contained call to the active provider (never raises; see `PluginHost.call`)."""
    if timeout is None:
        return await plugin_host.call(CAPABILITY, method, *args, **kwargs)
    return await plugin_host.call(CAPABILITY, method, *args, timeout=timeout, **kwargs)


def describe_failure(result: CallResult) -> tuple[str, str]:
    """(error_code, message) for a failed call, as the sync status shows them."""
    pid = provider_id()
    exc = result.exception
    code = getattr(exc, "code", None) or (type(exc).__name__ if exc is not None else result.reason)
    text = str(exc) if exc is not None else (result.error or "")
    return str(code), (plugin_host.redact(pid, text) if pid else text)
