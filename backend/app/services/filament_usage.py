"""Translate explicitly indexed consumption into physical slots, without guessing rollover quantities.

The printer plugin owns the facts. Themis owns the saved route and slot/spool bindings.
All indices here are zero-based; slicer filament index N refers to model_filament N+1.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class UsageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    index: int = Field(ge=0)
    grams: float = Field(ge=0)


class UsageReport(BaseModel):
    """A plugin's cumulative report for exactly one Themis physical print attempt.

    A complete physical-slot report attributes consumption to the spools captured at start.
    Same-slot spool replacement cannot be expressed by v1: report complete=False for reconciliation.
    A tray switch without a consumption reading is never a measured usage report.
    """
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    version: Literal[1]
    attempt_id: str = Field(min_length=1)
    report_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    basis: Literal["physical_slot", "slicer_filament"]
    source: Literal["reported", "measured", "estimated"]
    final: bool
    complete: bool
    rollover: bool = False
    grams: float = Field(ge=0)
    breakdown: list[UsageEntry]

    @model_validator(mode="after")
    def totals_agree(self):
        if len({e.index for e in self.breakdown}) != len(self.breakdown):
            raise ValueError("duplicate consumption index")
        if self.complete and not math.isclose(sum(e.grams for e in self.breakdown), self.grams, rel_tol=0.001, abs_tol=0.05):
            raise ValueError("complete breakdown does not add up to the total grams")
        return self


@dataclass
class Allocation:
    by_slot: dict[int, float] = field(default_factory=dict)
    source: str = "estimated"
    reason: str | None = None


def allocate(context: dict, grams: float | None, breakdown: list | None, report: dict | None = None) -> Allocation:
    """Normalize a final report or the sliced estimate using the immutable print-start route.

    A present but unusable report blocks fallback: partial telemetry / a rollover must not
    silently charge the starting spool for consumption that cannot be attributed.
    """
    basis = context["basis"]
    source = "estimated"
    if report is not None:
        try:
            parsed = UsageReport.model_validate(report)
        except ValidationError:
            return Allocation(reason="Invalid printer filament usage report; reconcile spool usage")
        source = parsed.source
        if parsed.attempt_id != context["attempt_id"]:
            return Allocation(source=source, reason="Filament usage report belongs to another print attempt")
        if not parsed.final or not parsed.complete:
            return Allocation(source=source, reason="Printer filament usage is partial; reconcile spool usage")
        if parsed.rollover and (parsed.basis != "physical_slot" or source == "estimated"):
            return Allocation(source=source, reason="Spool rollover was reported without attributable physical-slot consumption")
        grams, basis = parsed.grams, parsed.basis
        breakdown = [{"extruder_index": e.index, "grams": e.grams} for e in parsed.breakdown]
    if type(grams) not in (int, float) or not math.isfinite(grams) or grams < 0:
        return Allocation(source=source, reason="No valid filament consumption total was reported")
    plan = context["slot_plan"]
    planned = list(dict.fromkeys(u["slot"] for u in plan))
    if report is None and len(planned) == 1:
        return Allocation({planned[0]: float(grams)}, source)
    if grams == 0 and not breakdown:
        return Allocation(source=source)
    if not breakdown:
        return Allocation(source=source, reason="The filament used per slot was not reported, so no spool was tracked")
    routing = {int(u["model_filament"] or 1) - 1: u["slot"] for u in plan}
    by_slot: dict[int, float] = {}
    seen = set()
    for entry in breakdown:
        index, used = entry.get("extruder_index"), entry.get("grams")
        if (type(index) is not int or index < 0 or index in seen or type(used) not in (int, float)
                or not math.isfinite(used) or used < 0):
            return Allocation(source=source, reason="Invalid filament consumption breakdown")
        seen.add(index)
        if used == 0:
            continue
        slot = index if basis == "physical_slot" else routing.get(index)
        if slot is None or not 0 <= slot < len(context["loaded_filaments"]) or context["loaded_filaments"][slot].get("empty"):
            return Allocation(source=source, reason=f"Consumption index {index} has no saved physical-slot binding")
        if report is None and slot not in planned:
            return Allocation(source=source, reason=f"Consumption was reported for unplanned slot T{slot}")
        by_slot[slot] = by_slot.get(slot, 0.0) + float(used)
    if not math.isclose(sum(by_slot.values()), grams, rel_tol=0.001, abs_tol=0.05):
        return Allocation(source=source, reason="Filament consumption breakdown is incomplete; reconcile spool usage")
    return Allocation(by_slot, source)
