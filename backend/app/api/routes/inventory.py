"""Provider-neutral inventory API (BIZ-202 §3.9). Talks only to the active `filament_inventory` provider through the
plugin host and the neutral DTOs; a route that needs a capability the provider lacks answers 409
`capability_unavailable`. `raw` provider payloads are never serialised here."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_scope
from ...database import get_session
from ...plugins.capabilities.filament_inventory import (
    LABEL_SCAN, MANAGE_MATERIALS, MANAGE_SPOOLS, PROFILE_LINKS_WRITE, REMOTE, TRACKS_WEIGHT, WRITE_WEIGHT,
    InventoryProviderError, InvMaterial, InvSpool, MaterialDraft, NotSupported, SpoolDraft,
)
from ...models import InventoryPendingWrite, Job
from ...services.inventory import (
    cache as inventory_cache, config as inventory_config, deduction as inventory_deduction, outbox as inventory_outbox,
    provider as inventory_provider, read as inventory_read, sync as inventory_sync,
)

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])


def material_out(m: InvMaterial) -> dict:
    d = asdict(m)
    d.pop("raw", None)
    return d


def spool_out(s: InvSpool, url: str | None = None) -> dict:
    d = asdict(s)
    d.pop("raw", None)
    d["material"] = material_out(s.material) if s.material else None
    d["url"] = url
    return d


def _unavailable(result) -> HTTPException:
    return HTTPException(status_code=503, detail=inventory_provider.describe_failure(result)[1])


def _ref_order(item) -> tuple[int, str]:
    """Natural ref order (numeric refs 2 < 10): the API order is deterministic whatever order a provider lists in."""
    return (len(item.ref), item.ref)


def _envelope(pid: str, items: list, reading: "inventory_read.Reading") -> dict:
    """`stale`/`as_of`: a REMOTE provider that is unreachable is answered from the last-known cache (stale, as of when it was
    fetched); a live read is never stale."""
    return {"provider": pid, "stale": reading.stale, "as_of": reading.as_of, "items": items}


def _serve(reading: "inventory_read.Reading | None"):
    if reading is None:
        raise inventory_provider.CapabilityUnavailable()
    if not reading.ok:
        raise HTTPException(status_code=503, detail=reading.error)
    return reading


@router.get("/materials", summary="List materials of the active inventory provider (last-known data when it is unreachable)",
            responses={409: {"description": "No inventory provider is active"}, 503: {"description": "Provider unreachable and nothing cached"}},
            dependencies=[Depends(require_scope("inventory:read"))])
async def list_materials(include_archived: bool = False):
    pid = inventory_provider.require()
    reading = _serve(await inventory_read.materials_reading())
    return _envelope(pid, [material_out(m) for m in sorted(reading.items, key=_ref_order) if include_archived or not m.archived], reading)


@router.get("/spools", summary="List spools of the active inventory provider (last-known data when it is unreachable)",
            description="`remaining_g` is the *effective* weight: the newest queued (unsynced) write replaces what the provider "
                        "reported, and `unsynced` says so.",
            responses={409: {"description": "No inventory provider is active"}, 503: {"description": "Provider unreachable and nothing cached"}},
            dependencies=[Depends(require_scope("inventory:read"))])
async def list_spools(include_archived: bool = False):
    pid = inventory_provider.require()
    reading = _serve(await inventory_read.spools_reading())
    pending = await inventory_read.pending_targets(pid)
    provider = inventory_provider.active_provider()
    items = []
    for s in sorted(inventory_read.effective(reading.items, pending), key=_ref_order):
        if include_archived or not s.archived:
            items.append({**spool_out(s, provider.spool_url(s.ref)), "unsynced": s.ref in pending})
    return _envelope(pid, items, reading)


@router.post("/sync-now", summary="Refresh from the provider now",
             responses={409: {"description": "No provider, or it is not a remote one"}, 503: {"description": "Provider unreachable"}},
             dependencies=[Depends(require_scope("inventory:write"))])         # it records sync state and can fire alerts
async def sync_now(session: AsyncSession = Depends(get_session)):
    inventory_provider.require(REMOTE)
    try:
        return await inventory_sync.record_sync(session)
    except InventoryProviderError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/sync-status", summary="Provider health and sync status (never fails)",
            dependencies=[Depends(require_scope("inventory:read"))])
async def sync_status(session: AsyncSession = Depends(get_session)):
    pid = inventory_provider.provider_id()
    provider = inventory_provider.active_provider()
    caps = sorted(provider.capabilities) if provider else []
    cached = await inventory_cache.load(pid, inventory_cache.SPOOLS) if pid and inventory_cache.cacheable() else None
    return {"provider": pid, "capabilities": caps, **inventory_sync.status(pid),
            "pending_count": await inventory_sync.pending_count(session, pid), "cache_as_of": cached[1] if cached else None}


# --- library management (capability-gated: only providers that own their library) -----------------------------------------

_HEX = r"^#?[0-9A-Fa-f]{6}$"


class _Clean(BaseModel):
    """Every provider sees the same input: strings are stripped and an empty one means "not set" (None)."""

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


class MaterialIn(_Clean):
    name: str = Field(max_length=200)                        # blank -> None -> rejected: a material needs a name
    material: str | None = Field(default=None, max_length=50)
    color_hex: str | None = Field(default=None, pattern=_HEX)
    vendor: str | None = Field(default=None, max_length=200)
    density: float | None = Field(default=None, gt=0, le=100)
    diameter: float | None = Field(default=None, gt=0, le=100)


class MaterialPatch(_Clean):
    """Partial update: only the fields sent change; `null` clears an optional one."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    material: str | None = Field(default=None, max_length=50)
    color_hex: str | None = Field(default=None, pattern=_HEX)
    vendor: str | None = Field(default=None, max_length=200)
    density: float | None = Field(default=None, gt=0, le=100)
    diameter: float | None = Field(default=None, gt=0, le=100)


