"""A capability: a named, versioned service contract that one plugin at a time serves (design spec §1), or — for `routed`
capabilities — that every enabled provider serves for the resources bound to it (BIZ-250)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

# Dotted lowercase segments; a segment may start with a digit ("3mf.construct", the contract name from BIZ-248).
CAP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*(\.[a-z0-9][a-z0-9_]*)+$")

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


def required_operations(d: CapabilityDef) -> tuple[str, ...]:
    """Every operation a provider of `d` must have: the duck-typed `required_methods` plus the members of its `protocol`
    (sorted). This is the "required" column of the conformance tables in docs/provider-interfaces.md; optional operations are
    the ones a feature flag gates (`features`) and are never listed here."""
    import typing
    members: set[str] = set()
    if d.protocol is not None:
        if hasattr(typing, "get_protocol_members"):                       # Python 3.13+
            members = set(typing.get_protocol_members(d.protocol))
        else:                                                             # 3.11 / 3.12
            members = set(getattr(d.protocol, "__protocol_attrs__", None) or typing._get_protocol_attrs(d.protocol))  # type: ignore[attr-defined]
    return tuple(sorted(members | set(d.required_methods)))
