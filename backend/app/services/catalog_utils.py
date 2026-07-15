from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.spoolman_service import fetch_filaments

logger = logging.getLogger("app.catalog_utils")


def catalog_name_sets(catalog: dict) -> tuple[set[str], set[str], set[str], set[str]]:
    """Return (machine_names, process_names, filament_names, filament_uuids)."""
    machine_names = {m["name"] for m in catalog.get("machine", []) if m.get("name")}
    process_names = {p["name"] for p in catalog.get("process", []) if p.get("name")}
    filament_names = {f["name"] for f in catalog.get("filament", []) if f.get("name")}
    filament_uuids = {f["uuid"] for f in catalog.get("filament", []) if f.get("uuid")}
    return machine_names, process_names, filament_names, filament_uuids


async def compute_drift(
    old_catalog: dict,
    new_catalog: dict,
    session: AsyncSession,
    spoolman_cfg,  # SpoolmanConfig | None
) -> dict | None:
    """Compare old vs new catalog; query live data for stale references.

    Returns a pending-remaps payload dict (without sync_id/status) if anything
    is affected, or None if the swap can proceed immediately.
    """
    from app.models import Printer, Job, JobPrinterConfig

    old_machines, old_processes, old_filaments, old_uuids = catalog_name_sets(old_catalog)
    new_machines, new_processes, new_filaments, new_uuids = catalog_name_sets(new_catalog)

    removed_machines = old_machines - new_machines
    removed_processes = old_processes - new_processes
    removed_filaments = old_filaments - new_filaments
    removed_uuids = old_uuids - new_uuids

    if not any([removed_machines, removed_processes, removed_filaments, removed_uuids]):
        return None

    # --- Collect raw hits and group by (field, stale_value) ---

    # Printers: group by (field, stale_value)
    printer_groups: dict[tuple[str, str], dict] = {}
    printers = (await session.execute(select(Printer))).scalars().all()
    for printer in printers:
        if printer.current_orca_printer_profile in removed_machines:
            key = ("current_orca_printer_profile", printer.current_orca_printer_profile)
            g = printer_groups.setdefault(key, {
                "field": "current_orca_printer_profile",
                "stale_value": printer.current_orca_printer_profile,
                "options_kind": "machine",
                "required": True,
                "affected_printer_ids": [],
                "affected_printer_names": [],
                "affected_slots": [],
            })
            g["affected_printer_ids"].append(printer.id)
            g["affected_printer_names"].append(printer.name)
            g["affected_slots"].append(None)

        for slot_idx, slot in enumerate(printer.loaded_filaments or []):
            fp = slot.get("filament_profile")
            if fp and fp in removed_filaments:
                key = ("loaded_filaments.filament_profile", fp)
                g = printer_groups.setdefault(key, {
                    "field": "loaded_filaments.filament_profile",
                    "stale_value": fp,
                    "options_kind": "filament",
                    "required": True,
                    "affected_printer_ids": [],
                    "affected_printer_names": [],
                    "affected_slots": [],
                })
                g["affected_printer_ids"].append(printer.id)
                g["affected_printer_names"].append(printer.name)
                g["affected_slots"].append(slot_idx)

    # Jobs: queued + blocked only; group by (field, stale_value)
    job_groups: dict[tuple[str, str], dict] = {}
    live_jobs = (await session.execute(
        select(Job).where(Job.status.in_(["queued", "blocked"]))
    )).scalars().all()
    for job in live_jobs:
        configs = (await session.execute(
            select(JobPrinterConfig).where(JobPrinterConfig.job_id == job.id)
        )).scalars().all()
        for cfg in configs:
            if cfg.print_profile in removed_processes:
                key = ("print_profile", cfg.print_profile)
                g = job_groups.setdefault(key, {
                    "field": "print_profile",
                    "stale_value": cfg.print_profile,
                    "options_kind": "process",
                    "required": False,
                    "affected_config_ids": [],
                    "affected_file_names": [],
                })
                g["affected_config_ids"].append(cfg.id)
                g["affected_file_names"].append(f"job#{job.id}")

            fp = cfg.filament_profile
            if fp and fp in removed_filaments:
                key = ("filament_profile", fp)
                g = job_groups.setdefault(key, {
                    "field": "filament_profile",
                    "stale_value": fp,
                    "options_kind": "filament",
                    "required": False,
                    "affected_config_ids": [],
                    "affected_file_names": [],
                })
                g["affected_config_ids"].append(cfg.id)
                g["affected_file_names"].append(f"job#{job.id}")

    # Spoolman filaments: group by stale_uuid
    spoolman_groups: dict[str, dict] = {}
    spoolman_error: str | None = None
    spool_filaments: list = []
    if spoolman_cfg and spoolman_cfg.enabled and spoolman_cfg.url and removed_uuids:
        try:
            spool_filaments = await fetch_filaments(spoolman_cfg.url, spoolman_cfg.api_key)
            for fil in spool_filaments:
                raw_extra = (fil.get("extra") or {}).get("orca_profiles")
                if not raw_extra:
                    continue
                try:
                    profiles: dict = json.loads(json.loads(raw_extra))
                except Exception:
                    continue
                for uid in profiles:
                    if uid in removed_uuids:
                        stale_name = profiles[uid] if isinstance(profiles[uid], str) else str(uid)
                        g = spoolman_groups.setdefault(uid, {
                            "stale_uuid": uid,
                            "stale_name": stale_name,
                            "options_kind": "filament_uuid",
                            "required": False,
                            "affected_filament_ids": [],
                            "affected_filament_names": [],
                        })
                        g["affected_filament_ids"].append(fil["id"])
                        g["affected_filament_names"].append(fil.get("name", str(fil["id"])))
        except Exception as exc:
            spoolman_error = str(exc)
            logger.warning("Spoolman fetch failed during drift check: %s", exc)

    all_printer = list(printer_groups.values())
    all_jobs = list(job_groups.values())
    all_spoolman = list(spoolman_groups.values())

    if not any([all_printer, all_jobs, all_spoolman]):
        return None

    # Build filament_uuids from Spoolman filaments (non-stale orca UUIDs only).
    # This gives the user their own library (~tens of items) instead of the full
    # OrcaSlicer catalog (~7000 items).
    fil_uuid_opts: list[dict] = []
    if spool_filaments:
        stale_uuids = set(spoolman_groups.keys())
        seen_names: set[str] = set()
        for fil in spool_filaments:
            name = (fil.get("name") or "").strip()
            if not name or name in seen_names:
                continue
            raw_extra = (fil.get("extra") or {}).get("orca_profiles")
            if not raw_extra:
                continue
            try:
                profiles2: dict = json.loads(json.loads(raw_extra))
            except Exception:
                continue
            for uid in profiles2:
                if uid not in stale_uuids:
                    fil_uuid_opts.append({"uuid": uid, "name": name})
                    seen_names.add(name)
                    break

    return {
        "pending": {
            "printers": all_printer,
            "jobs": all_jobs,
            "spoolman_filaments": all_spoolman,
        },
        "options": {
            "machine": sorted(new_machines),
            "process": sorted(new_processes),
            "filament": sorted(new_filaments),
            "filament_uuids": fil_uuid_opts,
        },
        "spoolman_error": spoolman_error,
    }
