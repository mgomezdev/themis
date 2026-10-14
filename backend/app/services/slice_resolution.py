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
from .providers.contract import (
    DEFAULT_HEALTH_TIMEOUT_S, Action, InputKind, Reason, Readiness, TargetPrinter, WorkItem, WorkOutcome, resolve_work,
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

    @classmethod
    def of(cls, printer: Printer) -> "PrinterView":
        return cls(printer.name, printer.preferred_slicer_plugin_id, float(printer.bed_x_mm), float(printer.bed_y_mm),
                   printer.model_uuid)


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


def _input_kind(filename: str) -> InputKind:
    return InputKind.STL if (filename or "").lower().endswith(".stl") else InputKind.MODEL_3MF


def target_of(printer: "Printer | PrinterView") -> TargetPrinter:
    return TargetPrinter(bed_mm=(float(printer.bed_x_mm), float(printer.bed_y_mm), _BED_Z_MM), model_uuid=printer.model_uuid)


def _unavailable(printer: "Printer | PrinterView", outcome: WorkOutcome, state: Readiness | None) -> SliceDecision:
    label = _label(outcome.provider_id)
    if state is Readiness.NOT_READY:
        msg = f"{label} is not ready — slicing paused"
    elif state is Readiness.UNREACHABLE:
        msg = f"{label} is unreachable — slicing paused"
    else:
        msg = f"{label} sidecar not configured — slicing paused"
    return SliceDecision(outcome, msg, recoverable=True)


async def resolve_slicing(printer: "Printer | PrinterView", filename: str, *, timeout: float = DEFAULT_HEALTH_TIMEOUT_S) -> SliceDecision:
    """Resolve the slicing provider for `filename` (an unsliced model) on `printer`. Never raises for an unusable provider."""
    preferred = printer.preferred_slicer_plugin_id or None
    default = default_provider_id()
    enabled = enabled_provider_ids()
    producers = {pid: p for pid in enabled if (p := gcode_producer_for(pid)) is not None}   # unconfigured ones are absent
    chosen = preferred or default

    if chosen in enabled and chosen not in producers:    # enabled but no URL / invalid settings: down, not missing
        reason = Reason.PREFERENCE_UNAVAILABLE if preferred else Reason.DEFAULT_UNAVAILABLE
        return _unavailable(printer, WorkOutcome(Action.BLOCKED, chosen, reason), None)

    statuses = {}
    if chosen in producers:                              # probe only the provider this printer would use
        statuses[chosen] = await asyncio.to_thread(producers[chosen].status, timeout)
    outcome = resolve_work(WorkItem(_input_kind(filename)), target_of(printer), producers, statuses,
                           preferred=preferred, default=default)
    if outcome.action is Action.PRODUCE:
        return SliceDecision(outcome)
    r = outcome.reason
    if r in (Reason.PREFERENCE_UNAVAILABLE, Reason.DEFAULT_UNAVAILABLE):
        st = statuses.get(outcome.provider_id)
        return _unavailable(printer, outcome, st.state if st else None)
    if r is Reason.PREFERENCE_DORMANT:
        msg = f"{printer.name} is set to slice with {_label(outcome.provider_id)}, which is disabled or removed — slicing paused"
    elif r in (Reason.PREFERENCE_INELIGIBLE, Reason.DEFAULT_INELIGIBLE):
        msg = f"{_label(outcome.provider_id)} can't slice for {printer.name} — slicing paused"
    elif not enabled:
        msg = "No slicing provider is enabled — slicing paused"
    else:
        msg = "Several slicing providers are enabled and none is the default — slicing paused"
    return SliceDecision(outcome, msg, recoverable=False)
