"""`gcode.produce` — a choose-one capability (BIZ-250/BIZ-290): core picks one provider per operation (capability default = the
selection row; the host auto-selects the unambiguous case at load, and `slicing.default_provider_id` falls back to the sole enabled
provider before the host has loaded). Core names no slicer: it asks the host
which plugin serves this capability and takes that plugin's slicing adapter. The neutral `GcodeProducer` contract and `resolve_work`
(per-printer preference, readiness) are `services/providers/contract.py`; the queue claim uses them through
`services/slice_resolution.py` (BIZ-291)."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .definition import CapabilityDef, RoutedCapability

CAPABILITY = "gcode.produce"


@runtime_checkable
class SlicingPlugin(Protocol):
    def slicing_provider(self, url: str | None = None) -> Any:
        """The plugin's `SlicingProvider` adapter carrying its connection options; None while the plugin is unconfigured."""

    def gcode_producer(self, url: str | None = None) -> Any:
        """The plugin's neutral `GcodeProducer` (identity, `status`, `supports_target`) over that adapter; None while unconfigured."""


DEFINITION = CapabilityDef(
    id=CAPABILITY, version=1, label="G-code producer", mode="choose_one", protocol=SlicingPlugin,
    description="Slices a model into G-code for a target printer (Laminus today); one enabled provider is the default.")

GCODE_PRODUCE: RoutedCapability[SlicingPlugin] = RoutedCapability(CAPABILITY)
