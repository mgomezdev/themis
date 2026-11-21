"""Which slicing provider takes a job on a printer, and is it usable right now (BIZ-291).

The queue's claim path asks `resolve_slicing` instead of probing one global provider: the printer's preferred `gcode.produce`
plugin (`Printer.preferred_slicer_plugin_id`) or else the capability default, checked through the neutral `resolve_work`
(`providers/contract.py`, docs/architecture/laminus-contract.md §6). An explicit preference that is disabled, ineligible or not
ready BLOCKS; it never falls back to another provider. Pre-sliced files never come here (they need no provider). Blocking I/O
(`status` is an HTTP probe) runs in a worker thread."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..models import Printer
from ..plugins import get_plugin
from .slicer_profiles import MachineProfileResolution
from .print_artifacts import SUFFIXES, model_input, required_output
from .providers.contract import (
    DEFAULT_HEALTH_TIMEOUT_S, Action, ArtifactKind, Reason, Readiness, TargetPrinter, WorkItem, WorkOutcome, resolve_work,
)
from .providers.slicing import default_provider_id, enabled_provider_ids, gcode_producer_for

_BED_Z_MM = 250.0   # the printer row stores x/y only; z is not used to pick a provider


@dataclass(frozen=True)
class PrinterView:
    """The printer fields resolution reads, copied while the ORM row is still attached to its session (the slice task outlives it)."""
    name: str
    preferred_slicer_plugin_id: str | None
    bed_x_mm: float
    bed_y_mm: float
    model_uuid: str | None
    # The machine profile this printer slices with under its effective provider (services/slicer_profiles.py, BIZ-294), resolved by the
    # caller with a session; None = no mapping for its model/variant, which makes the provider ineligible (`profile_reason` says why).
    machine_profile: str | None = None
    profile_reason: str | None = None
    output_kind: ArtifactKind = ArtifactKind.GCODE
    resolved_provider_id: str | None = None
    nozzle_sizes_mm: tuple[float, ...] = ()

    @classmethod
    def of(cls, printer: Printer, resolution: MachineProfileResolution | None = None) -> "PrinterView":
        preferred = resolution.preferred_provider_id(printer) if resolution else printer.preferred_slicer_plugin_id
        return cls(printer.name, preferred, float(printer.bed_x_mm), float(printer.bed_y_mm),
                   printer.model_uuid, resolution.profile if resolution else None, resolution.reason if resolution else None,
                   required_output(printer.printer_type), resolution.provider_id if resolution else None,
                   tuple(t["nozzle_mm"] for t in (getattr(printer, "topology", None) or {}).get("tools", [])))


@dataclass(frozen=True)
class SliceDecision:
    outcome: WorkOutcome
    message: str | None = None      # the job's block reason when the outcome is BLOCKED
    # True when the provider exists but is down (unconfigured, not ready, unreachable): a cached slice may still print, and the
    # job simply waits. False for a hard block (no/dormant/ineligible provider), which the user has to resolve.
    recoverable: bool = False

    @property
    def provider_id(self) -> str | None:
        return self.outcome.provider_id

    @property
    def blocked(self) -> bool:
        return self.outcome.action is Action.BLOCKED


def _label(plugin_id: str | None) -> str:
    m = get_plugin(plugin_id) if plugin_id else None
    return m.name if m else (plugin_id or "The slicing provider")


def target_of(printer: "Printer | PrinterView") -> TargetPrinter:
    return TargetPrinter(bed_mm=(float(printer.bed_x_mm), float(printer.bed_y_mm), _BED_Z_MM), model_uuid=printer.model_uuid,
                         machine_profile=getattr(printer, "machine_profile", None),
                         nozzle_sizes_mm=getattr(printer, "nozzle_sizes_mm", ()),
                         toolheads=len(getattr(printer, "nozzle_sizes_mm", ())) or 1)


def _unavailable(printer: "Printer | PrinterView", outcome: WorkOutcome, state: Readiness | None) -> SliceDecision:
    label = _label(outcome.provider_id)
    if state is Readiness.NOT_READY:
        msg = f"{label} is not ready — slicing paused"
    elif state is Readiness.UNREACHABLE:
        msg = f"{label} is unreachable — slicing paused"
    else:
        msg = f"{label} sidecar not configured — slicing paused"
    return SliceDecision(outcome, msg, recoverable=True)


async def resolve_slicing(printer: PrinterView, filename: str, *, timeout: float = DEFAULT_HEALTH_TIMEOUT_S) -> SliceDecision:
    """Resolve the slicing provider for `filename` (an unsliced model) on `printer`. Never raises for an unusable provider.
    An enabled provider with no machine profile mapped for the printer's model/variant is INELIGIBLE (hard block, the user maps it)."""
    preferred = printer.preferred_slicer_plugin_id or None
    default = getattr(printer, "resolved_provider_id", None) or default_provider_id()
    enabled = enabled_provider_ids()
    chosen = preferred or default

    if preferred and preferred not in enabled:
        return SliceDecision(WorkOutcome(Action.BLOCKED, preferred, Reason.PREFERENCE_DORMANT),
                             f"{printer.name} is set to slice with {_label(preferred)}, which is disabled or removed — slicing paused")
    if chosen and chosen not in enabled:
        return SliceDecision(WorkOutcome(Action.BLOCKED, chosen, Reason.DEFAULT_UNAVAILABLE),
                             f"The configured converter {_label(chosen)} is disabled or removed. Check Settings → Capabilities.")
    if chosen not in enabled:
        msg = ("No enabled slicing provider is configured as the default" if enabled else "No slicing provider is enabled")
        return SliceDecision(WorkOutcome(Action.BLOCKED, reason=Reason.NO_PROVIDER), f"{msg} — slicing paused")
    if printer.machine_profile is None:    # BIZ-294: no machine profile for this model/variant
        reason = Reason.PREFERENCE_INELIGIBLE if preferred else Reason.DEFAULT_INELIGIBLE
        return SliceDecision(WorkOutcome(Action.BLOCKED, chosen, reason),
                             f"{printer.profile_reason or 'no machine profile for ' + printer.name} — slicing paused", recoverable=False)

    producer = gcode_producer_for(chosen)
    if producer is None:
        reason = Reason.PREFERENCE_UNAVAILABLE if preferred else Reason.DEFAULT_UNAVAILABLE
        return _unavailable(printer, WorkOutcome(Action.BLOCKED, chosen, reason), None)

    input_kind = model_input(filename)
    output_kind = getattr(printer, "output_kind", ArtifactKind.GCODE)
    if input_kind not in producer.accepted_inputs or output_kind not in producer.output_kinds:
        reason = Reason.PREFERENCE_INELIGIBLE if preferred else Reason.DEFAULT_INELIGIBLE
        return SliceDecision(WorkOutcome(Action.BLOCKED, chosen, reason),
                             f"{_label(chosen)} can't convert this file to {SUFFIXES[output_kind]} for {printer.name}")
    status = await asyncio.to_thread(producer.status, timeout)
    outcome = resolve_work(WorkItem(input_kind, output_kind=output_kind), target_of(printer), {chosen: producer}, {chosen: status},
                           preferred=preferred, default=default)
    if outcome.action is Action.PRODUCE:
        return SliceDecision(outcome)
    r = outcome.reason
    if r in (Reason.PREFERENCE_UNAVAILABLE, Reason.DEFAULT_UNAVAILABLE):
        return _unavailable(printer, outcome, status.state)
    label = _label(chosen)
    msg = (f"{label} can't slice for {printer.name} with machine profile {printer.machine_profile!r} — Refresh the profile "
           f"sync, and re-map the printer model if that profile was renamed or removed — slicing paused")
    return SliceDecision(outcome, msg)
