"""A capability: a named, versioned service contract that one plugin at a time serves (design spec §1)."""
from __future__ import annotations

import re
from dataclasses import dataclass

CAP_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


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
