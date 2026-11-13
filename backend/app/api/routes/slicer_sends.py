"""Atomic, duplicate-safe slicer imports and updates to unstarted work (BIZ-378)."""
from datetime import datetime, timezone
import asyncio
import contextlib
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID, uuid5

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...models import GcodeFile, Job, JobModelTarget, JobPrinterConfig, Project, ProjectGroup, ProjectItem, SlicerSend, QueueConfig, UploadedFile
from ...services import project_groups
from ...services.queue_engine import queue_engine
from ...services.library_scanner import is_presliced_file
from ...services.library_store import get_library_store
from ...services.artifact_store import ArtifactError
from .jobs import JobCreate, create_job
from .projects import ProjectItemCreate
from .project_children import add_item
from .project_groups import GroupPatch, patch_group
from . import projects as project_routes

router = APIRouter(prefix="/api/v1/slicer-sends", tags=["slicer-sends"],
                   dependencies=[Depends(require_scope("jobs:read"))])


class SlicerSendInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    send_id: UUID
    source_app: Literal["orcaslicer"] = "orcaslicer"
    update: bool = False
    kind: Literal["job","project"]
    job: JobCreate | None = None
    project_id: int | None = None
    project_name: str | None = Field(default=None,min_length=1,max_length=255)
    group: GroupPatch | None = None
    items: list[ProjectItemCreate] = Field(default_factory=list,max_length=500)

    @model_validator(mode="after")
    def valid_destination(self):
        if self.kind == "job":
            if not self.job or self.items or self.group or self.project_id or self.project_name:
                raise ValueError("A job send requires job settings only")
        elif self.job or not self.items or not self.group or bool(self.project_id) == bool(self.project_name):
            raise ValueError("A project send requires items, a group, and exactly one destination")
        return self


class AtomicSession:
    """Existing creation helpers flush normally; the enclosing send owns the single commit."""
    def __init__(self, session):
        self.session = session

    def __getattr__(self, name):
        return getattr(self.session,name)

    async def commit(self):
        await self.session.flush()


async def _can_update(session, result):
    if result.get("job_id"):
        job = await session.get(Job,result["job_id"])
        return job is not None and job.status in ("queued","blocked") and job.printed_on_printer_id is None
    item_ids = set(result.get("item_ids",[]))
    if not item_ids:
        return False
    items = list((await session.execute(select(ProjectItem).where(ProjectItem.id.in_(item_ids)))).scalars())
    if len(items) != len(item_ids) or any(i.quantity_completed or i.quantity_failed or i.quantity_held for i in items):
        return False
    group = await session.get(ProjectGroup,result["group_id"])
    if group is None or group.strategy == "pool" and group.queue_position is not None:
        return False
    for job in await _item_jobs(session,result):
        quantities = {int(k) for k in json.loads(job.project_item_quantities or "{}")}
        if job.status not in ("queued","blocked") or job.printed_on_printer_id is not None or not quantities <= item_ids:
            return False
    return True


async def _item_jobs(session,result):
    ids = set(result.get("item_ids",[]))
    jobs = (await session.execute(select(Job).where(Job.project_id == result["project_id"],Job.status != "cancelled"))).scalars()
    return [job for job in jobs if ids & {int(k) for k in json.loads(job.project_item_quantities or "{}")}]


async def _reply(session, record):
    result = dict(record.result or {})
    status = None
    if result.get("job_id"):
        job = await session.get(Job,result["job_id"])
        status = job.status if job else "deleted"
    return {"send_id":record.send_id,"source_app":record.source_app,"created":result,
            "can_update":await _can_update(session,result),"status":status}


@router.get("/{send_id}",summary="Look up a slicer send and whether it can still be updated")
async def get_send(send_id: UUID, request: Request, session: AsyncSession = Depends(get_session)):
    # Require authentication before revealing whether a UUID exists.
    await require_scope("jobs:read")(request,session)
    record = await session.get(SlicerSend,str(send_id))
    if record is None:
        raise HTTPException(404,"Send not found")
    if record.result and record.result.get("project_id"):
        await require_scope("projects:read")(request,session)
    return await _reply(session,record)


async def _replace_job(session, old, fresh):
    old_id, new_id = old.id, fresh.id
    await session.execute(delete(JobPrinterConfig).where(JobPrinterConfig.job_id == old_id))
    await session.execute(delete(JobModelTarget).where(JobModelTarget.job_id == old_id))
    await session.execute(delete(GcodeFile).where(GcodeFile.job_id == old_id))
    for model in (JobModelTarget,JobPrinterConfig):
        for row in (await session.execute(select(model).where(model.job_id == new_id))).scalars():
            row.job_id = old_id
    fields = ("uploaded_file_id","plate_number","order_id","project_id","overrides","min_bed_mm","printer_preference",
              "save_slice","save_slice_name","eligibility_confirmed","not_before","sliced_version_id","slice_cache_info",
              "estimate_seconds","estimate_filament_grams","estimate_filament_breakdown","estimate_preset_label","filament_cost")
    for field in fields:
        setattr(old,field,getattr(fresh,field))
    old.status,old.assigned_printer_id,old.block_reason,old.unmatched_filaments = "queued",None,None,None
    old.estimate_status = None
    old.estimate_token = (old.estimate_token or 0) + 1
    old.updated_at = datetime.now(timezone.utc).isoformat()
    await session.flush()
    await session.delete(fresh)
    await session.flush()
    return old_id


@router.post("",status_code=201,summary="Create, retry or update an atomic slicer send",
             responses={409:{"description":"Different payload or work already generated/started"}})
