"""Pooled print groups (BIZ-362): a group with strategy "pool" is one queue entry; each idle printer packs the remaining parts it
can take into its own next plate. Remaining = quantity − parts reserved on the pool's live jobs + failed parts put back
(BIZ-364 rows resolved "requeued" on pool jobs). Split / Together items pool; Solo, Locked and gcode items are generated as jobs."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Job, JobItemFailure, JobPrinterConfig, Printer, Project, ProjectGroup, ProjectItem, UploadedFile
from . import filament_matching, part_waivers, project_groups
from .library_store import get_library_store
from .providers.contract import ConstructInput, ConstructRequest, NothingFits, TargetPrinter

POOL = "pool"


def poolable(item) -> bool:
    return (item.kind or "stl") in ("stl", "3mf_object", "3mf_plate") and (item.pack_rule or "split") in ("split", "together")


async def items_of(session: AsyncSession, group: ProjectGroup) -> list[ProjectItem]:
    rows = (await session.execute(select(ProjectItem).where(ProjectItem.group_id == group.id)
                                  .order_by(ProjectItem.sort_order, ProjectItem.id))).scalars().all()
    return [i for i in rows if poolable(i)]


async def _reserved(session: AsyncSession, group_id: int, live_only: bool) -> dict[int, int]:
    stmt = select(Job).where(Job.pool_group_id == group_id, Job.status != "cancelled")
    if live_only:
        stmt = stmt.where(Job.status.not_in(["complete", "failed"]))
    out: dict[int, int] = {}
    for j in (await session.execute(stmt)).scalars():
        for k, n in json.loads(j.project_item_quantities or "{}").items():
            out[int(k)] = out.get(int(k), 0) + int(n)
    return out


async def remaining(session: AsyncSession, group: ProjectGroup) -> dict[int, int]:
    """{item id: parts still to print} for the pool's items (0 when all are reserved or done)."""
    reserved = await _reserved(session, group.id, live_only=False)
    returned: dict[int, int] = {}
    for jif in (await session.execute(select(JobItemFailure).join(Job, Job.id == JobItemFailure.job_id).where(
            Job.pool_group_id == group.id, JobItemFailure.resolution.in_(["requeued", "reprinted"])))).scalars():
        returned[jif.project_item_id] = returned.get(jif.project_item_id, 0) + jif.quantity_failed
    return {i.id: max(0, i.quantity - reserved.get(i.id, 0) + returned.get(i.id, 0)) for i in await items_of(session, group)}


async def active_pools(session: AsyncSession) -> list[ProjectGroup]:
    """Pools of queued projects with a queue position (set at generate), in queue order."""
    return list((await session.execute(
        select(ProjectGroup).join(Project, Project.id == ProjectGroup.project_id)
        .where(ProjectGroup.strategy == POOL, ProjectGroup.queue_position.is_not(None), Project.stage == "queued")
        .order_by(ProjectGroup.queue_position))).scalars().all())


def _ask(item):
    return SimpleNamespace(ask_manufacturer=item.ask_manufacturer, ask_type=item.ask_type, ask_subtype=item.ask_subtype,
                           ask_color=item.ask_color, filament_map=None, tool_index=None)


async def eligible_on(session: AsyncSession, group: ProjectGroup, printer: Printer) -> bool:
    """Is `printer` one of the group's targets?"""
    from .queue.preference import allowed
    return allowed(group.printer_preference,printer) and any(project_groups.covers(t.target_key, printer) for t in await project_groups.targets_of(session, group.id))


async def process_for(session: AsyncSession, group: ProjectGroup, printer: Printer) -> str:
    """The process profile of the most specific target covering `printer` ("" = its default)."""
    targets = {t.target_key: t.process_profile for t in await project_groups.targets_of(session, group.id)}
    for key in (str(printer.id), f"{project_groups.MODEL_PREFIX}{printer.model_uuid}", project_groups.ANY_KEY):
        if key in targets:
            return targets[key]
    return ""


async def takeable(session: AsyncSession, group: ProjectGroup, printer: Printer, conflicts=frozenset()) -> dict[int, int]:
    """The remaining parts `printer` could take: loaded filament fits the item's ask and the printer isn't waived for it."""
    left = await remaining(session, group)
    waived = await part_waivers.waived_keys(session, printer.id)
    out = {}
    for item in await items_of(session, group):
        n = left.get(item.id, 0)
        if n <= 0 or not filament_matching.evaluate_for_printer(_ask(item), printer, conflicts).eligible:
            continue
        if waived:
            if part_waivers.part_key(item, await session.get(UploadedFile, item.file_id)) in waived:
                continue
        out[item.id] = n
    return out


_ASK = ("ask_manufacturer", "ask_type", "ask_subtype", "ask_color")
PACK_FOLDER = "Job Pack 3MFs"


