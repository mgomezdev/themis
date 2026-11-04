"""Project Builder Review (BIZ-363): what stops a project from queueing well, each naming the Builder tab that fixes it.

Errors block "Queue project"; notes inform. Checks: a draft can't queue; a group with items needs eligible printers; "Printer default"
on a printer with no default process profile; every part must fit at least one covered printer's bed in X/Y (either way round) and Z;
loaded colours; pre-sliced gcode eligibility. Part sizes come from the project constructor's `measure` (cached in `part_sizes`)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import PartSize, Printer, Project, ProjectItem, UploadedFile
from . import filament_matching, gcode_eligibility, part_waivers, project_groups

logger = logging.getLogger(__name__)


@dataclass
class Check:
    level: str                    # error | note | ok
    tab: str                      # details | items | groups
    message: str
    group_id: int | None = None
    item_id: int | None = None


def _name(item: ProjectItem, f: UploadedFile | None) -> str:
    base = f.original_filename if f is not None else f"file {item.file_id}"
    kind = item.kind or "stl"
    if kind == "3mf_object":
        return f"{base} object {item.source_ref}"
    if kind in ("3mf_plate", "gcode") and item.source_ref:
        return f"{base} plate {item.source_ref}"
    return base


def _selection(item: ProjectItem) -> dict:
    kind = item.kind or "stl"
    if kind == "3mf_object":
        return {"object_id": item.source_ref}
    if kind == "3mf_plate":
        return {"plate": int(item.source_ref or 1)}
    return {}


def _measure_files(store, constructor, wanted: dict[str, tuple[str, list[dict]]]) -> dict[str, list]:
    """{file key: (library key, [selection])} -> {file key: [size | None]}. Blocking (worker thread)."""
    out = {}
    for fkey, (lib_key, sels) in wanted.items():
        try:
            with store.materialize(lib_key) as local:
                out[fkey] = list(constructor.measure(local, sels))
        except Exception:                              # noqa: BLE001 - an unmeasurable part is a note, never a failure
            logger.info("review: could not measure %s", lib_key, exc_info=True)
            out[fkey] = [None] * len(sels)
    return out


async def part_sizes(session: AsyncSession, items: list[ProjectItem], files: dict[int, UploadedFile],
                     constructor) -> dict[int, tuple[float, float, float] | None]:
    """{item id: (x, y, z) | None}: cached sizes, the rest measured (one call per file) and cached."""
    from .library_store import get_library_store
    keys = {i.id: part_waivers.part_key(i, files.get(i.file_id)) for i in items}
    cached = {r.part_key: r for r in (await session.execute(
        select(PartSize).where(PartSize.part_key.in_(set(keys.values()))))).scalars()} if keys else {}
    out: dict[int, tuple | None] = {}
    wanted: dict[str, tuple[str, list[dict]]] = {}
    pending: dict[str, list[tuple[int, int]]] = {}
    for i in items:
        row = cached.get(keys[i.id])
        if row is not None:
            out[i.id] = (row.size_x_mm, row.size_y_mm, row.size_z_mm)
            continue
        f = files.get(i.file_id)
        if f is None or constructor is None:
            out[i.id] = None
            continue
        lib_key, sels = wanted.setdefault(str(f.id), (f.relative_path, []))
        pending.setdefault(str(f.id), []).append((i.id, len(sels)))
        sels.append(_selection(i))
    if wanted:
        measured = await asyncio.to_thread(_measure_files, get_library_store(), constructor, wanted)
        now = datetime.now(timezone.utc).isoformat()
        for fkey, slots in pending.items():
            for item_id, idx in slots:
                size = measured[fkey][idx] if idx < len(measured[fkey]) else None
                out[item_id] = size
                if size is not None and keys[item_id] not in cached:
                    cached[keys[item_id]] = PartSize(part_key=keys[item_id], size_x_mm=size[0], size_y_mm=size[1],
                                                     size_z_mm=size[2], measured_at=now)
                    session.add(cached[keys[item_id]])
        await session.commit()
    return out


async def bed_height(session: AsyncSession, printer: Printer) -> float | None:
    """The printer's build height: its own value, else its machine profile's printable height, else unknown."""
    if printer.bed_z_mm:
        return float(printer.bed_z_mm)
    from .catalog_service import cached_catalog
    from .slicer_profiles import machine_profile_for
    catalog = cached_catalog()
    name = await machine_profile_for(session, printer)
    if catalog is None or not name:
        return None
    return next((p.height_mm for p in catalog.machines if p.name == name and p.height_mm), None)


def fits(size, printer: Printer, height: float | None) -> bool:
    bx, by = float(printer.bed_x_mm), float(printer.bed_y_mm)
    sx, sy, sz = size
    flat = (sx <= bx and sy <= by) or (sy <= bx and sx <= by)
    return flat and (height is None or sz <= height)


def _ask(item):
    return SimpleNamespace(ask_manufacturer=item.ask_manufacturer, ask_type=item.ask_type, ask_subtype=item.ask_subtype,
                           ask_color=item.ask_color, filament_map=None, tool_index=None)


def _names(printers) -> str:
    return ", ".join(p.name for p in printers)


async def review(session: AsyncSession, project: Project, constructor) -> list[Check]:
    checks: list[Check] = []
    if project.stage == "draft":
        checks.append(Check("error", "details", "This project is a draft: promote it to planning before queueing"))
    items = list((await session.execute(select(ProjectItem).where(ProjectItem.project_id == project.id)
                                        .order_by(ProjectItem.sort_order, ProjectItem.id))).scalars())
    if not items:
        checks.append(Check("error", "items", "Add at least one item"))
        return checks
    files = {f.id: f for f in (await session.execute(
        select(UploadedFile).where(UploadedFile.id.in_({i.file_id for i in items})))).scalars()}
    groups = await project_groups.groups_of(session, project.id)
    default = next((g for g in groups if g.is_default), None)
    by_group: dict[int, list[ProjectItem]] = {}
    for i in items:
        by_group.setdefault(i.group_id or (default.id if default else 0), []).append(i)

    covered_of: dict[int, list[Printer]] = {}
    for g in groups:
        g_items = by_group.get(g.id, [])
        if not g_items:
            continue
        targets = [(t.target_key, t.process_profile) for t in await project_groups.targets_of(session, g.id)]
        if not targets:
            checks.append(Check("error", "groups", f"{g.name}: choose its eligible printers", group_id=g.id))
            covered_of[g.id] = []
            continue
        resolved = await project_groups.resolve(session, targets, None)
        covered = [p for p in resolved.covered if p.enabled]
        covered_of[g.id] = covered
        if not covered:
            checks.append(Check("error", "groups", f"{g.name}: none of its eligible printers is enabled", group_id=g.id))
        for key, profile in targets:
            if profile:
                continue
            lacking = [p for p in covered if not p.default_process_profile and project_groups.covers(key, p)]
            if lacking:
                checks.append(Check("error", "groups", f"{g.name}: {_names(lacking)} has no default process profile, and the "
                                    "group uses \"Printer default\" for it. Set one on the printer or pick a profile here",
                                    group_id=g.id))

    packed = [i for i in items if (i.kind or "stl") != "gcode"]
    sizes = await part_sizes(session, packed, files, constructor)
    heights = {p.id: await bed_height(session, p) for ps in covered_of.values() for p in ps}
    unknown_z = sorted({p.name for ps in covered_of.values() for p in ps if heights[p.id] is None})
    if unknown_z:
        checks.append(Check("note", "groups", f"Build height unknown for {', '.join(unknown_z)}: parts are checked in X/Y only "
                                              "there (set it on the printer)"))
    all_fit = True
    for i in items:
        g_id = i.group_id or (default.id if default else 0)
        covered = covered_of.get(g_id, [])
        name = _name(i, files.get(i.file_id))
        if (i.kind or "stl") == "gcode":
            f = files.get(i.file_id)
            ok = [p for p in covered if (await gcode_eligibility.evaluate(session, f, p)).allowed]
            if covered and not ok:
                checks.append(Check("error", "groups", f"{name} is pre-sliced for none of its group's printers", g_id, i.id))
            elif ok:
                checks.append(Check("ok", "items", f"{name} is pre-sliced; eligible: {_names(ok)}", g_id, i.id))
            continue
        if not covered:
            continue
        size = sizes.get(i.id)
        if size is None:
            checks.append(Check("note", "items", f"{name}: its size is unknown, so it isn't fit-checked", g_id, i.id))
        else:
            fit = [p for p in covered if fits(size, p, heights[p.id])]
            dims = " × ".join(f"{v:g}" for v in size)
            split_plate = (i.kind == "3mf_plate") and (i.pack_rule or "split") in ("split", "solo")
            if not fit:
                all_fit = False
                checks.append(Check("note" if split_plate else "error", "items",
                                    f"{name} ({dims} mm) fits none of its group's printers" + (
                                        " as a whole plate (its objects are packed separately)" if split_plate else ""),
                                    g_id, i.id))
            elif len(fit) < len(covered):
                checks.append(Check("note", "items", f"{name} ({dims} mm) is too big for "
                                    f"{_names([p for p in covered if p not in fit])}; the others can take it", g_id, i.id))
        conflicts = frozenset()
        if not any(filament_matching.evaluate_for_printer(_ask(i), p, conflicts).eligible for p in covered):
            checks.append(Check("note", "items", f"{name}: no eligible printer has its filament loaded right now", g_id, i.id))
    if all_fit and not any(c.level == "error" for c in checks):
        checks.append(Check("ok", "items", "Every item is in a group, and every measured part fits at least one eligible printer"))
    return checks


def as_response(checks: list[Check]) -> dict:
    return {"checks": [asdict(c) for c in checks], "open": sum(1 for c in checks if c.level == "error")}
