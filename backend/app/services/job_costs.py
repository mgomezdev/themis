"""What a project really cost to make: filament + machine time + labour + bought-in parts.

Rates are applied **live** from the current settings (not snapshotted when a job printed), so changing a rate in
Settings re-prices every past job consistently. Definitions:

- filament: the manually entered `jobs.filament_cost` (unchanged; never computed from Spoolman pricing).
- machine: for each *completed* job, `actual_seconds` (the slicer's figure, not a measurement) × the rate of the
  printer it ran on (that printer's own rate if set, else the shop rate). Failed/cancelled jobs cost nothing here.
  A job's printer is `assigned_printer_id`, which deleting the printer clears — so completed jobs of a deleted
  printer are re-priced at the shop rate (its own rate is gone with it).
- labour: logged minutes × the shop labour rate.
- parts: quantity × unit cost of each non-printed part that has a unit cost.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CostConfig, Job, Printer, ProjectLabor, ProjectPart

CATEGORIES = ("filament", "machine", "labour", "parts")


@dataclass(frozen=True)
class Rates:
    machine: float = 0.0
    labour: float = 0.0
    printer_machine: dict = None   # printer id -> override rate

    def machine_for(self, printer_id: int | None) -> float:
        override = (self.printer_machine or {}).get(printer_id) if printer_id is not None else None
        return self.machine if override is None else override


async def load_rates(session: AsyncSession) -> Rates:
    cfg = await session.get(CostConfig, 1)
    overrides = {pid: rate for pid, rate in (await session.execute(
        select(Printer.id, Printer.machine_rate_per_hour).where(Printer.machine_rate_per_hour.is_not(None))
    )).all()}
    return Rates(machine=cfg.machine_rate_per_hour if cfg else 0.0, labour=cfg.labour_rate_per_hour if cfg else 0.0,
                 printer_machine=overrides)


def compute(jobs: Iterable[Job], labor: Iterable[ProjectLabor], parts: Iterable[ProjectPart], rates: Rates) -> dict:
    """Cost breakdown for one project. All amounts rounded to cents; `total` is the sum of the rounded categories."""
    filament = sum(j.filament_cost for j in jobs if j.filament_cost is not None)
    machine_seconds = 0
    machine = 0.0
    for j in jobs:
        if j.status == "complete" and j.actual_seconds:
            machine_seconds += j.actual_seconds
            machine += j.actual_seconds / 3600 * rates.machine_for(j.assigned_printer_id)
    labour_minutes = sum(l.minutes for l in labor)
    labour = labour_minutes / 60 * rates.labour
    parts_cost = sum((p.quantity or 0) * p.unit_cost for p in parts if p.unit_cost is not None)
    out = {
        "filament": round(filament, 2), "machine": round(machine, 2),
        "labour": round(labour, 2), "parts": round(parts_cost, 2),
        "machine_hours": round(machine_seconds / 3600, 2), "labour_hours": round(labour_minutes / 60, 2),
    }
    out["total"] = round(sum(out[c] for c in CATEGORIES), 2)
    return out


async def costs_by_project(session: AsyncSession, project_ids: list[int], jobs_by_project: dict[int, list[Job]]) -> dict[int, dict]:
    """Costs for several projects with constant queries (jobs are passed in, already loaded)."""
    if not project_ids:
        return {}
    rates = await load_rates(session)
    labor: dict[int, list[ProjectLabor]] = defaultdict(list)
    for l in (await session.execute(select(ProjectLabor).where(ProjectLabor.project_id.in_(project_ids)))).scalars().all():
        labor[l.project_id].append(l)
    parts: dict[int, list[ProjectPart]] = defaultdict(list)
    for p in (await session.execute(select(ProjectPart).where(ProjectPart.project_id.in_(project_ids)))).scalars().all():
        parts[p.project_id].append(p)
    return {pid: compute(jobs_by_project.get(pid, []), labor[pid], parts[pid], rates) for pid in project_ids}
