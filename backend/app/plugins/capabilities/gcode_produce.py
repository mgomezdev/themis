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
    def gcode_producer(self, url: str | None = None) -> Any:
        """Return the plugin's print artifact producer, or None while unconfigured."""


DEFINITION = CapabilityDef(
    id=CAPABILITY, version=1, label="Print artifact converter", mode="choose_one", protocol=SlicingPlugin,
    description="Converts a model to the file format requested by its target printer. The capability default is used when no specific rule applies.")

GCODE_PRODUCE: RoutedCapability[SlicingPlugin] = RoutedCapability(CAPABILITY)