class SpoolIn(_Clean):
    material_ref: str = Field(max_length=64)
    label: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    initial_g: float | None = Field(default=None, ge=0, le=100_000)
    remaining_g: float | None = Field(default=None, ge=0, le=100_000)

    @model_validator(mode="after")
    def _remaining_within_initial(self):
        if self.initial_g is not None and self.remaining_g is not None and self.remaining_g > self.initial_g:
            raise ValueError("remaining_g cannot exceed initial_g")
        return self


class SpoolPatch(_Clean):
    label: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)


class ArchiveBody(BaseModel):
    archived: bool = True


class RemainingBody(BaseModel):
    remaining_g: float = Field(ge=0, le=100_000)


def _hex(value: str | None) -> str | None:
    return None if value is None else "#" + value.lstrip("#").upper()


def _patch(body: BaseModel) -> dict:
    changes = body.model_dump(exclude_unset=True)
    if "color_hex" in changes:
        changes["color_hex"] = _hex(changes["color_hex"])
    if not changes:
        raise HTTPException(status_code=422, detail="Nothing to change")
    if "name" in changes and changes["name"] is None:
        raise HTTPException(status_code=422, detail="A material needs a name")
    if "label" in changes and changes["label"] is None:
        raise HTTPException(status_code=422, detail="A spool needs a label")
    return changes


async def _write(method: str, *args):
    """A contained provider write; maps NotSupported -> 409 and the provider's own status (404/422) through."""
    result = await inventory_provider.call(method, *args)
    if result.ok:
        return result.value
    exc = result.exception
    if isinstance(exc, NotSupported):
        raise inventory_provider.CapabilityUnavailable(exc.capability)
    if isinstance(exc, InventoryProviderError) and exc.status in _PASSTHROUGH:
        # 404 unknown ref / 422 invalid input / 409 conflict (body is `{"detail": ...}`, unlike capability_unavailable's
        # `{"error": ...}`); any other upstream status (401, 5xx...) is "provider unavailable".
        raise HTTPException(status_code=exc.status, detail=inventory_provider.describe_failure(result)[1])
    raise _unavailable(result)


_PASSTHROUGH = {404, 409, 422}
_LIBRARY_RESPONSES = {409: {"description": "The provider does not manage its own library"},
                      404: {"description": "Unknown ref"}, 422: {"description": "Invalid input"}}


@router.post("/materials", status_code=201, summary="Create a material", responses=_LIBRARY_RESPONSES,
             dependencies=[Depends(require_scope("inventory:write"))])
async def create_material(body: MaterialIn):
    inventory_provider.require(MANAGE_MATERIALS)
    draft = MaterialDraft(**{**body.model_dump(), "color_hex": _hex(body.color_hex)})
    return material_out(await _write("create_material", draft))


@router.patch("/materials/{ref}", summary="Update a material", responses=_LIBRARY_RESPONSES,
              dependencies=[Depends(require_scope("inventory:write"))])
async def update_material(ref: str, body: MaterialPatch):
    inventory_provider.require(MANAGE_MATERIALS)
    return material_out(await _write("update_material", ref, _patch(body)))


