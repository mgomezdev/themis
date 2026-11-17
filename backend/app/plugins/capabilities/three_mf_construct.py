"""`3mf.construct` — an exclusive capability (BIZ-296): the one active provider arranges loose models into a 3MF project for a
target bed (project generation). The neutral `ProjectConstructor` contract and `resolve_construct` are
`services/providers/contract.py`; core reaches the provider through `services/providers/construction.py`."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .definition import CapabilityDef

CAPABILITY = "3mf.construct"


@runtime_checkable
class ConstructPlugin(Protocol):
    def project_constructor(self, url: str | None = None) -> Any:
        """The plugin's neutral `ProjectConstructor` carrying its connection options; None while the plugin is unconfigured."""


DEFINITION = CapabilityDef(
    id=CAPABILITY, version=1, label="Project builder", mode="exclusive", protocol=ConstructPlugin,
    description="Arranges loose models onto the plates of a 3MF project for a bed size (project generation).")
