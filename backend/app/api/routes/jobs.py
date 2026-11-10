from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import GcodeFile, Job, JobModelTarget, JobPrinterConfig, Order, Printer, PrinterModelRecord, Project, QueueConfig, SlicedVersion, UploadedFile
from ...services import job_details, slice_cache
from ...services import gcode_eligibility as eligibility
from ...services.library_scanner import file_kind, is_presliced_file
from ...services.library_store import get_library_store, with_local_path
from ...services.three_mf_parser import source_has_project_settings
from ...services import filament_matching, model_targets, scheduling, slicer_profiles
from ...services.queue_engine import effective_process_profile, queue_engine, _slot_for_config
from ...plugins.capabilities.filament_inventory import InvSpool
from ._filament_ask import AskFields, ask_columns, ask_out, type_labels
from ...services.inventory import read as inventory_read, refs as inventory_refs
from ...services.inventory.preflight import check_spool_sufficiency
from ...services.providers.contract import OverrideInspection, PlateEstimate
from ...services.providers.slicing import get_format_provider
from ...services.printer_manager import printer_manager

logger = logging.getLogger(__name__)

def _inspection_json(i: OverrideInspection) -> dict:
    """The check-overrides response body for an `OverrideInspection` DTO (keys unchanged: `from`/`to`, ...)."""
    return {
        "has_embedded_settings": i.has_embedded_settings,
        "setting_changes": [{"key": c.key, "from": c.from_value, "to": c.to_value} for c in i.setting_changes],
        "slot_warning": ({"used_slots": i.slot_warning.used_slots, "printer_slots": i.slot_warning.printer_slots}
                         if i.slot_warning else None),
        "has_findings": i.has_findings,
    }


def _clean_overrides(o: dict | None) -> dict | None:
    """Strip any key not in the curated allowlist before storing."""
    if not o:
        return None
    allowed = frozenset(s.key for s in get_format_provider().curated_settings())
    cleaned = {k: str(v) for k, v in o.items() if k in allowed}
    return cleaned or None


# Statuses where a printer is physically working on the job and must be told to stop.
_PRINTER_ACTIVE_STATUSES = {"printing", "paused", "uploading"}


# Collaborators the modules imported at the bottom of this file call as `_jobs.<name>` rather than importing
# themselves: tests swap them out on THIS module, so this is the one place they are looked up.
_PATCH_POINTS = (queue_engine, printer_manager, get_format_provider)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

_CANCELLABLE_STATUSES = {"queued", "blocked", "slicing", "sliced", "uploading", "printing", "paused", "failed"}


class FilamentMapEntry(AskFields):
    """One model color: pinned to a tool slot (`tool_index`), or a tuple ask (`filament_*`) resolved against the printer's slots."""
    model_filament: int | None = Field(default=None, ge=1, le=filament_matching.MAX_MODEL_FILAMENTS)
    tool_index: int | None = Field(default=None, ge=0, le=255)


class PrinterConfigInput(AskFields):
    printer_id: int
    print_profile: str = ""   # OrcaSlicer process preset; not used for pre-sliced (.gcode / .gcode.3mf) jobs
    filament_profile: str | None = None
    tool_index: int | None = None
    filament_map: list[dict] | None = None

    @field_validator("filament_map")
    @classmethod
    def _validate_filament_map(cls, v: list[dict] | None) -> list[dict] | None:
        """Reject malformed entries (e.g. non-dict items, non-numeric tool_index, a type without a subtype)
        at creation time instead of letting them poison the queue engine later."""
        if v is None:
            return None
        return [FilamentMapEntry.model_validate(e).model_dump() for e in v]