@router.post("/materials/{ref}/archive", summary="Archive (or restore) a material", responses=_LIBRARY_RESPONSES,
             dependencies=[Depends(require_scope("inventory:write"))])
async def archive_material(ref: str, body: ArchiveBody | None = None):
    inventory_provider.require(MANAGE_MATERIALS)
    return material_out(await _write("archive_material", ref, (body or ArchiveBody()).archived))


@router.post("/spools", status_code=201, summary="Create a spool", responses=_LIBRARY_RESPONSES,
             dependencies=[Depends(require_scope("inventory:write"))])
async def create_spool(body: SpoolIn):
    inventory_provider.require(MANAGE_SPOOLS)
    return spool_out(await _write("create_spool", SpoolDraft(**body.model_dump())))


@router.patch("/spools/{ref}", summary="Update a spool's label / storage location", responses=_LIBRARY_RESPONSES,
              dependencies=[Depends(require_scope("inventory:write"))])
async def update_spool(ref: str, body: SpoolPatch):
    inventory_provider.require(MANAGE_SPOOLS)
    return spool_out(await _write("update_spool", ref, _patch(body)))


@router.post("/spools/{ref}/archive", summary="Archive (or restore) a spool", responses=_LIBRARY_RESPONSES,
             dependencies=[Depends(require_scope("inventory:write"))])
async def archive_spool(ref: str, body: ArchiveBody | None = None):
    inventory_provider.require(MANAGE_SPOOLS)
    return spool_out(await _write("archive_spool", ref, (body or ArchiveBody()).archived))


async def _weight_corrected(session: AsyncSession, ref: str) -> bool:
    """A user-set weight is authoritative: drop queued writes for the spool (they were computed from the old weight) and
    clear a tracking suspension. Returns whether tracking was suspended."""
    pid = inventory_provider.provider_id()
    await session.execute(update(InventoryPendingWrite).where(
        InventoryPendingWrite.provider == pid, InventoryPendingWrite.spool_ref == ref,
        InventoryPendingWrite.status.in_(("pending", "conflict"))).values(status="superseded"))
    restored = await inventory_deduction.restore(session, pid, ref)
    await session.commit()
    return restored


@router.put("/spools/{ref}/remaining", summary="Set a spool's remaining weight (absolute grams)", responses=_LIBRARY_RESPONSES,
            dependencies=[Depends(require_scope("inventory:write"))])
async def set_remaining(ref: str, body: RemainingBody, session: AsyncSession = Depends(get_session)):
    inventory_provider.require(WRITE_WEIGHT)
    async with inventory_outbox.flush_lock():            # no queued write may land after this one
        await _write("set_remaining", ref, body.remaining_g)
        await _weight_corrected(session, ref)
    # Write then read back (two calls, not atomic). If only the read-back fails the weight IS set: answer with what we wrote.
    read = await inventory_provider.call("get_spool", ref)
    spool = read.value if read.ok and read.value is not None else InvSpool(ref=ref, remaining_g=body.remaining_g, label=f"spool {ref}")
    return spool_out(spool)


class ResumeBody(BaseModel):
    remaining_g: float | None = Field(default=None, ge=0, le=100_000)


@router.post("/spools/{ref}/resume-tracking", summary="Resume usage tracking after correcting a spool's weight",
             description="With `remaining_g` the weight is written first (needs WRITE_WEIGHT); without it the spool's current "
                         "weight is confirmed as correct. Clears the suspension and queued writes for the spool.",
             responses={404: {"description": "Tracking is not suspended for this spool"}, **_LIBRARY_RESPONSES},
             dependencies=[Depends(require_scope("inventory:write"))])
async def resume_tracking(ref: str, body: ResumeBody | None = None, session: AsyncSession = Depends(get_session)):
    pid = inventory_provider.require()
    if not await inventory_deduction.is_suspended(session, pid, ref):
        raise HTTPException(status_code=404, detail="Tracking is not suspended for this spool")
    if body and body.remaining_g is not None:
        inventory_provider.require(WRITE_WEIGHT)
    async with inventory_outbox.flush_lock():
        if body and body.remaining_g is not None:
            await _write("set_remaining", ref, body.remaining_g)
        await _weight_corrected(session, ref)
    return {"provider": pid, "spool_ref": ref, "tracking": "ok"}


@router.get("/tracking", summary="Spools whose usage tracking is suspended",
            dependencies=[Depends(require_scope("inventory:read"))])
