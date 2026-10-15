from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.providers.slicing import Catalog
from app.plugins.capabilities.filament_inventory import PROFILE_LINKS_READ, InvMaterial
from app.services.inventory import provider as inventory_provider, read as inventory_read

logger = logging.getLogger("app.catalog_utils")

# Drift/remap field for a model's slicer profile mapping rows (as opposed to a printer's own raw override).
MODEL_PROFILE_FIELD = "model_slicer_profile"


def catalog_name_sets(catalog: Catalog) -> tuple[set[str], set[str], set[str], set[str]]:
    """Return (machine_names, process_names, filament_names, filament_refs)."""
    return (
        catalog.names("machine"),
        catalog.names("process"),
        catalog.names("filament"),
        catalog.refs("filament"),
    )


def stale_binding_groups(filaments: list[InvMaterial], is_stale) -> dict[tuple[str, str], dict]:
    """Group the profile names bound to inventory filaments that `is_stale(name)` flags, by (printer preset, name)."""
    groups: dict[tuple[str, str], dict] = {}
    for fil in filaments:
        fil_id = int(fil.ref) if fil.ref.isdigit() else fil.ref
        for printer_preset, names in (fil.profile_links or {}).items():
            for name in names:
                if is_stale(name):
                    g = groups.setdefault((printer_preset, name), {
                        "printer_preset": printer_preset,
                        "stale_name": name,
                        "required": False,
                        "affected_filament_ids": [],
                        "affected_filament_names": [],
                    })
                    g["affected_filament_ids"].append(fil_id)
                    g["affected_filament_names"].append(fil.name or str(fil.ref))
    return groups


async def compute_drift(
    old_catalog: Catalog,
    new_catalog: Catalog,
    session: AsyncSession,
) -> dict | None:
    """Compare old vs new catalog; query live data for stale references.

    Returns a pending-remaps payload dict (without sync_id/status) if anything
    is affected, or None if the swap can proceed immediately.
    """
    from app.models import Printer, Job, JobPrinterConfig

    old_machines, old_processes, old_filaments, _ = catalog_name_sets(old_catalog)
    new_machines, new_processes, new_filaments, _ = catalog_name_sets(new_catalog)

    removed_machines = old_machines - new_machines
    removed_processes = old_processes - new_processes
    removed_filaments = old_filaments - new_filaments

    if not any([removed_machines, removed_processes, removed_filaments]):
        return None

    # --- Collect raw hits and group by (field, stale_value) ---

    # Printers: group by (field, stale_value)
    printer_groups: dict[tuple[str, str], dict] = {}
    printers = (await session.execute(select(Printer))).scalars().all()
    # Machine profiles now live on the model mapping rows (BIZ-294): one entry per stale profile, listing the printers whose model
    # uses it. Applying it (routes/laminus.py) rewrites the mapping rows, so a model with no printers yet is still reported.
    from app.models import PrinterModelRecord, PrinterModelSlicerProfile
    stale_rows = [r for r in (await session.execute(select(PrinterModelSlicerProfile))).scalars().all()
                  if r.machine_profile in removed_machines]
    for row in stale_rows:
        key = (MODEL_PROFILE_FIELD, row.machine_profile)
        g = printer_groups.setdefault(key, {
            "field": MODEL_PROFILE_FIELD,
            "stale_value": row.machine_profile,
            "options_kind": "machine",
            "required": True,
            "affected_printer_ids": [],
            "affected_printer_names": [],
            "affected_slots": [],
        })
        for printer in printers:
            if printer.model_uuid == row.model_uuid and printer.id not in g["affected_printer_ids"]:
                g["affected_printer_ids"].append(printer.id)
                g["affected_printer_names"].append(printer.name)
                g["affected_slots"].append(None)
        if not g["affected_printer_names"]:
            rec = await session.get(PrinterModelRecord, row.model_uuid)
            g.setdefault("affected_model_names", []).append(rec.display_name if rec else row.model_uuid)
    for printer in printers:
        if printer.machine_profile_override in removed_machines:
            key = ("machine_profile_override", printer.machine_profile_override)
            g = printer_groups.setdefault(key, {
                "field": "machine_profile_override",
                "stale_value": printer.machine_profile_override,
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

    # Inventory filaments: check that the profile names bound to each filament still exist in the new catalog.
    # Bindings are {printer_preset: [filament_profile_name, ...]} — group stale names by (preset, name).
    inventory_groups: dict[tuple[str, str], dict] = {}
    inventory_error: str | None = None
    if inventory_provider.has(PROFILE_LINKS_READ) and removed_filaments:
        fetched = await inventory_provider.call("list_materials")
        if fetched.ok:
            inventory_groups = stale_binding_groups(fetched.value, lambda name: name in removed_filaments)
        else:
            inventory_error = inventory_provider.describe_failure(fetched)[1]
            logger.warning("Inventory fetch failed during drift check: %s", inventory_error)

    all_printer = list(printer_groups.values())
    all_jobs = list(job_groups.values())
    all_inventory = list(inventory_groups.values())

    if not any([all_printer, all_jobs, all_inventory]):
        return None

    return {
        "pending": {
            "printers": all_printer,
            "jobs": all_jobs,
            "inventory_filaments": all_inventory,
        },
        "options": {
            "machine": sorted(new_machines),
            "process": sorted(new_processes),
            "filament": sorted(new_filaments),
        },
        "inventory_error": inventory_error,
    }
