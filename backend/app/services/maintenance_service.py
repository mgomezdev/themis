"""Maintenance-item due-status computation and printer vendor/model resolution."""
from __future__ import annotations
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MaintenanceItem, MaintenanceTrigger, Printer, PrinterMaintenanceState

_CALENDAR_DAYS = {"hours": 1 / 24, "days": 1.0, "weeks": 7.0, "months": 30.0}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_vendor_model(profile_name: str | None, catalog: list[dict]) -> tuple[str, str] | None:
    """Match a printer's current_orca_printer_profile against the machine catalog
    (GET /printers/orca-machine-catalog shape: {name, vendor, printer_model, nozzle})
    to get (vendor, printer_model). Returns None if unset or not found."""
    if not profile_name:
        return None
    for entry in catalog:
        if entry.get("name") == profile_name:
            return entry.get("vendor") or "", entry.get("printer_model") or ""
    return None


def item_applies_to_printer(item: MaintenanceItem, resolved: tuple[str, str] | None) -> bool:
    if item.scope == "general":
        return True
    if resolved is None:
        return False
    return item.machine_vendor == resolved[0] and item.machine_model == resolved[1]


async def _get_or_init_state(
    session: AsyncSession, printer: Printer, item: MaintenanceItem
) -> PrinterMaintenanceState:
    state = (await session.execute(
        select(PrinterMaintenanceState).where(
            PrinterMaintenanceState.printer_id == printer.id,
            PrinterMaintenanceState.maintenance_item_id == item.id,
        )
    )).scalar_one_or_none()
    if state is None:
        # No maintenance has ever been logged for this (printer, item) pair —
        # baseline starts at zero so lifetime counters accrue toward the
        # threshold from the printer's very first job, not from "whenever we
        # first computed due-status."
        state = PrinterMaintenanceState(
            printer_id=printer.id,
            maintenance_item_id=item.id,
            # last_done_at starts at "now" (unlike the job/time baselines
            # above) — a freshly added calendar-based item shouldn't appear
            # instantly overdue.
            last_done_at=_now(),
            baseline_job_count=0,
            baseline_print_seconds=0,
        )
        session.add(state)
        await session.flush()
    return state


def _trigger_due(trigger: MaintenanceTrigger, printer: Printer, state: PrinterMaintenanceState) -> bool:
    if trigger.trigger_type == "calendar":
        days_elapsed = (
            datetime.now(timezone.utc) - datetime.fromisoformat(state.last_done_at)
        ).total_seconds() / 86400
        threshold_days = trigger.amount * _CALENDAR_DAYS.get(trigger.unit or "days", 1.0)
        return days_elapsed >= threshold_days
    if trigger.trigger_type == "job_time":
        hours_elapsed = (printer.lifetime_print_seconds - state.baseline_print_seconds) / 3600
        return hours_elapsed >= trigger.amount
    if trigger.trigger_type == "job_count":
        jobs_elapsed = printer.lifetime_job_count - state.baseline_job_count
        return jobs_elapsed >= trigger.amount
    return False


async def compute_due_status(
    session: AsyncSession,
    printers: list[Printer],
    items: list[MaintenanceItem],
    triggers_by_item: dict[int, list[MaintenanceTrigger]],
    catalog: list[dict],
) -> list[dict]:
    """One row per (printer, applicable item):
    {printer_id, printer_name, item_id, item_name, due, last_done_at}.

    Side effect: commits the session. Lazily creating a PrinterMaintenanceState
    row for a (printer, item) pair that's never been evaluated before requires
    persisting it, so this function commits before returning — callers should
    not have unrelated uncommitted changes pending on the same session.
    """
    rows: list[dict] = []
    for printer in printers:
        resolved = resolve_vendor_model(printer.current_orca_printer_profile, catalog)
        for item in items:
            if not item.enabled or not item_applies_to_printer(item, resolved):
                continue
            state = await _get_or_init_state(session, printer, item)
            triggers = triggers_by_item.get(item.id, [])
            due = any(_trigger_due(t, printer, state) for t in triggers)
            rows.append({
                "printer_id": printer.id,
                "printer_name": printer.name,
                "item_id": item.id,
                "item_name": item.name,
                "due": due,
                "last_done_at": state.last_done_at,
            })
    await session.commit()
    return rows


async def mark_done(session: AsyncSession, printer: Printer, item: MaintenanceItem) -> PrinterMaintenanceState:
    state = await _get_or_init_state(session, printer, item)
    state.last_done_at = _now()
    state.baseline_job_count = printer.lifetime_job_count
    state.baseline_print_seconds = printer.lifetime_print_seconds
    await session.commit()
    return state


COMMON_MAINTENANCE_TEMPLATES: list[dict] = [
    {
        "name": "Wash build plate",
        "description": "Wash with warm water and dish soap (or an IPA wipe for PEI) to keep first-layer adhesion consistent.",
        "triggers": [{"trigger_type": "job_count", "amount": 10, "unit": None}],
    },
    {
        "name": "Clean nozzle / hotend",
        "description": "Cold-pull or brass-brush the nozzle tip to clear carbon buildup.",
        "triggers": [
            {"trigger_type": "job_count", "amount": 25, "unit": None},
            {"trigger_type": "calendar", "amount": 1, "unit": "months"},
        ],
    },
    {
        "name": "Lubricate linear rails / rods",
        "description": "Apply a thin coat of PTFE or lithium grease to all linear motion rails/rods.",
        "triggers": [
            {"trigger_type": "job_time", "amount": 100, "unit": None},
            {"trigger_type": "calendar", "amount": 3, "unit": "months"},
        ],
    },
    {
        "name": "Check belt tension",
        "description": "Pluck each belt and listen for a consistent, taut tone; re-tension as needed.",
        "triggers": [{"trigger_type": "calendar", "amount": 2, "unit": "months"}],
    },
    {
        "name": "Inspect / replace PTFE tube",
        "description": "Check the PTFE tube for scoring or a burnt/darkened end; replace if worn.",
        "triggers": [
            {"trigger_type": "job_time", "amount": 250, "unit": None},
            {"trigger_type": "calendar", "amount": 6, "unit": "months"},
        ],
    },
    {
        "name": "Clean cooling fans",
        "description": "Blow out dust from the part-cooling, hotend, and chamber fans.",
        "triggers": [{"trigger_type": "calendar", "amount": 3, "unit": "months"}],
    },
    {
        "name": "Inspect nozzle for wear",
        "description": "Check nozzle orifice roundness, especially after printing abrasive (CF/GF) filament.",
        "triggers": [
            {"trigger_type": "job_time", "amount": 150, "unit": None},
            {"trigger_type": "job_count", "amount": 50, "unit": None},
        ],
    },
    {
        "name": "Check for firmware / software updates",
        "description": "Check the vendor app or Themis printer settings for a pending firmware update.",
        "triggers": [{"trigger_type": "calendar", "amount": 1, "unit": "months"}],
    },
    {
        "name": "Calibrate bed level / flow",
        "description": "Run the printer's bed-leveling and flow-calibration routine.",
        "triggers": [{"trigger_type": "job_count", "amount": 20, "unit": None}],
    },
    {
        "name": "Clean chamber filter / activated carbon",
        "description": "Replace or rinse the enclosure's carbon/HEPA filter (enclosed or AMS-equipped printers).",
        "triggers": [{"trigger_type": "calendar", "amount": 2, "unit": "months"}],
    },
]