async def tracking(session: AsyncSession = Depends(get_session)):
    pid = inventory_provider.provider_id()
    rows = await inventory_deduction.suspended(session, pid) if pid else []
    return {"provider": pid, "items": [{"spool_ref": r.spool_ref, "reason": r.reason, "since": r.since, "job_id": r.job_id}
                                       for r in rows]}


def _write_out(r: InventoryPendingWrite) -> dict:
    return {"id": r.id, "provider": r.provider, "spool_ref": r.spool_ref, "target_g": r.target_g, "job_id": r.job_id,
            "printer_id": r.printer_id, "source": r.source, "created_at": r.created_at, "attempts": r.attempts,
            "last_attempt_at": r.last_attempt_at, "last_error": r.last_error, "status": r.status,
            "pre_weight_g": r.pre_weight_g, "conflict_current_g": r.conflict_current_g}


@router.get("/pending-writes", summary="Weight updates not yet applied to the provider (status `pending`, or `conflict` = held)",
            dependencies=[Depends(require_scope("inventory:read"))])
async def pending_writes(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(InventoryPendingWrite).where(InventoryPendingWrite.status.in_(("pending", "conflict")))
                                  .order_by(InventoryPendingWrite.id))).scalars().all()
    return {"provider": inventory_provider.provider_id(), "items": [_write_out(r) for r in rows]}


async def _pending_or_404(session: AsyncSession, write_id: int) -> InventoryPendingWrite:
    row = await session.get(InventoryPendingWrite, write_id)
    if row is None or row.status != "pending":
        raise HTTPException(status_code=404, detail="No such pending write")
    return row


@router.post("/pending-writes/flush", summary="Send pending weight updates now",
             dependencies=[Depends(require_scope("inventory:write"))])
async def flush_pending_writes(session: AsyncSession = Depends(get_session)):
    inventory_provider.require(WRITE_WEIGHT)
    applied = await inventory_outbox.flush(inventory_deduction.factory_for(session))
    return {"applied": applied}


@router.post("/pending-writes/{write_id}/discard", summary="Drop a pending weight update",
             responses={404: {"description": "No such pending write"}},
             dependencies=[Depends(require_scope("inventory:write"))])
async def discard_pending_write(write_id: int, session: AsyncSession = Depends(get_session)):
    async with inventory_outbox.flush_lock():            # not while a send of this row is in flight
        row = await _pending_or_404(session, write_id)
        row.status = "discarded"
        await session.commit()
    return _write_out(row)


class ResolveBody(BaseModel):
    target_g: float | None = Field(default=None, ge=0, le=100_000)


@router.post("/pending-writes/{write_id}/resolve", summary="Retry a pending weight update, optionally with a corrected target",
             responses={404: {"description": "No such pending write"}},
             dependencies=[Depends(require_scope("inventory:write"))])
async def resolve_pending_write(write_id: int, body: ResolveBody | None = None, session: AsyncSession = Depends(get_session)):
    inventory_provider.require(WRITE_WEIGHT)
    async with inventory_outbox.flush_lock():
        row = await _pending_or_404(session, write_id)
        if body and body.target_g is not None:
            row.target_g = body.target_g
        await session.commit()
    await inventory_outbox.flush(inventory_deduction.factory_for(session))
    await session.refresh(row)
    return _write_out(row)


class ConflictChoice(BaseModel):
    choice: Literal["themis", "provider", "subtract"] = Field(
        description="`themis`: write the value computed from the print-start weight. `provider`: keep the weight now in the "
                    "provider and drop this deduction. `subtract`: write the provider's current weight minus the grams the job used.")


@router.post("/pending-writes/{write_id}/resolve-conflict", summary="Resolve a held weight update (the provider's weight changed during the print)",
             responses={404: {"description": "No such held write"}, **_LIBRARY_RESPONSES},
             dependencies=[Depends(require_scope("inventory:write"))])
