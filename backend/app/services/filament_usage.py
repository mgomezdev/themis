"""Filament used by one physical print attempt, per loaded slot (BIZ-366 v2).

The printer plugin owns the facts: it reports the cumulative grams each of ITS loaded slots fed during the attempt (an AMS rollover
onto a backup spool is simply a second slot). Themis owns the slot -> spool bindings: the attempt's start context says which spool
was in each slot. With no report, a completed print falls back to the slicer's estimate routed through the claim-time slot plan;
an attempt that ended any other way (failed, cancelled, requeued) deducts nothing without a report.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_REPORT_SLOTS = 64


class SlotUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    slot: int = Field(ge=0)            # the plugin's published slot id (`loaded_filaments[].slot`), not a list position
    grams: float = Field(ge=0)


class UsageReport(BaseModel):
    """A plugin's cumulative usage for exactly one Themis physical print attempt (`filament_usage_context.attempt_id`)."""
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    version: Literal[2]
    attempt_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    final: bool
    source: Literal["reported", "measured"]
    slots: list[SlotUsage] = Field(max_length=MAX_REPORT_SLOTS)

    @model_validator(mode="after")
    def unique_slots(self):
        if len({s.slot for s in self.slots}) != len(self.slots):
            raise ValueError("a slot appears more than once")
        return self

    @property
    def grams(self) -> float:
        return sum(s.grams for s in self.slots)


ENDINGS = ("complete", "failed", "cancelled", "requeued")


@dataclass
class Allocation:
    """What one attempt used: `slots` = [(the loaded-slot dict it fed from, grams)], with the source and any note."""
    slots: list[tuple[dict, float]] = field(default_factory=list)
    source: str = "estimated"           # reported | measured | estimated | none
    reason: str | None = None

    @property
    def grams(self) -> float:
        return sum(g for _, g in self.slots)


def _slot_by_id(loaded: list | None, slot_id: int) -> dict | None:
    for s in loaded or []:
        if isinstance(s, dict) and s.get("slot") == slot_id and not s.get("empty"):
            return s
    return None


def allocate(context: dict, *, ended: str, report: dict | None, end_loaded: list | None,
             estimate_grams: float | None, estimate_breakdown: list | None) -> Allocation:
    """The attempt's usage per loaded slot. A report for this attempt wins; each of its slots is the one loaded at the attempt's start
    (else at its end: a spool loaded mid-print). Without one, only a completed print falls back to the slicer estimate."""
    if report is not None:
        try:
            parsed = UsageReport.model_validate(report)
        except ValidationError:
            return Allocation(source="none", reason="The printer's filament usage report was invalid, so nothing was deducted")
        if parsed.attempt_id == context["attempt_id"]:      # another attempt's report counts as none sent for this one
            out, missing = [], []
            for s in parsed.slots:
                if s.grams == 0:
                    continue
                slot = _slot_by_id(context.get("loaded_filaments"), s.slot) or _slot_by_id(end_loaded, s.slot)
                if slot is None:
                    missing.append(str(s.slot))
                else:
                    out.append((slot, float(s.grams)))
            reason = (f"The printer reported usage for slot {', '.join(missing)}, which held no filament on record, "
                      "so that part was not deducted") if missing else None
            return Allocation(out, parsed.source, reason)
    if ended != "complete":
        return Allocation(source="none", reason=f"The printer reported no filament usage for this {ended} print, so nothing was deducted")
    return _estimate(context, estimate_grams, estimate_breakdown)


def _estimate(context: dict, grams, breakdown: list | None) -> Allocation:
    """The slicer estimate through the claim-time slot plan (slicer filament index N = model filament N+1)."""
    if type(grams) not in (int, float) or not math.isfinite(grams) or grams < 0:
        return Allocation(source="none", reason="No filament usage was reported or estimated for this print")
    loaded = list(context.get("loaded_filaments") or [])
    plan = context.get("slot_plan") or []
    planned = list(dict.fromkeys(u["slot"] for u in plan))
    at = lambda i: loaded[i] if 0 <= i < len(loaded) and not loaded[i].get("empty") else None      # noqa: E731
    if grams == 0:
        return Allocation()
    if len(planned) == 1:
        slot = at(planned[0])
        return Allocation([(slot, float(grams))]) if slot else Allocation(reason="The printed slot is no longer on record")
    if not breakdown:
        return Allocation(reason="The filament used per slot was not estimated, so no spool was tracked")
    routing = {int(u["model_filament"] or 1) - 1: u["slot"] for u in plan}
    by_index: dict[int, float] = {}
    for entry in breakdown:
        index, used = entry.get("extruder_index"), entry.get("grams")
        if type(index) is not int or type(used) not in (int, float) or not math.isfinite(used) or used < 0:
            return Allocation(reason="Invalid filament usage estimate")
        if used == 0:
            continue
        target = routing.get(index)
        if target is None or at(target) is None:
            return Allocation(reason=f"Estimated filament {index + 1} has no planned slot")
        by_index[target] = by_index.get(target, 0.0) + float(used)
    return Allocation([(at(i), g) for i, g in by_index.items()])