def _next_plate(store, constructor, wanted, target, max_parts):
    """`constructor.next_plate` over the stored files `wanted` = [(key, copies, ref, select)] -> (3MF bytes, copies taken per
    input). The provider counts instances: a 3MF plate bundle is several per copy, so they are divided by the instances per copy
    the plate's manifest says were requested (as generation does, BIZ-359). Blocking."""
    with contextlib.ExitStack() as stack:
        inputs = tuple(ConstructInput(stack.enter_context(store.materialize(key)), qty, ref,
                                      **{k: v for k, v in sel.items() if v is not None}) for key, qty, ref, sel in wanted)
        out_dir = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        artifact, consumed = constructor.next_plate(ConstructRequest(inputs, target), out_dir, max_parts=max_parts)
        try:
            manifest = constructor.plate_manifest(artifact.path)
        except Exception:                              # noqa: BLE001 - a broken manifest reader means one instance per copy
            manifest = None
        requested = getattr(manifest, "requested", None) or {}
        copies = {}
        for i, n in consumed.items():
            if 0 <= i < len(wanted) and n > 0:
                per_copy = max(1, requested.get(i, wanted[i][1]) // wanted[i][1])
                if n // per_copy:
                    copies[i] = n // per_copy
        return artifact.path.read_bytes(), copies


async def pack_plate(factory, group_id: int, printer_id: int, constructor) -> int | None:
    """Pack `printer`'s next plate from the pool and queue it as a job for that printer alone; the job id, or None when the
    pool has nothing this printer can take any more. Raises `NothingFits` / `ProviderError` from the constructor. The pack call
    runs in a worker thread with no session open; the caller serialises packs per pool, so nothing is reserved twice."""
    from .slice_resolution import target_of
    from .slicer_profiles import machine_profile_for
    store = get_library_store()
    async with factory() as session:
        group, printer = await session.get(ProjectGroup, group_id), await session.get(Printer, printer_id)
        if group is None or printer is None:
            return None
        take = await takeable(session, group, printer, await filament_matching.conflicts_for(list(printer.loaded_filaments or [])))
        items = [i for i in await items_of(session, group) if i.id in take]
        if not items:
            return None
        by_ask: dict[tuple, list] = {}                         # one filament ask per plate, tried in item order
        for item in items:
            by_ask.setdefault(tuple(getattr(item, c) for c in _ASK), []).append(item)
        attempts = []
        for ask, ask_items in by_ask.items():
            wanted = []
            for item in ask_items:
                f = await session.get(UploadedFile, item.file_id)
                kind = item.kind or "stl"
                wanted.append((f.relative_path, take[item.id], f"item-{item.id}",
                               {"object_id": item.source_ref if kind == "3mf_object" else None,
                                "plate": int(item.source_ref) if kind == "3mf_plate" else None, "rule": item.pack_rule or "split"}))
            attempts.append((ask, ask_items, wanted))
        base = target_of(printer)
        target = TargetPrinter(base.bed_mm, base.model_uuid, machine_profile=await machine_profile_for(session, printer))
        profile = await process_for(session, group, printer)
        overrides = dict(group.overrides) if group.overrides else None
        preference = group.printer_preference
        choices = {t.target_key:t for t in await project_groups.targets_of(session,group.id)}
        chosen = next((choices[key] for key in (str(printer.id), f"model:{printer.model_uuid}", "any") if key in choices),None)
        target_overrides = chosen.overrides if chosen else None
        filament_profile = chosen.filament_profile if chosen else None
        filament_map = chosen.filament_map if chosen else None
        project = await session.get(Project, group.project_id)
        position, max_parts, project_id, order_id = group.queue_position, group.max_parts, project.id, project.order_id
        slug = re.sub(r"[^a-z0-9]+", "-", project.name.lower()).strip("-") or "project"

    for ask, items, wanted in attempts:
        try:
            packed, copies = await asyncio.to_thread(_next_plate, store, constructor, wanted, target, max_parts)
        except NothingFits:
            continue                                           # this ask's parts don't fit this bed: try the next ask
        quantities = {str(items[i].id): n for i, n in copies.items()}
        if quantities:
            break
    else:
        raise NothingFits("no remaining part fits this bed")

    now = datetime.now(timezone.utc).isoformat()
    key = f"{PACK_FOLDER}/pool-{group_id}/{slug}-{uuid.uuid4().hex[:8]}.3mf"
    published = await asyncio.to_thread(store.put, key, io.BytesIO(packed))
    async with factory() as session:
        f = UploadedFile(original_filename=key.rsplit("/", 1)[1], plates=[{"plate_number": 1, "thumbnail_path": None}],
                         uploaded_at=now, relative_path=key, folder=f"/{PACK_FOLDER}/pool-{group_id}", size_bytes=published.size,
                         content_hash=hashlib.sha256(packed).hexdigest(), mtime=published.mtime, missing=False)
        session.add(f)
        await session.flush()
        job = Job(uploaded_file_id=f.id, plate_number=1, project_id=project_id, order_id=order_id, queue_position=position,
                  status="queued", created_at=now, updated_at=now, project_item_quantities=json.dumps(quantities),
                  pool_group_id=group_id, overrides=overrides, printer_preference=preference)
        session.add(job)
        await session.flush()
        session.add(JobPrinterConfig(job_id=job.id, printer_id=printer_id, print_profile=profile, overrides=target_overrides,
            filament_profile=filament_profile, filament_map=filament_map, **dict(zip(_ASK, ask))))
        await session.commit()
        return job.id


async def stats(session: AsyncSession, group: ProjectGroup) -> dict:
    items = await items_of(session, group)
    return {"done": sum(i.quantity_completed for i in items),
            "printing": sum((await _reserved(session, group.id, live_only=True)).values()),
            "left": sum((await remaining(session, group)).values())}


async def warning(session: AsyncSession, group: ProjectGroup) -> str | None:
    """Why the pool is stuck, when no eligible printer could take any of its remaining parts."""
    if sum((await remaining(session, group)).values()) == 0:
        return None
    for printer in (await session.execute(select(Printer).where(Printer.enabled.is_(True)))).scalars():
        if await eligible_on(session, group, printer) and await takeable(session, group, printer):
            return None
    return f"{group.name}: no eligible printer can take the remaining parts (filament, waivers, or eligible printers)"
