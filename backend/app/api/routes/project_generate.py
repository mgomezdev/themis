"""Generate: pack a project's items into plates through the construction provider, store the packs in the library and queue a job per plate; plus the held-job actions.

Part of the `projects` routes: registered on the same `router` and imported at the bottom of projects.py.
What tests replace on that module (get_library_store, get_project_constructor, queue_engine, refresh_content_hash, secrets) is read as `_projects.<name>` at call time, so one patch
point covers every projects route.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import tempfile
import uuid as _uuid
from pathlib import Path
from typing import Literal, Optional
from fastapi import Depends, Header, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from ._filament_ask import row_tuple, tuple_columns
from ...services.filament_tuple import FilamentTuple
from ...auth import require_scope
from ...database import get_session
from ...models import Job, JobModelTarget, JobPrinterConfig, Printer, Project, ProjectGroup, ProjectItem, QueueConfig, SlicedVersion, UploadedFile
from ...services import idempotency, model_targets, print_results, project_events, project_groups, slice_cache, bed_requirement
from ...services.three_mf_parser import plate_numbers
from ...services.artifact_store import ArtifactError, ArtifactStore
from ...services.library_scanner import (
    ACTIVE_JOB_STATUSES, fresh_content_hash,
)
from ...services.providers.contract import (
    Action, CancelToken, ConstructInput, ConstructRequest, ErrorCode, ProjectConstructor, ProviderError, Reason, TargetPrinter,
    resolve_construct,
)

from . import projects as _projects
from .projects import (  # noqa: I001
    GenerateRequest,
    _get_project_or_404,
    _job_count,
    _now_iso,
    _remember,
    _slugify,
    logger,
    router,
)


# ---------------------------------------------------------------------------
# Generate — packs STLs one 3MF per filament group via Orca, saves to library,
# and queues one job per plate.
# ---------------------------------------------------------------------------


async def _max_queue_position(session: AsyncSession) -> float:
    """The last place in the queue: jobs and pools (BIZ-362: a pool is a queue entry too), so nothing ties with a pool."""
    jobs = (await session.execute(select(func.max(Job.queue_position)))).scalar_one_or_none() or 0.0
    pools = (await session.execute(select(func.max(ProjectGroup.queue_position)))).scalar_one_or_none() or 0.0
    return max(jobs, pools)


def _parse_plate_nums(path: Path) -> list[int]:
    try:
        import zipfile
        with zipfile.ZipFile(path) as zf:
            return plate_numbers(zf)
    except Exception:
        return []


PACK_FOLDER = "Job Pack 3MFs"


async def _stored(store: ArtifactStore, key: str) -> bool:
    """Whether the library holds `key`; a key the store refuses (e.g. a legacy row with an empty `relative_path`) is absent."""
    try:
        return await asyncio.to_thread(store.exists, key)
    except ArtifactError:
        return False


def _round_robin_quantities(group_items, num_plates: int) -> list[dict[str, int]]:
    """The pre-manifest guess: each item's quantity spread evenly over the plates, the remainder on the first ones."""
    return [{str(item.id): item.quantity // num_plates + (1 if i < item.quantity % num_plates else 0) for item in group_items}
            for i in range(num_plates)]


def _plate_item_quantities(manifest, wanted: list[tuple[str, int, str]], group_items, plate_nums: list[int]) -> list[dict[str, int]] | None:
    """Per plate (in `plate_nums` order), how many of each project item it holds, from the constructor's `PlateManifest`
    (BIZ-359). `wanted[i]` is pack input i = (key, total qty, file id); items sharing a file are filled in item order. None when
    there is no manifest or it disagrees with the request (plates, input indexes or per-input totals): never half-trusted."""
    if manifest is None or set(manifest.plates) != set(plate_nums):
        return None
    totals = [0] * len(wanted)
    for counts in manifest.plates.values():
        for idx, n in counts.items():
            if not 0 <= idx < len(wanted):
                return None
            totals[idx] += n
    # A bundle (a 3MF plate) is several parts per copy (BIZ-356): count copies, from the manifest's requested instances.
    requested = getattr(manifest, "requested", None) or {}
    per_copy = [max(1, requested.get(i, w[1]) // w[1]) for i, w in enumerate(wanted)]
    if totals != [w[1] * per_copy[i] for i, w in enumerate(wanted)]:
        return None
    by_file: dict[str, list] = {}
    for item in group_items:
        by_file.setdefault(str(item.file_id), []).append(item)
    left = {item.id: item.quantity for item in group_items}
    by_ref = {f"item-{item.id}": [item] for item in group_items}
    seen = [0] * len(wanted)          # instances of each input on the plates so far: copies are credited cumulatively, so a
    out: list[dict[str, int]] = []    # split bundle spread over plates still sums to its quantity
    for plate in plate_nums:
        q = {str(item.id): 0 for item in group_items}
        for idx, n in sorted(manifest.plates[plate].items()):
            before = seen[idx]
            seen[idx] += n
            n = seen[idx] // per_copy[idx] - before // per_copy[idx]
            ref = wanted[idx][2]
            for item in by_ref.get(ref) or by_file.get(ref, []):
                take = min(n, left[item.id])
                q[str(item.id)] += take
                left[item.id] -= take
                n -= take
        out.append(q)
    return out


def _stored_manifest(store: ArtifactStore, key: str, constructor: ProjectConstructor):
    """The constructor's manifest for a stored pack (the reader needs a path, so the artifact is materialized)."""
    try:
        with store.materialize(key) as local:
            return constructor.plate_manifest(Path(local))
    except Exception:            # a third-party provider that breaks the "never raises" rule must not fail a committed pack
        logger.warning("plate manifest read failed for %s; using round-robin plate counts", key, exc_info=True)
        return None


def _stored_plate_nums(store: ArtifactStore, key: str) -> list[int]:
    """Plate numbers of a stored pack: the zip reader needs a path, so the artifact is materialized."""
    with store.materialize(key) as local:
        return _parse_plate_nums(local)


def _construct_stored_models(store: ArtifactStore, constructor: ProjectConstructor, wanted: list[tuple[str, int, str]],
                             target: TargetPrinter, cancel: CancelToken | None = None) -> bytes:
    """Build one project from the stored STLs `wanted` = (key, quantity, ref), each passed once. The constructor takes paths,
    so each artifact is materialized for the duration of the call. Blocking: call from a worker thread."""
    with contextlib.ExitStack() as stack:
        inputs = tuple(ConstructInput(stack.enter_context(store.materialize(w[0])), w[1], w[2],
                                      **({k: v for k, v in w[3].items() if v is not None} if len(w) > 3 else {}))
                       for w in wanted)
        out_dir = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        return constructor.construct(ConstructRequest(inputs, target), out_dir, cancel=cancel).path.read_bytes()


async def _require_ready(constructor: ProjectConstructor) -> None:
    """The readiness half of `resolve_construct`: probed only when a group actually has to be built (a reused pack needs no
    provider), at most once per request."""
    status = await asyncio.to_thread(constructor.status)
    if resolve_construct(constructor, status).action is Action.BLOCKED:
        raise HTTPException(503, f"The project builder is unavailable: {status.detail or status.state.value}")


async def _construct_in_thread(store: ArtifactStore, constructor: ProjectConstructor, wanted: list[tuple[str, int, str]],
                               target: TargetPrinter) -> bytes:
    """`_construct_stored_models` in a worker thread. If the request is cancelled meanwhile, the thread runs on, so the
    provider is told to stop (and discard its result) through the cancel token."""
    cancel = CancelToken()
    try:
        return await asyncio.to_thread(_construct_stored_models, store, constructor, wanted, target, cancel)
    except asyncio.CancelledError:
        cancel.cancel()
        raise


def _construct_http_error(err: ProviderError) -> HTTPException:
    if err.code in (ErrorCode.NOT_READY, ErrorCode.UNREACHABLE):
        return HTTPException(503, f"The project builder is unavailable: {err}")
    if err.code is ErrorCode.TIMEOUT:
        return HTTPException(504, "Generation timed out — try fewer parts or reduce quantities")
    if err.code in (ErrorCode.UNSUPPORTED_INPUT, ErrorCode.UNSUPPORTED_TARGET):
        return HTTPException(422, f"The project builder cannot build this project: {err}")
    return HTTPException(502, f"Project builder error during generation: {err}")


async def _reusable_pack(session: AsyncSession, recipe_hash: str) -> UploadedFile | None:
    """The newest earlier pack made from the same recipe whose file is still on disk, or None."""
    rows = (await session.execute(
        select(UploadedFile).where(UploadedFile.pack_recipe_hash == recipe_hash, UploadedFile.missing.is_(False))
        .order_by(UploadedFile.id.desc())
    )).scalars().all()
    for f in rows:
        if not f.content_hash:
            continue
        fresh = await asyncio.to_thread(
            fresh_content_hash, _projects.get_library_store(), f.relative_path, f.content_hash, f.size_bytes, f.mtime)
        if fresh is not None and fresh[0] == f.content_hash:   # present and not edited since it was packed
            return f
    return None


def _filament_label(ask: FilamentTuple) -> str:
    """Short string for use in generated 3MF filenames."""
    parts = [p.lower() for p in (ask.manufacturer, ask.type) if p]
    if ask.subtype and ask.subtype.casefold() not in ("any", "standard"):
        parts.append(ask.subtype.lower())
    if ask.color:
        parts.append(ask.color.lstrip("#"))
    return "-".join(parts) if parts else "any"


@router.post(
    "/{project_id}/generate",
    summary="Pack STLs and queue jobs",
    responses={
        404: {"description": "Project not found or a referenced file is missing"},
        422: {"description": "Project has no items or contains non-STL files"},
        502: {"description": "The project builder failed during generation"},
        503: {"description": "The project builder is not ready"},
        504: {"description": "Generation timed out"},
        409: {"description": "Project is a draft, or another request with this Idempotency-Key is still running"},
    },
    dependencies=[Depends(require_scope("projects:write"))],
)
async def generate_project(
    project_id: int,
    body: GenerateRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key", description="Repeat-safe generation: a retry returns the first result"),
) -> dict:
    """Pack project STL items into 3MF files (one per filament group), save them to the library,
    and queue one job per plate. Returns created job and file IDs plus the bed dimensions used
    for packing.
    Send an `Idempotency-Key` header to make the call safe to retry: a repeat with the same key and body returns the first result
    (`Idempotent-Replay: true`) and creates no further files or jobs."""
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    scope = f"POST /api/v1/projects/{project_id}/generate"
    if idempotency_key:
        replay = await idempotency.claim(factory, scope, idempotency_key, idempotency.request_hash(project_id, body.model_dump()))
        if replay is not None:
            response.status_code, response.headers["Idempotent-Replay"] = replay.status_code, "true"
            return replay.response
    jobs_before = await _job_count(factory, project_id) if idempotency_key else 0
    try:
        result = await _generate_project(project_id, body, session)
    except BaseException:
        if idempotency_key:
            created = await _job_count(factory, project_id) - jobs_before
            if created > 0:       # some groups were committed: a retry under this key would generate them again
                await idempotency.partial(factory, scope, idempotency_key, f"This request created {created} job(s) for project {project_id} "
                                          "before it failed: check the project's jobs, then retry with a new Idempotency-Key")
            else:
                await idempotency.release(factory, scope, idempotency_key)
        raise
    if idempotency_key:
        await _remember(factory, scope, idempotency_key, 200, result)
    return result


class HeldAction(BaseModel):
    action: Literal["reprint", "drop"]


@router.get("/{project_id}/review", summary="Project Builder review checks (BIZ-363)",
            description="What would stop the project queueing well, each naming the Builder tab that fixes it (`details`, `items`, "
                        "`groups`): `error` blocks queueing, `note` informs, `ok` confirms. `open` counts the errors. Checks: draft "
                        "stage, groups without eligible printers, \"Printer default\" on a printer with no default process profile, "
                        "parts that fit no eligible bed (X/Y either way round, and Z when known), loaded colours, gcode eligibility.",
            dependencies=[Depends(require_scope("projects:read"))])
async def project_review(project_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    from ...services import project_review as pr
    proj = await _get_project_or_404(project_id, session)
    return pr.as_response(await pr.review(session, proj, _projects.get_project_constructor()))


@router.get("/{project_id}/failures", summary="The project's failure log (BIZ-364)",
            description="Every reported failure, oldest first: part, job, printer, when, and what happened to the failed parts "
                        "(`requeued`, `held`, `reprinted`, `dropped`).",
            dependencies=[Depends(require_scope("projects:read"))])
async def project_failures(project_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    await _get_project_or_404(project_id, session)
    return await print_results.failure_log(session, project_id)


@router.post("/{project_id}/items/{item_id}/held", summary="Reprint or drop an item's held parts (BIZ-364)",
             responses={409: {"description": "Nothing of this item is held"}},
             dependencies=[Depends(require_scope("projects:write"))])
async def resolve_held_parts(project_id: int, item_id: int, body: HeldAction, session: AsyncSession = Depends(get_session)) -> dict:
    await _get_project_or_404(project_id, session)
    out = await print_results.resolve_held(session, project_id, item_id, body.action)
    await session.commit()
    return out


async def _gcode_targets(session: AsyncSession, f: UploadedFile, resolved):
    """A gcode item's eligibility within its group: only printers that take the file as is (format + machine eligibility,
    BIZ-188/263), as job creation enforces; model targets are re-checked per printer when they materialize. 422 (409 when only
    unknown eligibility stands in the way) when no printer of the group could print it."""
    from ...services import gcode_eligibility
    keep, unknown = [], False
    for p, profile in resolved.printers:
        if not model_targets.accepts_file(p.printer_type, f.original_filename):
            continue
        outcome = await gcode_eligibility.evaluate(session, f, p)
        if outcome.allowed:
            keep.append((p, profile))
        else:
            unknown = unknown or outcome.code == "unknown_eligibility"
    models = []
    for uuid, profile in resolved.models:
        printers = await model_targets.printers_of_target(session, uuid)
        if not printers or any(model_targets.accepts_file(p.printer_type, f.original_filename) for p in printers):
            models.append((uuid, profile))
    if (resolved.printers or resolved.models) and not keep and not models:
        raise HTTPException(409 if unknown else 422,
                            f"No printer of this group can print {f.original_filename} as sliced"
                            + (": set the printer models it is for (its eligibility is unknown)" if unknown else ""))
    return project_groups.ResolvedTargets(keep, models, resolved.covered)


async def _plan_group_packs(session: AsyncSession, pgroup: ProjectGroup, pg_items: list, targets: list[tuple[str, str]],
                            library) -> tuple[list[dict], dict, list[Printer]]:
    """The packs one print group needs for `pg_items` (one per filament ask), its response entry, and the explicit printers its
    targets resolve to. `pg_items` are project items, or stand-ins carrying a smaller `quantity` (a requeue of failed parts)."""
    packs: list[dict] = []
    envelope = (float(pgroup.envelope_x), float(pgroup.envelope_y)) if pgroup.envelope_x and pgroup.envelope_y else None
    target_intents = await project_groups.targets_of(session,pgroup.id)
    resolved = await project_groups.resolve(session, targets, envelope)
    pack_bed_x, pack_bed_y = envelope or project_groups.auto_envelope(resolved.covered)
    bed_models = {p.model_uuid for p in resolved.covered}
    target = TargetPrinter((pack_bed_x, pack_bed_y, 250.0), next(iter(bed_models)) if len(bed_models) == 1 else None)
    out = {"id": pgroup.id, "pack_bed_x": pack_bed_x, "pack_bed_y": pack_bed_y}
    if not resolved.printers and not resolved.models:
        # Its jobs will sit unclaimed (the unmatched sweep blocks them); say so instead of a silent 200.
        out["warning"] = (f"{pgroup.name}: no printer can take these jobs" + (
            " (the envelope leaves out every selected printer)" if targets else " (choose eligible printers)"))
    
    # Group items by filament requirement (the ask tuple)
    by_ask: dict[FilamentTuple, list[ProjectItem]] = {}
    for item in pg_items:
        by_ask.setdefault(row_tuple(item), []).append(item)
    # Resolve model keys per ask (+ what each pack is made from, for pack reuse). BIZ-356: Locked plates and gcode are fixed
    # jobs; a plain split STL merges with other copies of its file; every other item is its own input (object / plate + rule).
    for ask, ask_items in by_ask.items():
        wanted: dict[str, tuple] = {}
        recipe: list[tuple] = []
        packed: list = []
        for item in ask_items:
            f = await session.get(UploadedFile, item.file_id)
            if f is None:
                raise HTTPException(422, f"File {item.file_id} not found in library")
            kind, rule = getattr(item, "kind", None) or "stl", getattr(item, "pack_rule", None) or "split"
            if kind == "gcode" and (pgroup.overrides or any(t.overrides for t in target_intents)):
                raise HTTPException(422, "Process overrides cannot apply to pre-sliced G-code; move it to a group without overrides")
            if not await _stored(library, f.relative_path):
                raise HTTPException(422, f"{f.original_filename!r} is missing from disk")
            await _projects.refresh_content_hash(f, library)
            if kind in ("3mf_object", "3mf_plate") and getattr(item, "source_hash", None) and f.content_hash != item.source_hash:
                raise HTTPException(422, f"{f.original_filename!r} changed since its {kind.split('_')[1]} was picked: pick it again")
            if kind == "gcode" or (kind == "3mf_plate" and rule == "locked"):
                own = await _gcode_targets(session, f, resolved) if kind == "gcode" else resolved
                packs.append({"group_id": pgroup.id, "overrides": pgroup.overrides, "ask": ask, "items": [item], "wanted": [], "recipe": [], "target": target,
                              "resolved": own, "pack_mode": {},
                              "fixed": {"file": f, "plate": int(item.source_ref or 1), "copies": item.quantity}})
                continue
            if kind == "stl" and not f.original_filename.lower().endswith(".stl"):
                raise HTTPException(400, f"File {f.original_filename!r} is not an STL — pick a plate or an object of it")
            content = f.content_hash or f"file:{f.id}:{f.size_bytes}:{f.mtime}"
            packed.append(item)
            if kind == "stl" and rule == "split":
                prior = wanted[f.relative_path][1] if f.relative_path in wanted else 0
                wanted[f.relative_path] = (f.relative_path, prior + item.quantity, str(f.id))
                recipe.append((content, item.quantity))
            else:
                select = {"object_id": item.source_ref if kind == "3mf_object" else None,
                          "plate": int(item.source_ref) if kind == "3mf_plate" else None, "rule": rule}
                wanted[f"item-{item.id}"] = (f.relative_path, item.quantity, f"item-{item.id}", select)
                recipe.append((content, item.quantity, kind, item.source_ref, rule))
        if packed:
            packs.append({"group_id": pgroup.id, "overrides": pgroup.overrides, "ask": ask, "items": packed, "wanted": list(wanted.values()),
                          "recipe": recipe, "target": target, "resolved": resolved,
                          "pack_mode": {"mode": "geometry", "bed": [pack_bed_x, pack_bed_y]}})
    return packs, out, [p for p, _ in resolved.printers]


async def _queue_packs(session: AsyncSession, proj: Project, packs: list[dict], constructor, *, allow_cached: bool,
                      save_slice: bool, artifact_cleanup: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Build (or reuse) each planned pack, store it, and queue one job per plate with the pack's printer configs and model
    targets, in `packs` order. Returns (jobs, files) for the response."""
    library = _projects.get_library_store()
    checked_ready = False
    # Pack output is staged under a throwaway folder and moved to its job-ID folder once the IDs are known; this store
    # prunes the emptied staging folder (the library store keeps folders, which are user-visible objects).
    pack_store = _projects.get_library_store(prune_empty=True)

    jobs_out: list[dict] = []
    files_out: list[dict] = []

    for pack in packs:
        ask, stl_wanted, group_items, target = pack["ask"], pack["wanted"], pack["items"], pack["target"]
        fixed = pack.get("fixed")     # BIZ-356: a Locked plate or gcode item prints its own file as is, one job per copy
        if fixed is not None:
            reused = new_file = fixed["file"]          # never packed, moved or deleted
            plate_nums = [fixed["plate"]] * fixed["copies"]
            now = _now_iso()
        else:
            # Same STLs x quantities, same bed / pack mode => the same pack: reuse it rather than re-pack, so the cached
            # slices keyed on that file still apply (BIZ-193).
            recipe_hash = slice_cache.sha256_of({"items": pack["recipe"], "pack": pack["pack_mode"]})
            reused = await _reusable_pack(session, recipe_hash) if allow_cached else None
            now = _now_iso()

            if reused is not None:
                new_file = reused
                plate_nums = [p.get("plate_number") for p in (reused.plates or []) if p.get("plate_number") is not None]
                slice_cache.log_event("pack_reused", project_id=proj.id, recipe_hash=recipe_hash, pack_file_id=reused.id,
                                      pack_content_hash=reused.content_hash)
            else:
                if not checked_ready:
                    await _require_ready(constructor)
                    checked_ready = True
                try:
                    # Geometry-only, whatever presets the project carries: slicing profiles are applied at dispatch time.
                    packed_bytes = await _construct_in_thread(library, constructor, stl_wanted, target)
                except ArtifactError as exc:   # an STL vanished or became unreadable between the check and the pack
                    raise HTTPException(422, f"Could not read a model file for generation: {exc}") from exc
                except ProviderError as exc:
                    raise _construct_http_error(exc) from exc

                label = _filament_label(ask)
                out_filename = f"project-{_slugify(proj.name)}-{label}.3mf"
                # Write to a temp subdirectory; renamed to the job-ID subfolder once IDs are known.
                tmp_key = f"{PACK_FOLDER}/_tmp_{_uuid.uuid4().hex[:10]}/{out_filename}"
                published = await asyncio.to_thread(pack_store.put, tmp_key, io.BytesIO(packed_bytes))
                if artifact_cleanup is not None:
                    artifact_cleanup.append(tmp_key)
                # A real content hash: the slicing cache keys versions on it (an empty one is uncacheable), and the
                # library's dedup/move detection relies on it too.
                pack_hash = hashlib.sha256(packed_bytes).hexdigest()

                plate_nums = await asyncio.to_thread(_stored_plate_nums, pack_store, tmp_key)

                new_file = UploadedFile(
                    original_filename=out_filename,
                    plates=[{"plate_number": p, "thumbnail_path": None} for p in plate_nums],
                    uploaded_at=now,
                    relative_path=tmp_key,
                    folder=f"/{PACK_FOLDER}",
                    size_bytes=published.size,
                    content_hash=pack_hash,
                    mtime=published.mtime,
                    missing=False,
                    pack_recipe_hash=recipe_hash,
                )
                session.add(new_file)
                await session.commit()
                await session.refresh(new_file)
                slice_cache.log_event("pack_new", project_id=proj.id, recipe_hash=recipe_hash, pack_file_id=new_file.id,
                                      pack_content_hash=pack_hash)

        effective_plates = plate_nums or [1]
        if fixed is not None:
            plate_item_qtys = [{str(group_items[0].id): 1} for _ in effective_plates]
        else:
            # Real per-plate counts from the pack's manifest (BIZ-359); the round-robin guess only for a pack that reports none.
            manifest = await asyncio.to_thread(_stored_manifest, pack_store, new_file.relative_path, constructor)
            plate_item_qtys = _plate_item_quantities(manifest, stl_wanted, group_items, effective_plates)
            if plate_item_qtys is None:
                if manifest is not None:
                    logger.warning("project %s: pack manifest disagrees with the request; using round-robin plate counts", proj.id)
                plate_item_qtys = _round_robin_quantities(group_items, len(effective_plates))

        next_pos = await _max_queue_position(session) + 1.0
        new_jobs: list[Job] = []
        for plate_idx, plate_num in enumerate(effective_plates):
            job = Job(
                uploaded_file_id=new_file.id,
                plate_number=plate_num,
                project_id=proj.id,
                order_id=proj.order_id,
                queue_position=next_pos,
                status="queued",
                created_at=now,
                updated_at=now,
                project_item_quantities=json.dumps(plate_item_qtys[plate_idx]),
                save_slice=save_slice,
                allow_cached_slice=allow_cached,
                overrides=dict(pack["overrides"]) if pack.get("overrides") else None,
                min_bed_mm=bed_requirement.largest(group_items) if fixed is not None else None,
            )
            session.add(job)
            new_jobs.append(job)
            next_pos += 1.0
        # Commit jobs so the DB assigns their autoincrement IDs.
        await session.commit()
        # Refresh each job so j.id is populated.
        for j in new_jobs:
            await session.refresh(j)

        if reused is None:
            # Rename temp dir to the job-ID subfolder now that IDs are known.
            job_id_label = str(new_jobs[0].id) if len(new_jobs) == 1 else f"{new_jobs[0].id}-{new_jobs[-1].id}"
            final_key = f"{PACK_FOLDER}/{job_id_label}/{out_filename}"
            moved = await asyncio.to_thread(pack_store.move, tmp_key, final_key)
            if artifact_cleanup is not None:
                artifact_cleanup[-1] = moved.key
            new_file.relative_path = moved.key
            new_file.mtime = moved.mtime
            new_file.folder = f"/{PACK_FOLDER}/{job_id_label}"
            await session.commit()

        if fixed is None:
            cached_plates = (await session.execute(
                select(func.count(func.distinct(SlicedVersion.plate_number)))
                .join(UploadedFile, UploadedFile.id == SlicedVersion.file_id)
                .where(SlicedVersion.source_file_id == new_file.id, UploadedFile.missing.is_(False))
            )).scalar() or 0
            files_out.append({
                "id": new_file.id,
                "original_filename": new_file.original_filename,
                "folder": new_file.folder,
                "plate_count": len(plate_nums),
                # Slicing cache (informational — whether a version is used is decided when a printer claims each job).
                "pack_reused": reused is not None,
                "cached_plates": cached_plates,
            })

        # Create printer configs for each of the group's eligible printers × job, each with its target's process profile
        # ("" = the printer's own default, BIZ-358). The machine preset is deliberately NOT set here — queue_engine reads it
        # fresh from the printer at dispatch time.
        resolved = pack["resolved"]
        intent = {t.target_key:t for t in await project_groups.targets_of(session,pack["group_id"])}
        pgroup = await session.get(ProjectGroup,pack["group_id"])
        for j in new_jobs:
            j.printer_preference = pgroup.printer_preference
            for p, profile in resolved.printers:
                chosen = intent.get(str(p.id))
                session.add(JobPrinterConfig(
                    job_id=j.id,
                    printer_id=p.id,
                    print_profile=profile,
                    overrides=chosen.overrides if chosen else None,
                    filament_profile=chosen.filament_profile if chosen else None,
                    filament_map=chosen.filament_map if chosen else None,
                    **tuple_columns(ask),
                ))
            for model_uuid, profile in resolved.models:
                chosen = intent.get("model:"+model_uuid if model_uuid else "any")
                session.add(JobModelTarget(
                    job_id=j.id,
                    model_uuid=model_uuid,
                    print_profile=profile,
                    overrides=chosen.overrides if chosen else None,
                    filament_profile=chosen.filament_profile if chosen else None,
                    filament_map=chosen.filament_map if chosen else None,
                    **tuple_columns(ask),
                ))
            await session.flush()
            await model_targets.materialize_job(session, j.id)
        await session.commit()

        # Trigger estimates for each new job if enabled.
        queue_cfg = await session.get(QueueConfig, 1)
        est_enabled = queue_cfg is not None and queue_cfg.estimates_enabled
        if est_enabled and not session.info.get("slicer_transaction"):
            for j in new_jobs:
                j.estimate_token = (j.estimate_token or 0) + 1
                j.estimate_status = "pending"
            await session.commit()
            for j in new_jobs:
                _projects.queue_engine.spawn_estimate(j.id)

        jobs_out.extend({
            "id": j.id,
            "group_id": pack["group_id"],
            "uploaded_file_id": j.uploaded_file_id,
            "plate_number": j.plate_number,
            "queue_position": j.queue_position,
            "status": j.status,
        } for j in new_jobs)

    return jobs_out, files_out


async def _generate_project(project_id: int, body: GenerateRequest, session: AsyncSession) -> dict:
    proj = await _get_project_or_404(project_id, session)
    if proj.stage == "draft":
        raise HTTPException(409, "Promote the project to planning before creating jobs")

    # 3mf.construct is exclusive: the one active provider or the request is refused (BIZ-296). Its readiness is checked only
    # when a group needs building (`_require_ready`), so reusable packs and input errors don't wait on the provider.
    constructor = _projects.get_project_constructor()
    if resolve_construct(constructor, None).reason is Reason.NO_PROVIDER:
        raise HTTPException(422, "No project builder is configured — set up the plugin that provides 3mf.construct "
                                 "(Settings -> Plugins); it is required for generation")
    item_rows = (
        await session.execute(
            select(ProjectItem)
            .where(ProjectItem.project_id == project_id)
            .order_by(ProjectItem.sort_order, ProjectItem.id)
        )
    ).scalars().all()

    if not item_rows:
        raise HTTPException(422, "Project has no items — add STL files before generating")

    # Print groups (BIZ-360), in queue order. A Default Group with no saved targets takes the request's eligibility (the
    # pre-groups API), so existing callers keep working.
    print_groups = await project_groups.groups_of(session, project_id)
    legacy = [(str(pid), body.process_preset or "") for pid in dict.fromkeys(body.eligible_printer_ids)]
    legacy += ([(project_groups.ANY_KEY, body.process_preset or "")] if body.eligible_any_printer else
               [(project_groups.MODEL_PREFIX + u, body.process_preset or "") for u in dict.fromkeys(body.eligible_model_uuids)])

    library = _projects.get_library_store()
    packs: list[dict] = []          # one per (print group, filament ask), in queue order
    groups_out: list[dict] = []
    all_explicit: dict[int, Printer] = {}
    for pgroup in print_groups:
        pg_items = [i for i in item_rows if i.group_id == pgroup.id]
        if not pg_items:
            continue                                         # an empty group adds nothing to the queue
        saved = [(t.target_key, t.process_profile) for t in await project_groups.targets_of(session, pgroup.id)]
        targets = saved or (legacy if pgroup.is_default else [])
        if pgroup.strategy == "pool":
            # BIZ-362: Split / Together items wait in the pool for printers to claim; the pool takes its place in the queue.
            from ...services import pools
            if any(pools.poolable(i) for i in pg_items):
                pgroup.queue_position = await _max_queue_position(session) + 1.0
                await session.commit()
            pg_items = [i for i in pg_items if not pools.poolable(i)]
            if not pg_items:
                groups_out.append({"id": pgroup.id, "pool": True})
                continue
        group_packs, out, explicit = await _plan_group_packs(session, pgroup, pg_items, targets, library)
        packs.extend(group_packs)
        groups_out.append(out)
        all_explicit.update({p.id: p for p in explicit})

    # Clean up legacy single-result file unless an active job holds it
    if proj.result_file_id is not None:
        active = (
            await session.execute(
                select(Job.id)
                .where(
                    Job.uploaded_file_id == proj.result_file_id,
                    Job.status.in_(ACTIVE_JOB_STATUSES),
                )
                .limit(1)
            )
        ).first()
        if active is None:
            old_file = await session.get(UploadedFile, proj.result_file_id)
            # Never delete a file the slicing cache still relies on (a reusable pack, or one with cached slices).
            if old_file and (old_file.pack_recipe_hash or (await session.execute(
                    select(SlicedVersion.id).where(SlicedVersion.source_file_id == old_file.id).limit(1))).first()):
                old_file = None
            if old_file:
                await asyncio.to_thread(library.delete, old_file.relative_path, missing_ok=True)
                await session.delete(old_file)
        proj.result_file_id = None
        await session.commit()

    jobs_out, files_out = await _queue_packs(session, proj, packs, constructor,
                                             allow_cached=body.allow_cached, save_slice=body.save_slice)
    packed_groups = [g for g in groups_out if not g.get("pool")]

    proj.updated_at = _now_iso()
    await session.commit()
    if jobs_out:
        await project_events.publish("project.generated", proj, job_ids=[j["id"] for j in jobs_out])

    return {
        "project_id": proj.id,
        "jobs": jobs_out,
        "files": files_out,
        "eligible_printer_ids": list(all_explicit),
        # The first group's pack bed (the pre-groups shape); every group's is in `groups`.
        "pack_bed_x": packed_groups[0]["pack_bed_x"] if packed_groups else 256.0,
        "pack_bed_y": packed_groups[0]["pack_bed_y"] if packed_groups else 256.0,
        "groups": groups_out,
    }