async def resolve_conflict(write_id: int, body: ConflictChoice, session: AsyncSession = Depends(get_session)):
    inventory_provider.require(WRITE_WEIGHT)
    async with inventory_outbox.flush_lock():
        row = await session.get(InventoryPendingWrite, write_id)
        if row is None or row.status != "conflict":
            raise HTTPException(status_code=404, detail="No such held write")
        if row.provider != inventory_provider.provider_id():       # its spool ref means something else to another provider
            raise HTTPException(status_code=409, detail="This held write belongs to another inventory provider; reselect it to resolve")
        if body.choice == "provider":
            row.status = "discarded"
            job = await session.get(Job, row.job_id) if row.job_id else None
            if job is not None:
                job.deduction_skipped, job.deduction_note = True, "Weight changed in the inventory during the print — its value was kept"
        else:
            if body.choice == "subtract":
                read = await inventory_provider.call("get_spool", row.spool_ref)
                if not read.ok or read.value is None or read.value.remaining_g is None:
                    raise HTTPException(status_code=502, detail="Could not read the spool's current weight from the inventory")
                spent = max(0.0, (row.pre_weight_g or 0.0) - row.target_g)
                row.target_g = max(0.0, float(read.value.remaining_g) - spent)
            row.status, row.pre_weight_g, row.conflict_current_g = "pending", None, None      # decided: no second check
        await session.commit()
    if row.status == "pending":
        await inventory_outbox.flush(inventory_deduction.factory_for(session))
    await session.refresh(row)
    return _write_out(row)


class LabelBody(BaseModel):
    text: str = Field(max_length=2000)


@router.post("/resolve-label", summary="Resolve scanned label text to a spool",
             responses={409: {"description": "The provider cannot scan labels"}},
             dependencies=[Depends(require_scope("inventory:read"))])
async def resolve_label(body: LabelBody):
    inventory_provider.require(LABEL_SCAN)
    provider = inventory_provider.active_provider()
    try:
        ref = provider.parse_label(body.text)
    except Exception:
        ref = None
    return {"spool_ref": ref}


class LinksBody(BaseModel):
    links: dict[str, list[str]]


@router.patch("/materials/{ref}/profile-links", summary="Set a material's Orca preset links",
              responses={409: {"description": "The provider cannot store profile links"}, 503: {"description": "Provider unreachable"}},
              dependencies=[Depends(require_scope("inventory:write"))])
async def set_profile_links(ref: str, body: LinksBody):
    inventory_provider.require(PROFILE_LINKS_WRITE)
    result = await inventory_provider.call("set_profile_links", ref, body.links)
    if not result.ok:
        exc = result.exception
        status = exc.status if isinstance(exc, InventoryProviderError) and exc.status else 503
        raise HTTPException(status_code=status, detail=inventory_provider.describe_failure(result)[1])
    return material_out(result.value)


class LowStock(BaseModel):
    """Grams below which a spool raises `spool.low`. `overrides` maps a material ref (of the active provider) to its own
    threshold and wins over `default_g`; with neither set nothing alerts."""
    default_g: float | None = Field(default=None, ge=0, le=100_000)
    overrides: dict[str, float] = Field(default_factory=dict)

    @field_validator("overrides")
    @classmethod
    def _valid(cls, v: dict[str, float]) -> dict[str, float]:
        for key, grams in v.items():
            if not key.strip() or ":" in key:
                raise ValueError(f"override key {key!r} must be a material ref")
            if not 0 <= grams <= 100_000:
                raise ValueError("override thresholds must be between 0 and 100000 grams")
        return v


class SettingsIn(BaseModel):
    deduct_on_complete: bool | None = None
    low_stock: LowStock | None = None


async def _settings_out(session: AsyncSession) -> dict:
    row = await inventory_config.get_config(session)
    pid = inventory_provider.provider_id()
    return {
        "provider": pid,
        "deduct_on_complete": bool(row.deduct_on_complete),
        "low_stock": {"default_g": row.low_stock_default_g,
                      "overrides": inventory_config.overrides_for(row, pid) if pid else {}},
    }


@router.get("/settings", summary="Inventory settings (deduct on completion, low-stock thresholds)",
            dependencies=[Depends(require_scope("inventory:read"))])
async def get_settings(session: AsyncSession = Depends(get_session)):
    out = await _settings_out(session)
    await session.commit()
    return out


@router.put("/settings", summary="Update inventory settings; omitted fields are unchanged",
            responses={409: {"description": "Low-stock thresholds need a provider that tracks weight"}},
            dependencies=[Depends(require_scope("inventory:write"))])
async def put_settings(body: SettingsIn, session: AsyncSession = Depends(get_session)):
    row = await inventory_config.get_config(session)
    if body.low_stock is not None:
        pid = inventory_provider.require(TRACKS_WEIGHT)
        row.low_stock_default_g = body.low_stock.default_g
        row.low_stock_overrides = inventory_config.merge_overrides(row, pid, body.low_stock.overrides)
    if body.deduct_on_complete is not None:
        row.deduct_on_complete = body.deduct_on_complete
    await session.commit()
    return await _settings_out(session)
