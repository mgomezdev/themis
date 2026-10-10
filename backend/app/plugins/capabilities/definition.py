"""A capability: a named, versioned service contract that one plugin at a time serves (design spec §1), or — for `routed`
capabilities — that every enabled provider serves for the resources bound to it (BIZ-250)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

CAP_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

P = TypeVar("P")

# exclusive: one provider serves the whole capability (inventory). routed: every enabled provider serves it; a call goes to
# the provider bound to its resource. choose_one: every enabled provider is built, one eligible provider is picked per
# operation (per-resource preference -> capability default -> blocked). fan_out: every enabled provider receives each event.
Mode = Literal["exclusive", "routed", "choose_one", "fan_out"]
BUILDS_ALL: frozenset[str] = frozenset({"routed", "choose_one", "fan_out"})    # modes where every enabled provider is built


@dataclass(frozen=True)
class CapabilityDef:
    id: str
    version: int
    label: str
    description: str = ""
    # Duck-typing check for plugin-defined contracts: these async methods must exist on the serving part (verified when the
    # instance is built). Core capabilities with an ABC leave this empty.
    required_methods: tuple[str, ...] = ()
    features: frozenset[str] = frozenset()     # the feature flags a provider may declare for this capability
    # See `Mode`. `routed` and `fan_out` have no selection row; `choose_one` uses the selection row as the capability default.
    mode: Mode = "exclusive"
    # Typing contract: a `runtime_checkable` Protocol the serving part must satisfy (checked when the instance is built).
    protocol: type | None = None


@dataclass(frozen=True)
class RoutedCapability(Generic[P]):
    """Typed handle for a routed capability: `host.call_for(HANDLE, plugin_id, lambda p: p.method(...))` type-checks `p` as P."""
    id: str
