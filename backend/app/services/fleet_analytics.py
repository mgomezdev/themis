"""Aggregate fleet analytics (success rate, utilization, material, cost) over a date range.

Reads terminal jobs in one query (+ one for their printer configs, used only to name the material), so the
cost is constant in queries however many jobs there are.

Definitions (also surfaced in the UI):
- A job lands in the range by `completed_at` (UTC; stored for complete, failed and cancelled jobs).
- success_rate = complete / (complete + failed). Cancelled jobs are a user decision, not a printer outcome, so
  they are counted but excluded from the rate. None when nothing completed or failed.
- print time / grams / cost count *completed* jobs only. `actual_seconds`/`actual_filament_grams` are the
  slicer's figures taken at slice time, not measured, so they'd overstate what a failed or cancelled print
  really used; failed-print waste is deliberately not guessed at.
- utilization = print time / range length, clamped to 100 % (a job can finish early in the range after
  starting before it).
- a job is attributed to `printed_on_printer_id`, falling back to `assigned_printer_id`; jobs with neither
  (e.g. cancelled before assignment) count in totals only.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Job, JobPrinterConfig, Printer

_TERMINAL = ("complete", "failed", "cancelled")
_MATERIAL_RE = re.compile(r"\b(PETG|PLA|ABS|ASA|TPU|PVA|HIPS|PET|PA|PC|NYLON)\b", re.IGNORECASE)
UNKNOWN_MATERIAL = "Unknown"
MAX_RANGE_DAYS = 366


def material_from_name(name: str | None) -> str | None:
    """Material family named in a filament preset/profile string ("Bambu PLA Basic @BBL X1C" -> "PLA")."""
    if not name:
        return None
    m = _MATERIAL_RE.search(name)
    if not m:
        return None
    mat = m.group(1).upper()
    return "PA" if mat == "NYLON" else mat


def _rate(complete: int, failed: int) -> float | None:
    done = complete + failed
    return None if done == 0 else round(complete / done * 100, 1)


@dataclass
class _Bucket:
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    seconds: int = 0
    grams: float = 0.0
    cost: float = 0.0
    cost_jobs: int = 0

    def add(self, job) -> None:
        if job.status == "complete":
            self.completed += 1
            self.seconds += job.actual_seconds or 0
            self.grams += job.actual_filament_grams or 0.0
            if job.filament_cost is not None:
                self.cost += job.filament_cost
                self.cost_jobs += 1
        elif job.status == "failed":
            self.failed += 1
        else:
            self.cancelled += 1


def _job_material_grams(job, config) -> list[tuple[str, float]]:
    """(material, grams) pairs for one completed job."""
    config_material = None
    if config is not None:
        ft = (config.filament_type or "").strip()
        config_material = (ft.upper() if ft and ft.lower() != "any" else None) or material_from_name(config.filament_profile)
    if job.actual_filament_breakdown:
        return [
            (material_from_name(e.get("filament_profile")) or config_material or UNKNOWN_MATERIAL, float(e.get("grams") or 0.0))
            for e in job.actual_filament_breakdown
        ]
    if job.actual_filament_grams:
        return [(config_material or UNKNOWN_MATERIAL, job.actual_filament_grams)]
    return []


async def compute(session: AsyncSession, start: date, end: date) -> dict:
    """`end` is inclusive. Raises ValueError for an inverted or over-long range."""
    if end < start:
        raise ValueError("end must not be before start")
    days = (end - start).days + 1
    if days > MAX_RANGE_DAYS:
        raise ValueError(f"range may not exceed {MAX_RANGE_DAYS} days")
    lo = datetime.combine(start, datetime.min.time()).isoformat()
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time()).isoformat()

    jobs = (await session.execute(
        select(Job).where(Job.status.in_(_TERMINAL), Job.completed_at >= lo, Job.completed_at < hi)
    )).scalars().all()
    job_ids = [j.id for j in jobs]

    printers = (await session.execute(select(Printer).order_by(Printer.name))).scalars().all()

    # First config per (job, printer) names the material when the breakdown can't.
    configs: dict[tuple[int, int], JobPrinterConfig] = {}
    if job_ids:
        rows = (await session.execute(
            select(JobPrinterConfig).where(
                JobPrinterConfig.job_id.in_(
                    select(Job.id).where(Job.status == "complete", Job.completed_at >= lo, Job.completed_at < hi))
            ).order_by(JobPrinterConfig.id)
        )).scalars().all()
        for c in rows:
            configs.setdefault((c.job_id, c.printer_id), c)

    totals = _Bucket()
    per_printer: dict[int, _Bucket] = defaultdict(_Bucket)
    material_grams: dict[str, float] = defaultdict(float)
    material_jobs: dict[str, set[int]] = defaultdict(set)

    for j in jobs:
        totals.add(j)
        pid = j.printed_on_printer_id if j.printed_on_printer_id is not None else j.assigned_printer_id
        if pid is not None:
            per_printer[pid].add(j)
        if j.status == "complete":
            config = configs.get((j.id, pid)) if pid is not None else None
            for material, grams in _job_material_grams(j, config):
                material_grams[material] += grams
                material_jobs[material].add(j.id)

    period_seconds = days * 86400

    def _row(b: _Bucket) -> dict:
        return {
            "completed": b.completed, "failed": b.failed, "cancelled": b.cancelled,
            "success_rate": _rate(b.completed, b.failed),
            "print_seconds": b.seconds,
            "filament_grams": round(b.grams, 1),
            # null when no completed job has a recorded cost, so the UI can show "—" instead of a false $0.
            "filament_cost": round(b.cost, 2) if b.cost_jobs else None,
        }

    return {
        "range": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "totals": _row(totals),
        "printers": [
            {
                "printer_id": p.id, "name": p.name,
                **_row(per_printer.get(p.id, _Bucket())),
                "utilization_pct": round(min(100.0, per_printer.get(p.id, _Bucket()).seconds / period_seconds * 100), 1),
            }
            for p in printers
        ],
        "materials": sorted(
            ({"material": m, "grams": round(g, 1), "jobs": len(material_jobs[m])} for m, g in material_grams.items()),
            key=lambda r: -r["grams"],
        ),
    }