async def send(body: SlicerSendInput, request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    cleanup = []
    try:
        return await _send(body,request,response,session,cleanup)
    except Exception:
        await session.rollback()
        store = get_library_store(prune_empty=True)
        for key in reversed(cleanup):
            with contextlib.suppress(ArtifactError,OSError):
                await asyncio.to_thread(store.delete,key,missing_ok=True)
        raise


async def _send(body,request,response,session,cleanup):
    await require_scope("jobs:write" if body.kind == "job" else "projects:write")(request,session)
    normalized = body.model_dump(mode="json",exclude={"update"})
    fingerprint = sha256(json.dumps(normalized,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    sid = str(body.send_id)
    # SQLite's writer lock serializes this identity with concurrent sends and claims.
    # The ledger insert and every created/updated row commit together, including errors.
    await session.execute(insert(SlicerSend).values(send_id=sid,source_app=body.source_app,request_hash=fingerprint,
        created_at=datetime.now(timezone.utc).isoformat()).on_conflict_do_nothing(index_elements=["send_id"]))
    record = await session.get(SlicerSend,sid)
    old_result = record.result
    if old_result:
        if record.request_hash == fingerprint:
            response.status_code = 200
            return await _reply(session,record)
        if not body.update or not await _can_update(session,old_result):
            raise HTTPException(409,"This send differs or its work was generated/started. Send as new instead.")
        if bool(old_result.get("job_id")) != (body.kind == "job"):
            raise HTTPException(409,"A send cannot change between job and project. Send as new instead.")
        response.status_code = 200
    session.info["slicer_transaction"] = True
    atomic = AtomicSession(session)
    if body.kind == "job":
        created = await create_job(body.job,atomic)
        fresh = await session.get(Job,created["id"])
        jid = await _replace_job(session,await session.get(Job,old_result["job_id"]),fresh) if old_result else fresh.id
        job = await session.get(Job,jid)
        job.source_app,job.send_id = body.source_app,sid
        result = {"job_id":jid,"file_id":body.job.uploaded_file_id,"item_ids":[]}
    else:
        replaced_jobs = []
        if old_result:
            project_id,group_id = old_result["project_id"],old_result["group_id"]
            if body.project_id and body.project_id != project_id:
                raise HTTPException(409,"An update cannot move work to another project")
            replaced_jobs = await _item_jobs(session,old_result)
            for job in replaced_jobs:
                job.status = "cancelled"
                job.updated_at = datetime.now(timezone.utc).isoformat()
            await session.execute(delete(ProjectItem).where(ProjectItem.id.in_(old_result["item_ids"])))
        else:
            project_id = body.project_id
            if project_id is None:
                now = datetime.now(timezone.utc).isoformat()
                project = Project(name=body.project_name,stage="planning",source_app=body.source_app,external_ref=sid,
                                  created_at=now,updated_at=now)
                session.add(project)
                await session.flush()
                project_id = project.id
            if await session.get(Project,project_id) is None:
                raise HTTPException(404,"Project not found")
            await project_groups.ensure_default_group(session,project_id)
            group = ProjectGroup(project_id=project_id,name=body.group.name or "Orca import",
                                 sort_order=await project_groups.next_sort_order(session,project_id))
            session.add(group)
            await session.flush()
            group_id = group.id
        # Full replacement prevents an update from retaining previously used settings.
        group_body = body.group.model_copy(update={"overrides":body.group.overrides,
            "printer_preference":body.group.printer_preference,"targets":body.group.targets or []})
        await patch_group(project_id,group_id,group_body,atomic)
        ids = []
        for index,item in enumerate(body.items):
            item_body = item.model_copy(update={"group_id":group_id})
            result = await add_item(project_id,item_body,atomic)
            row = await session.get(ProjectItem,result["id"])
            row.source_app,row.send_id = body.source_app,str(uuid5(body.send_id,str(index)))
            ids.append(row.id)
        result = {"project_id":project_id,"group_id":group_id,"item_ids":ids}
        if replaced_jobs:
            from .project_generate import _plan_group_packs, _queue_packs
            group = await session.get(ProjectGroup,group_id)
            project = await session.get(Project,project_id)
            imported = list((await session.execute(select(ProjectItem).where(ProjectItem.id.in_(ids)))).scalars())
            targets = [(t.target_key,t.process_profile) for t in await project_groups.targets_of(session,group_id)]
            packs,_,_ = await _plan_group_packs(atomic,group,imported,targets,get_library_store())
            constructor = project_routes.get_project_constructor()
            if any(not pack.get("fixed") for pack in packs) and constructor is None:
                raise HTTPException(422,"Configure the project builder before updating generated geometry")
            jobs,_ = await _queue_packs(atomic,project,packs,constructor,allow_cached=False,save_slice=False,artifact_cleanup=cleanup)
            result["job_ids"] = [j["id"] for j in jobs]
    record.request_hash,record.result = fingerprint,result
    estimate_ids = []
    config = await session.get(QueueConfig,1)
    candidates = [job] if body.kind == "job" else [await session.get(Job,jid) for jid in result.get("job_ids",[])]
    for candidate in candidates:
        file = await session.get(UploadedFile,candidate.uploaded_file_id)
        if config is not None and config.estimates_enabled and not is_presliced_file(file):
            candidate.estimate_token = (candidate.estimate_token or 0)+1
            candidate.estimate_status = "pending"
            estimate_ids.append(candidate.id)
    await session.commit()
    session.info.pop("slicer_transaction",None)
    queue_engine.wake()
    for jid in estimate_ids:
        queue_engine.spawn_estimate(jid)
    return await _reply(session,record)