class ModelTargetInput(AskFields):
    """Eligible on *any* printer of the registry model `model_uuid` (BIZ-294); null = any printer at all (BIZ-357).
    No `tool_index` / slot-based `filament_map`: slot numbers differ from printer to printer."""
    model_uuid: str | None
    print_profile: str = ""
    filament_profile: str | None = None
    filament_map: list[dict] | None = None

    @field_validator("model_uuid")
    @classmethod
    def _model_uuid_set(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("model_uuid must not be blank")
        return v

    @field_validator("filament_map")
    @classmethod
    def _no_slot_pinning(cls, v: list[dict] | None) -> list[dict] | None:
        if v is None:
            return None
        entries = [FilamentMapEntry.model_validate(e).model_dump() for e in v]
        if any(e.get("tool_index") is not None for e in entries):
            raise ValueError("tool_index is printer-specific and can't be used with a make/model target")
        return entries


def _apply_gcode_estimate(job: Job, uploaded_file: UploadedFile) -> None:
    """A .gcode job needs no background test-slice: its estimate is already in the file's header."""
    est = with_local_path(get_library_store(), uploaded_file.relative_path,
                          lambda path: get_format_provider().parse_estimates(path, job.plate_number),
                          PlateEstimate(grams=None, seconds=None))
    grams, secs, per_extruder = est.grams, est.seconds, est.per_extruder_grams
    if grams is None and secs is None:
        job.estimate_status = None
        return
    job.estimate_status = "done"
    job.estimate_seconds = secs
    job.estimate_filament_grams = grams
    job.estimate_filament_breakdown = (
        [{"extruder_index": i, "filament_profile": None, "grams": g} for i, g in enumerate(per_extruder)]
        if per_extruder else None
    )
    job.estimate_preset_label = None


async def _check_gcode_printers(
    session: AsyncSession, uploaded_file: UploadedFile | None,
    configs: list[PrinterConfigInput], targets: list[ModelTargetInput],
) -> None:
    """A pre-sliced file can only go to a vendor that prints that format as-is: a raw .gcode not to one that only
    ingests a sliced archive (e.g. Bambu), a .gcode.3mf archive only to one that does (BIZ-190). Also refuses a
    duplicated printer config (one config per job+printer)."""
    seen: set[int] = set()
    for cfg in configs:
        if cfg.printer_id in seen:
            raise HTTPException(422, f"Printer {cfg.printer_id} is listed more than once")
        seen.add(cfg.printer_id)
    if not is_presliced_file(uploaded_file):
        return
    filename = uploaded_file.original_filename
    if file_kind(filename) == "gcode_3mf":
        why = "only prints raw .gcode, not a sliced .gcode.3mf archive"
    else:
        why = "can't print a raw .gcode file — it needs a sliced 3MF"
    for cfg in configs:
        printer = await session.get(Printer, cfg.printer_id)
        if printer is not None and not model_targets.accepts_file(printer.printer_type, filename):
            raise HTTPException(422, f"{printer.name} {why}")
    for t in targets:
        matching = await model_targets.printers_of_target(session, t.model_uuid)
        if matching and not any(model_targets.accepts_file(p.printer_type, filename) for p in matching):
            label = await _target_label(session, t.model_uuid)
            raise HTTPException(422, f"No {label} printer can take this file: each one {why}")


async def _check_machine_eligibility(
    session: AsyncSession, uploaded_file: UploadedFile | None, configs: list[PrinterConfigInput],
    targets: list[ModelTargetInput], confirmed: bool,
) -> None:
    """Pre-sliced G-code only goes to printers whose model it is eligible for (BIZ-263). Every picked printer is judged on its
    own and the refusal names each one: 422 for an incompatible printer / one with no registered model, 409 when the file's
    eligibility is simply unknown and the user has not confirmed. A make/model target is refused when no printer on that
    machine preset could take the file."""
    if not is_presliced_file(uploaded_file):
        return
    refused: list[eligibility.Outcome] = []
    for cfg in configs:
        printer = await session.get(Printer, cfg.printer_id)
        if printer is None:
            continue
        outcome = await eligibility.evaluate(session, uploaded_file, printer, confirmed=confirmed)
        if not outcome.allowed:
            refused.append(outcome)
    for t in targets:
        matching = await model_targets.printers_of_target(session, t.model_uuid)
        outcomes = [await eligibility.evaluate(session, uploaded_file, p, confirmed=confirmed) for p in matching]
        if outcomes and not any(o.allowed for o in outcomes):
            label = await _target_label(session, t.model_uuid)
            refused.append(eligibility.Outcome(False, outcomes[0].code, f"No {label} printer can take this file: "
                                                                        f"{outcomes[0].message}"))
    if not refused:
        return
    unknown = all(o.code == "unknown_eligibility" for o in refused)
    raise HTTPException(409 if unknown else 422, "; ".join(o.message for o in refused))


async def _cached_version_of(session: AsyncSession, uploaded_file: UploadedFile | None) -> SlicedVersion | None:
    if uploaded_file is None:
        return None
    return (await session.execute(
        select(SlicedVersion).where(SlicedVersion.file_id == uploaded_file.id))).scalar_one_or_none()


async def _check_version_printers(
    session: AsyncSession, version: SlicedVersion | None,
    configs: list[PrinterConfigInput], targets: list[ModelTargetInput],
) -> None:
    """A cached version was sliced for one make/model: refuse printers/targets of any other (BIZ-193)."""
    if version is None:
        return
    for cfg in configs:
        printer = await session.get(Printer, cfg.printer_id)
        if printer is not None and await slicer_profiles.machine_profile_for(session, printer) != version.machine_preset:
            raise HTTPException(422, f"{printer.name} isn't a {version.machine_preset} — this cached version was "
                                     "sliced for that printer model")
    for t in targets:
        if t.model_uuid is None:
            raise HTTPException(422, f"This cached version was sliced for {version.machine_preset}: pick that printer model, "
                                     "not Any printer")
        if version.machine_preset not in await slicer_profiles.profiles_of_model(session, t.model_uuid):
            label = await slicer_profiles.model_display_name(session, t.model_uuid)
            raise HTTPException(422, f"This cached version was sliced for {version.machine_preset}, not "
                                     f"{label}")


async def _target_label(session: AsyncSession, model_uuid: str | None) -> str:
    return "Any printer" if model_uuid is None else await slicer_profiles.model_display_name(session, model_uuid)


def _validate_targets(targets: list[ModelTargetInput]) -> None:
    seen: set[str | None] = set()
    for t in targets:
        if t.model_uuid in seen:
            raise HTTPException(422, "Any printer is listed more than once" if t.model_uuid is None
                                else f"Duplicate make/model target {t.model_uuid!r}")
        seen.add(t.model_uuid)


async def _require_known_models(session: AsyncSession, targets: list[ModelTargetInput]) -> None:
    for t in targets:
        if t.model_uuid is not None and await session.get(PrinterModelRecord, t.model_uuid) is None:
            raise HTTPException(422, f"Unknown printer model {t.model_uuid!r}")


def _add_targets(session: AsyncSession, job_id: int, targets: list[ModelTargetInput]) -> None:
    for t in targets:
        session.add(JobModelTarget(
            job_id=job_id,
            model_uuid=t.model_uuid,
            print_profile=t.print_profile,
            # Only a real preset: "any"/a bare type here would be sent to the slicer as a preset name. Left unset,
            # each printer's loaded slot supplies the preset.
            filament_profile=t.filament_profile or None,
            **ask_columns(t),
            filament_map=t.filament_map,
        ))


class OverrideCheckRequest(BaseModel):
    uploaded_file_id: int
    printer_id: int
    print_profile: str
    filament_profile: str | None = None
    filament_color: str | None = None


def _normalise_not_before(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    try:
        return scheduling.parse_not_before(v)
    except ValueError:
        raise ValueError("not_before must be an ISO-8601 timestamp")


class JobCreate(BaseModel):
    uploaded_file_id: int
    plate_number: int = 1
    order_id: int | None = None
    # Link to a project (customer or internal work). The job then takes the project's order grouping when the
    # project has one (else `order_id` stands). Not allowed while the project is still a draft.
    project_id: int | None = None
    printer_configs: list[PrinterConfigInput] = []
    # Make/model eligibility ("any Bambu P1S"); at least one printer config or target is required.
    model_targets: list[ModelTargetInput] = []
    overrides: dict | None = None
    not_before: str | None = None
    # Slicing cache (BIZ-192): keep this job's production slice in the library, next to the model, as a cached version.
    save_slice: bool = False
    save_slice_name: str | None = Field(default=None, max_length=200)
    # A pre-sliced file with no recorded machine eligibility (legacy) is refused until the user confirms it fits (BIZ-263).
    confirm_unknown_eligibility: bool = False

    @field_validator("not_before")
    @classmethod
    def _valid_not_before(cls, v: str | None) -> str | None:
        return _normalise_not_before(v)


async def _linkable_project(session: AsyncSession, project_id: int) -> Project:
    """The project a job may be attached to, or 404 / 409 (a draft project can't have jobs yet)."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    if project.stage == "draft":
        raise HTTPException(409, "Promote the project to planning before adding jobs")
    return project


def _to_dict(j: Job) -> dict:
    return {
        "id": j.id,
        "uploaded_file_id": j.uploaded_file_id,
        "plate_number": j.plate_number,
        "order_id": j.order_id,
        "project_id": j.project_id,
        "assigned_printer_id": j.assigned_printer_id,
        "queue_position": j.queue_position,
        "status": j.status,
        "overrides": j.overrides,
        "outcome": j.outcome,
        "project_item_quantities": json.loads(j.project_item_quantities) if j.project_item_quantities else None,
        "created_at": j.created_at,
        "updated_at": j.updated_at,
        "completed_at": j.completed_at,
        # Actual values (populated at production slice time)
        "actual_filament_grams": j.actual_filament_grams,
        "actual_seconds": j.actual_seconds,
        "actual_filament_breakdown": j.actual_filament_breakdown,
        "deduction_skipped": j.deduction_skipped,
        "deduction_note": j.deduction_note,
        # Estimate values (populated after background test slice)
        "estimate_status": j.estimate_status,
        "estimate_seconds": j.estimate_seconds,
        "estimate_filament_grams": j.estimate_filament_grams,
        "estimate_filament_breakdown": j.estimate_filament_breakdown,
        "estimate_preset_label": j.estimate_preset_label,
        "filament_cost": j.filament_cost,
        "not_before": j.not_before,
        # Slicing cache (BIZ-191..193)
        "save_slice": bool(j.save_slice),
        "save_slice_name": j.save_slice_name,
        "allow_cached_slice": bool(j.allow_cached_slice),
        "sliced_version_id": j.sliced_version_id,
        "slice_cache_info": j.slice_cache_info,
    }


async def _get_or_404(job_id: int, session: AsyncSession) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, f"Job {job_id} not found")
    return job


async def _next_queue_position(session: AsyncSession) -> float:
    result = await session.execute(
        select(func.max(Job.queue_position)).where(
            Job.status.not_in(["complete", "failed", "cancelled"])
        )
    )
    current_max = result.scalar()
    return (current_max or 0.0) + 1.0


async def _front_queue_position(session: AsyncSession) -> float:
    """Position just ahead of the current queue front (for re-queueing at the top)."""
    result = await session.execute(
        select(func.min(Job.queue_position)).where(
            Job.status.in_(["queued", "blocked"])
        )
    )
    current_min = result.scalar()
    return (current_min or 1.0) - 1.0


@router.post(
    "",
    status_code=201,
    summary="Create job",
    responses={
        404: {"description": "File, printer, or order not found"},
        422: {"description": "Neither printer_configs nor model_targets given, a bad or duplicate target/printer, a gcode file on a printer that can't take raw gcode, or a customer order"},
    },
    dependencies=[Depends(require_scope("jobs:write"))],
)
async def create_job(
    body: JobCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue a new print job for a specific file plate. At least one printer config or make/model target
    (`model_targets`) is required. The job is added at the end of the queue and the engine is woken immediately."""
    # Validate file exists
    uploaded_file = await session.get(UploadedFile, body.uploaded_file_id)
    if uploaded_file is None:
        raise HTTPException(404, f"File {body.uploaded_file_id} not found")

    if not body.printer_configs and not body.model_targets:
        raise HTTPException(422, "printer_configs or model_targets must not be empty")
    _validate_targets(body.model_targets)
    await _require_known_models(session, body.model_targets)

    for cfg in body.printer_configs:
        printer = await session.get(Printer, cfg.printer_id)
        if printer is None:
            raise HTTPException(404, f"Printer {cfg.printer_id} not found")
    await _check_gcode_printers(session, uploaded_file, body.printer_configs, body.model_targets)
    await _check_machine_eligibility(session, uploaded_file, body.printer_configs, body.model_targets,
                                     body.confirm_unknown_eligibility)
    version = await _cached_version_of(session, uploaded_file)
    await _check_version_printers(session, version, body.printer_configs, body.model_targets)
    # A pre-sliced archive prints exactly the plate asked for (Bambu is told `Metadata/plate_N.gcode`): a plate the
    # file doesn't have would only fail at print time, and failed is terminal.
    plates = [p.get("plate_number") for p in (uploaded_file.plates or [])]
    if is_presliced_file(uploaded_file) and plates and body.plate_number not in plates:
        raise HTTPException(422, f"Plate {body.plate_number} is not in this file (it has plate(s) "
                                 f"{', '.join(str(n) for n in plates)})")

    if body.order_id is not None:
        order = await session.get(Order, body.order_id)
        if order is None:
            raise HTTPException(404, f"Order {body.order_id} not found")
        if order.order_type == "customer":
            raise HTTPException(422, "Customer work is recorded as a project — link jobs to internal orders only")
    project = await _linkable_project(session, body.project_id) if body.project_id is not None else None
    if body.save_slice and is_presliced_file(uploaded_file):
        raise HTTPException(422, "This file is already sliced; there is no new slice to save")

    now = datetime.now(timezone.utc).isoformat()
    pos = await _next_queue_position(session)

    job = Job(
        uploaded_file_id=body.uploaded_file_id,
        plate_number=body.plate_number,
        order_id=project.order_id if project is not None and project.order_id is not None else body.order_id,
        project_id=project.id if project is not None else None,
        # Slicing overrides mean nothing to pre-sliced gcode; they are dropped, not stored.
        overrides=None if is_presliced_file(uploaded_file) else _clean_overrides(body.overrides),
        queue_position=pos,
        status="queued",
        not_before=body.not_before,
        save_slice=body.save_slice,
        save_slice_name=_clean_save_name(body.save_slice_name) if body.save_slice else None,
        eligibility_confirmed=bool(body.confirm_unknown_eligibility and is_presliced_file(uploaded_file)),
        created_at=now,
        updated_at=now,
    )
    if version is not None:   # printing a cached version picked by hand: always honoured, stale or not (BIZ-193)
        job.sliced_version_id = version.id
        job.slice_cache_info = slice_cache.decision_info(
            "hit", reason="user_selected", cache_key=version.cache_key, version=version,
            cached_file_hash=uploaded_file.content_hash)
    session.add(job)
    await session.flush()
    if version is not None:
        slice_cache.log_event("hit_slice_skipped", job_id=job.id, reason="user_selected",
                              cache_key=version.cache_key, sliced_version_id=version.id,
                              cached_file_id=uploaded_file.id, cached_file_hash=uploaded_file.content_hash,
                              source_file_id=version.source_file_id)

    for cfg in body.printer_configs:
        config = JobPrinterConfig(
            job_id=job.id,
            printer_id=cfg.printer_id,
            print_profile=cfg.print_profile,
            filament_profile=cfg.filament_profile,
            **ask_columns(cfg),
            tool_index=cfg.tool_index,
            filament_map=cfg.filament_map,
        )
        session.add(config)

    _add_targets(session, job.id, body.model_targets)
    await session.flush()
    await model_targets.materialize_job(session, job.id)

    await session.commit()
    await session.refresh(job)

    queue_engine.wake()

    if is_presliced_file(uploaded_file):
        _apply_gcode_estimate(job, uploaded_file)
        await session.commit()
        return _to_dict(job)

    queue_cfg = await session.get(QueueConfig, 1)
    estimates_enabled = queue_cfg is not None and queue_cfg.estimates_enabled
    if estimates_enabled:
        job.estimate_token = (job.estimate_token or 0) + 1
        job.estimate_status = "pending"
        await session.commit()
        queue_engine.spawn_estimate(job.id)

    return _to_dict(job)


@router.post(
    "/check-overrides",
    summary="Check embedded settings vs presets",
    responses={
        404: {"description": "File or printer not found"},
    },
    dependencies=[Depends(require_scope("jobs:read"))],
)
async def check_overrides(
    body: OverrideCheckRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Flag settings baked into the uploaded 3MF that the chosen presets would
    change, so the New Job flow can warn before slicing replaces them."""
    uploaded_file = await session.get(UploadedFile, body.uploaded_file_id)
    if uploaded_file is None:
        raise HTTPException(404, f"File {body.uploaded_file_id} not found")
    printer = await session.get(Printer, body.printer_id)
    if printer is None:
        raise HTTPException(404, f"Printer {body.printer_id} not found")

    empty = {"has_findings": False, "setting_changes": [], "slot_warning": None}
    # Bare/geometry-only uploads carry no settings to lose.
    if not await asyncio.to_thread(with_local_path, get_library_store(), uploaded_file.relative_path,
                                   source_has_project_settings, False):
        return {**empty, "has_embedded_settings": False}
    machine_name = await slicer_profiles.machine_profile_for(session, printer)
    if not machine_name:
        return {**empty, "has_embedded_settings": True}

    # Resolve profile names to UUIDs via the Orca sidecar, then fetch the merged
    # project config. If the sidecar is unavailable or any required UUID is missing,
    # skip the diff rather than blocking job creation.
    from ...services.providers.slicing import SlicingProviderError, get_slicing_provider

    slicing = get_slicing_provider()
    if slicing is None:
        return {**empty, "has_embedded_settings": True, "error": "Override check requires Laminus sidecar"}

    from ...services import catalog_service
    try:
        catalog = await catalog_service.get_cached_catalog()
    except Exception as e:
        return {**empty, "has_embedded_settings": True, "error": f"Catalog unavailable: {e}"}

    machine_uuid = catalog.ref_for("machine", machine_name)
    process_name = effective_process_profile(body, printer)     # "" = the printer's default (BIZ-358)
    process_uuid = catalog.ref_for("process", process_name)
    if not machine_uuid or not process_uuid:
        return {**empty, "has_embedded_settings": True,
                "error": f"Profile not found in sidecar: machine={machine_name!r} process={process_name!r}"}

    # Filament content doesn't affect the curated (process) diff. If the named
    # filament isn't in the catalog, pick the first compatible one as a stand-in.
    filament_uuid = catalog.ref_for("filament", body.filament_profile or "")
    if not filament_uuid:
        compat = [f.ref for f in slicing.compatible_presets(catalog, machine_name, "filament")]
        filament_uuid = compat[0] if compat else next((f.ref for f in catalog.filaments if f.name and f.ref), None)
    if not filament_uuid:
        return {**empty, "has_embedded_settings": True, "error": "No filament profiles found in sidecar catalog"}

    try:
        config = await asyncio.get_running_loop().run_in_executor(
            None, slicing.merged_config, machine_uuid, process_uuid, [filament_uuid]
        )
    except SlicingProviderError as e:
        return {**empty, "has_embedded_settings": True, "error": str(e)}

    slots = len(printer.loaded_filaments or []) or 1
    inspection = await asyncio.to_thread(
        with_local_path, get_library_store(), uploaded_file.relative_path,
        lambda path: get_format_provider().inspect_overrides(path, config, slots), None)
    if inspection is None:   # removed since the check above
        return {**empty, "has_embedded_settings": False}
    return _inspection_json(inspection)


async def _names_by_id(session: AsyncSession, model, ids, column=None) -> dict[int, str]:
    """`{id: name}` for the rows of `model` among `ids` (None/absent ids are skipped), in one query."""
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    name = model.name if column is None else column
    return {row_id: value for row_id, value in await session.execute(select(model.id, name).where(model.id.in_(wanted)))}


@router.get("", summary="List active jobs", dependencies=[Depends(require_scope("jobs:read"))])
async def list_jobs(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """All jobs ordered by queue position. Includes jobs in all statuses."""
    result = await session.execute(select(Job).order_by(Job.queue_position))
    jobs = result.scalars().all()

    job_ids = [j.id for j in jobs]
    targets_by_job = await model_targets.target_dicts_by_job(session, job_ids)
    # Two queries for the whole queue, not one per job plus one per printer config: this route is polled.
    configs_by_job: dict[int, list[JobPrinterConfig]] = {}
    for c in (await session.execute(
            select(JobPrinterConfig).where(JobPrinterConfig.job_id.in_(job_ids)).order_by(JobPrinterConfig.id))).scalars():
        configs_by_job.setdefault(c.job_id, []).append(c)
    printer_names = await _names_by_id(session, Printer, {c.printer_id for cs in configs_by_job.values() for c in cs})
    out = []
    for j in jobs:
        d = _to_dict(j)
        configs = configs_by_job.get(j.id, [])
        d["materials"] = type_labels(configs)
        d["eligible_printers"] = [{"id": c.printer_id, "name": printer_names[c.printer_id]}
                                  for c in configs if c.printer_id in printer_names]
        d["model_targets"] = targets_by_job[j.id]
        out.append(d)
    return out


@router.get("/history", summary="List job history", dependencies=[Depends(require_scope("jobs:read"))])
async def list_history(
    status: str = "complete,cancelled,failed",
    project_id: Optional[int] = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Completed/cancelled/failed jobs enriched with file name, printer name, and project name.
    `status` is a comma-separated list; `project_id` filters to a single project."""
    statuses = [s.strip() for s in status.split(",") if s.strip()]
    q = select(Job).where(Job.status.in_(statuses))
    if project_id is not None:
        q = q.where(Job.project_id == project_id)
    q = q.order_by(Job.updated_at.desc()).limit(limit)
    jobs = (await session.execute(q)).scalars().all()

    # One lookup per table for the whole page (was three queries per job).
    file_names = await _names_by_id(session, UploadedFile, {j.uploaded_file_id for j in jobs}, UploadedFile.original_filename)
    printer_names = await _names_by_id(session, Printer, {j.assigned_printer_id for j in jobs})
    project_names = await _names_by_id(session, Project, {j.project_id for j in jobs})
    out = []
    for j in jobs:
        d = _to_dict(j)
        d["file_name"] = file_names.get(j.uploaded_file_id)
        d["printer_name"] = printer_names.get(j.assigned_printer_id)
        d["project_name"] = project_names.get(j.project_id)
        out.append(d)
    return out


@router.get(
    "/{job_id}",
    summary="Get job",
    responses={
        404: {"description": "Job not found"},
    },
    dependencies=[Depends(require_scope("jobs:read"))],
)
async def get_job(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return _to_dict(await _get_or_404(job_id, session))


MAX_DETAIL_FIELDS = 50


@router.get(
    "/{job_id}/detail-fields",
    summary="Get generic job detail fields",
    responses={
        404: {"description": "Job not found"},
        422: {"description": "No field names, or more than 50"},
    },
    dependencies=[Depends(require_scope("jobs:read"))],
)
async def get_job_detail_fields(
    job_id: int,
    fields: str = Query(..., description="Comma-separated field names, e.g. `print_end_time,filament_usage`"),
    origin: Optional[str] = Query(None, description="Only the value written by this origin (a plugin id or `core`)"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The open-vocabulary details plugins wrote onto the job (debug/read-only). Each field answers `ok`, `unsupported`,
    `unavailable` or `unknown_field`; a bad name never fails the call. Distinct from `/{job_id}/details` (the job screen's shape)."""
    await _get_or_404(job_id, session)
    names = list(dict.fromkeys(f.strip() for f in fields.split(",") if f.strip()))
    if not names or len(names) > MAX_DETAIL_FIELDS:
        raise HTTPException(422, f"Give between 1 and {MAX_DETAIL_FIELDS} comma-separated field names")
    return {"job_id": job_id, "fields": await job_details.get_details_in(session, job_id, names, origin)}


@router.get(
    "/{job_id}/details",
    summary="Get job details",
    responses={
        404: {"description": "Job not found"},
    },
    dependencies=[Depends(require_scope("jobs:read"))],
)
async def get_job_details(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Full job detail: file name, plate stats, per-printer slicing configs, assigned printer."""
    job = await _get_or_404(job_id, session)

    # File + plate metadata
    file_info = None
    plate_info = None
    uploaded_file = await session.get(UploadedFile, job.uploaded_file_id)
    if uploaded_file:
        file_info = {"id": uploaded_file.id, "original_filename": uploaded_file.original_filename}
        plate = next(
            (p for p in (uploaded_file.plates or []) if p.get("plate_number") == job.plate_number),
            None,
        )
        if plate:
            plate_info = {
                "estimated_time": plate.get("estimated_time"),
                "filament_g": plate.get("filament_g"),
                "thumbnail_path": plate.get("thumbnail_path"),
            }

    # Needed filament grams for the spool preflight check (job-level, reused for
    # every printer_configs row): the background test-slice estimate if we have
    # one, else the 3MF-parsed plate estimate.
    needed_g = job.estimate_filament_grams
    if needed_g is None and plate_info:
        needed_g = plate_info.get("filament_g")

    # Per-printer slicing configs
    result = await session.execute(
        select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id)
    )
    configs = result.scalars().all()

    # Resolve each config's loaded-filament slot up front so we know whether a
    # inventory lookup is needed at all, and so we only ever fetch spools once.
    resolved: list[tuple[JobPrinterConfig, Printer | None, dict | None, filament_matching.Verdict]] = []
    spool_ids_needed: set[str] = set()
    conflicts_by_printer: dict[int, set[int]] = {}                  # one provider read per printer, not per config
    for cfg in configs:
        p = await session.get(Printer, cfg.printer_id)
        loaded = (p.loaded_filaments if p else None) or []
        if p and p.id not in conflicts_by_printer:
            conflicts_by_printer[p.id] = await filament_matching.conflicts_for(loaded)
        conflicts = conflicts_by_printer.get(p.id, set()) if p else set()
        fit = filament_matching.evaluate_for_printer(cfg, p, conflicts)          # why this printer does / does not match
        slot = _slot_for_config(cfg, loaded, conflicts) if p else None
        resolved.append((cfg, p, slot, fit))
        if inventory_refs.slot_spool_ref(slot) is not None:
            spool_ids_needed.add(inventory_refs.slot_spool_ref(slot))

    spools_by_id: dict[str, InvSpool] = {}
    if spool_ids_needed:
        spools_by_id = await inventory_read.spools_by_ref(f"job {job_id}")

    printer_configs = []
    for cfg, p, slot, fit in resolved:
        spool_warning = None
        if inventory_refs.slot_spool_ref(slot) is not None:
            spool = spools_by_id.get(inventory_refs.slot_spool_ref(slot))
            if spool is not None:
                spool_warning = check_spool_sufficiency(needed_g, spool)
        printer_configs.append({
            "printer_id": cfg.printer_id,
            "printer_name": p.name if p else f"Printer {cfg.printer_id}",
            "printer_type": p.printer_type if p else "unknown",
            "print_profile": cfg.print_profile,
            "filament_profile": cfg.filament_profile,
            **ask_out(cfg),
            "tool_index": cfg.tool_index,
            "filament_map": cfg.filament_map,
            "slice_failed": cfg.slice_failed,
            "slice_error": cfg.slice_error,
            # Does the printer's loaded filament satisfy the ask right now? `reasons` is one line per slot that does not.
            "filament_match": {"eligible": fit.eligible, "slots": [u.slot for u in fit.plan], "reasons": fit.reasons},
            "from_model_target": cfg.model_target_id is not None,
            "low_stock_warning": spool_warning,
        })

    # Assigned printer (if claimed)
    assigned_printer = None
    if job.assigned_printer_id:
        p = await session.get(Printer, job.assigned_printer_id)
        if p:
            assigned_printer = {"id": p.id, "name": p.name, "printer_type": p.printer_type}

    gcode_result = await session.execute(
        select(GcodeFile).where(GcodeFile.job_id == job_id).limit(1)
    )
    gcode_rec = gcode_result.scalar_one_or_none()

    return {
        **_to_dict(job),
        "block_reason": job.block_reason,
        "unmatched_filaments": job.unmatched_filaments or {},
        "file": file_info,
        "plate": plate_info,
        "printer_configs": printer_configs,
        "model_targets": await model_targets.target_dicts(session, job_id),
        "assigned_printer": assigned_printer,
        "filament_grams_live": gcode_rec.filament_grams if gcode_rec else None,
        "estimated_seconds_live": gcode_rec.estimated_seconds if gcode_rec else None,
    }


# ---- the rest of the jobs routes ---------------------------------------------------------------
# Imported last: each module below registers its routes on `router` (so they match after the ones above, in this order)
# and uses the names defined above. Their names are re-exported here for existing importers.
from .job_actions import (  # noqa: E402,F401,I001
    CompleteManuallyBody,  # noqa: F401
    JobConfigsUpdate,  # noqa: F401
    JobCostPatch,  # noqa: F401
    JobProjectLink,  # noqa: F401
    JobSaveSlicePatch,  # noqa: F401
    JobSchedulePatch,  # noqa: F401
    OutcomeBody,  # noqa: F401
    OutcomeFailureItem,  # noqa: F401
    ReorderBody,  # noqa: F401
    VerifySliceBody,  # noqa: F401
    _MANUAL_COMPLETE_TERMINAL_STATUSES,  # noqa: F401
    _REORDERABLE_STATUSES,  # noqa: F401
    _TERMINAL_STATUSES,  # noqa: F401
    _build_slice_request,  # noqa: F401
    _clean_save_name,  # noqa: F401
    _manual_complete_in_flight,  # noqa: F401
    _now,  # noqa: F401
    cancel_job,  # noqa: F401
    complete_job_manually,  # noqa: F401
    get_slice_failures,  # noqa: F401
    mark_job_outcome,  # noqa: F401
    reorder_job,  # noqa: F401
    set_job_cost,  # noqa: F401
    set_job_project,  # noqa: F401
    set_job_save_slice,  # noqa: F401
    set_job_schedule,  # noqa: F401
    unblock_job,  # noqa: F401
    update_job_configs,  # noqa: F401
    verify_slice,  # noqa: F401
)
