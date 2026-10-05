"""`filament_inventory` provider that keeps the library in Themis' own database (plugin-owned `local_inv_*` tables).

Not REMOTE: it can't be unreachable, so it has no cache or outage handling. `set_remaining` is one transaction that updates
the spool and appends an audit row. Refs are the integer row ids as strings."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ...plugins.host import plugin_host
from ..kinds.filament_inventory import (
    LABEL_SCAN, MANAGE_MATERIALS, MANAGE_SPOOLS, MATERIAL_FIELDS, PROFILE_LINKS_READ, PROFILE_LINKS_WRITE, SPOOL_FIELDS,
    TRACKS_WEIGHT, WRITE_WEIGHT, FilamentInventoryProvider, InvMaterial, InvSpool, InventoryProviderError, MaterialDraft,
    SpoolDraft,
)
from .settings import LocalInventorySettings

_LABEL = re.compile(r"^(?:themis:)?s-([0-9]{1,18})$|^([0-9]{1,18})$", re.IGNORECASE)
_MAX_REF_DIGITS = 18                                   # comfortably inside SQLite's int64


def is_ref(ref) -> bool:
    """A well-formed local ref: ASCII digits only (no Unicode digits, no int64 overflow)."""
    return isinstance(ref, str) and ref.isascii() and ref.isdecimal() and len(ref) <= _MAX_REF_DIGITS


def _weight(value, what: str) -> float:
    """A finite, non-negative weight, or a 422."""
    if value is None or not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise _err(f"{what} must be a finite, non-negative number of grams", 422)
    return float(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _err(message: str, status: int) -> InventoryProviderError:
    return InventoryProviderError(message, code=str(status), status=status)


def _material(r) -> InvMaterial:
    try:
        links = json.loads(r["profile_links"] or "{}")
    except Exception:
        links = {}
    return InvMaterial(
        ref=str(r["id"]), name=r["name"], material=r["material"], color_hex=r["color_hex"], vendor=r["vendor"],
        density=r["density"], diameter=r["diameter"],
        profile_links={k: list(v) for k, v in links.items() if isinstance(v, list)} if isinstance(links, dict) else {},
        archived=bool(r["archived"]))


class LocalInventoryProvider(FilamentInventoryProvider):
    capabilities = frozenset({TRACKS_WEIGHT, WRITE_WEIGHT, PROFILE_LINKS_READ, PROFILE_LINKS_WRITE, LABEL_SCAN,
                              MANAGE_MATERIALS, MANAGE_SPOOLS})

    def __init__(self, settings: LocalInventorySettings) -> None:
        self._default_initial_g = settings.default_initial_g

    # -- plumbing -------------------------------------------------------------------------------------------------

    @staticmethod
    def _factory():
        factory = plugin_host.session_factory
        if factory is None:
            raise _err("Local inventory has no database", 500)
        return factory

    @staticmethod
    def _id(ref: str, what: str) -> int:
        if not is_ref(str(ref)):
            raise _err(f"{what} {ref} not found", 404)
        return int(ref)

    async def _materials(self, session, where: str = "", **params) -> list[InvMaterial]:
        rows = (await session.execute(text(f"SELECT * FROM local_inv_materials {where} ORDER BY id"), params)).mappings().all()
        return [_material(r) for r in rows]

    async def _spools(self, session, where: str = "", **params) -> list[InvSpool]:
        rows = (await session.execute(text(f"SELECT * FROM local_inv_spools {where} ORDER BY id"), params)).mappings().all()
        mats = {m.ref: m for m in await self._materials(session)}
        return [InvSpool(
            ref=str(r["id"]), material_ref=str(r["material_id"]), material=mats.get(str(r["material_id"])),
            remaining_g=r["remaining_g"], initial_g=r["initial_g"], location=r["location"], label=r["label"],
            archived=bool(r["archived"])) for r in rows]

    async def _one_material(self, session, ref: str) -> InvMaterial:
        found = await self._materials(session, "WHERE id = :i", i=self._id(ref, "Material"))
        if not found:
            raise _err(f"Material {ref} not found", 404)
        return found[0]

    async def _one_spool(self, session, ref: str) -> InvSpool:
        found = await self._spools(session, "WHERE id = :i", i=self._id(ref, "Spool"))
        if not found:
            raise _err(f"Spool {ref} not found", 404)
        return found[0]

    # -- required -------------------------------------------------------------------------------------------------

    async def test_connection(self) -> dict:
        async with self._factory()() as session:
            n = (await session.execute(text("SELECT COUNT(*) FROM local_inv_spools"))).scalar_one()
        return {"version": "local", "spool_count": n}

    async def list_materials(self) -> list[InvMaterial]:
        async with self._factory()() as session:
            return await self._materials(session)

    async def list_spools(self) -> list[InvSpool]:
        async with self._factory()() as session:
            return await self._spools(session)

    async def get_spool(self, spool_ref: str) -> InvSpool | None:
        if not is_ref(str(spool_ref)):
            return None
        async with self._factory()() as session:
            found = await self._spools(session, "WHERE id = :i", i=int(spool_ref))
            return found[0] if found else None

    # -- weight ---------------------------------------------------------------------------------------------------

    async def set_remaining(self, spool_ref: str, remaining_g: float) -> None:
        remaining_g = _weight(remaining_g, "remaining_g")
        sid = self._id(spool_ref, "Spool")
        async with self._factory()() as session:
            row = (await session.execute(text("SELECT remaining_g FROM local_inv_spools WHERE id = :i"), {"i": sid})).first()
            if row is None:
                raise _err(f"Spool {spool_ref} not found", 404)
            now = _now()
            await session.execute(text("UPDATE local_inv_spools SET remaining_g = :g, updated_at = :t WHERE id = :i"),
                                  {"g": float(remaining_g), "t": now, "i": sid})
            await session.execute(text("INSERT INTO local_inv_weight_log (spool_id, old_g, new_g, source, at) "
                                       "VALUES (:i, :o, :n, 'set_remaining', :t)"),
                                  {"i": sid, "o": row[0], "n": float(remaining_g), "t": now})
            await session.commit()                                  # the update and its audit row land together or not at all

    async def set_profile_links(self, material_ref: str, links: dict[str, list[str]]) -> InvMaterial:
        mid = self._id(material_ref, "Material")
        async with self._factory()() as session:
            await self._one_material(session, material_ref)
            await session.execute(text("UPDATE local_inv_materials SET profile_links = :l, updated_at = :t WHERE id = :i"),
                                  {"l": json.dumps({k: list(v) for k, v in links.items()}), "t": _now(), "i": mid})
            await session.commit()
            return await self._one_material(session, material_ref)

    # -- library --------------------------------------------------------------------------------------------------

    async def create_material(self, draft: MaterialDraft) -> InvMaterial:
        if not (draft.name or "").strip():
            raise _err("a material needs a name", 422)
        now = _now()
        async with self._factory()() as session:
            res = await session.execute(text(
                "INSERT INTO local_inv_materials (name, material, vendor, color_hex, density, diameter, created_at, updated_at) "
                "VALUES (:name, :material, :vendor, :color_hex, :density, :diameter, :t, :t)"),
                {"name": draft.name.strip(), "material": draft.material, "vendor": draft.vendor, "color_hex": draft.color_hex,
                 "density": draft.density, "diameter": draft.diameter, "t": now})
            await session.commit()
            return await self._one_material(session, str(res.lastrowid))

    async def update_material(self, ref: str, patch: dict) -> InvMaterial:
        bad = sorted(set(patch) - set(MATERIAL_FIELDS))
        if bad:
            raise _err(f"cannot change {bad}", 422)
        if "name" in patch and not (patch["name"] or "").strip():
            raise _err("a material needs a name", 422)
        mid = self._id(ref, "Material")
        async with self._factory()() as session:
            await self._one_material(session, ref)
            if patch:
                sets = ", ".join(f"{k} = :{k}" for k in patch)
                await session.execute(text(f"UPDATE local_inv_materials SET {sets}, updated_at = :t WHERE id = :i"),
                                      {**patch, "t": _now(), "i": mid})
                await session.commit()
            return await self._one_material(session, ref)

    async def archive_material(self, ref: str, archived: bool = True) -> InvMaterial:
        mid = self._id(ref, "Material")
        async with self._factory()() as session:
            await self._one_material(session, ref)
            await session.execute(text("UPDATE local_inv_materials SET archived = :a, updated_at = :t WHERE id = :i"),
                                  {"a": int(archived), "t": _now(), "i": mid})
            await session.commit()
            return await self._one_material(session, ref)

    async def create_spool(self, draft: SpoolDraft) -> InvSpool:
        async with self._factory()() as session:
            material = await self._one_material(session, draft.material_ref)
            if material.archived:
                raise _err("cannot add a spool to an archived material", 422)
            initial = None if draft.initial_g is None else _weight(draft.initial_g, "initial_g")
            remaining = None if draft.remaining_g is None else _weight(draft.remaining_g, "remaining_g")
            if initial is None and remaining is None:
                initial = remaining = self._default_initial_g
            elif remaining is None:
                remaining = initial
            if remaining is not None and initial is not None and remaining > initial:
                raise _err("remaining_g cannot exceed initial_g", 422)
            label = (draft.label or "").strip() or " ".join(p for p in (material.vendor, material.name) if p)
            now = _now()
            try:
                res = await session.execute(text(
                    "INSERT INTO local_inv_spools (material_id, label, location, initial_g, remaining_g, created_at, updated_at) "
                    "VALUES (:m, :label, :loc, :i, :r, :t, :t)"),
                    {"m": int(material.ref), "label": label or "spool", "loc": draft.location, "i": initial, "r": remaining, "t": now})
                await session.execute(text("INSERT INTO local_inv_weight_log (spool_id, old_g, new_g, source, at) "
                                           "VALUES (:i, NULL, :n, 'create', :t)"), {"i": res.lastrowid, "n": remaining, "t": now})
                await session.commit()
            except IntegrityError:
                raise _err(f"Material {draft.material_ref} not found", 404)
            return await self._one_spool(session, str(res.lastrowid))

    async def update_spool(self, ref: str, patch: dict) -> InvSpool:
        bad = sorted(set(patch) - set(SPOOL_FIELDS))
        if bad:
            raise _err(f"cannot change {bad}", 422)
        if "label" in patch and not (patch["label"] or "").strip():
            raise _err("a spool needs a label", 422)
        sid = self._id(ref, "Spool")
        async with self._factory()() as session:
            await self._one_spool(session, ref)
            if patch:
                sets = ", ".join(f"{k} = :{k}" for k in patch)
                await session.execute(text(f"UPDATE local_inv_spools SET {sets}, updated_at = :t WHERE id = :i"),
                                      {**patch, "t": _now(), "i": sid})
                await session.commit()
            return await self._one_spool(session, ref)

    async def archive_spool(self, ref: str, archived: bool = True) -> InvSpool:
        sid = self._id(ref, "Spool")
        async with self._factory()() as session:
            await self._one_spool(session, ref)
            await session.execute(text("UPDATE local_inv_spools SET archived = :a, updated_at = :t WHERE id = :i"),
                                  {"a": int(archived), "t": _now(), "i": sid})
            await session.commit()
            return await self._one_spool(session, ref)

    def parse_label(self, text: str) -> str | None:
        m = _LABEL.match((text or "").strip())
        return (m.group(1) or m.group(2)) if m else None
