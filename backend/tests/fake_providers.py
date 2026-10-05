"""In-memory provider fakes for tests that need to swap a provider without touching HTTP."""
from __future__ import annotations

from app.plugins.kinds.filament_inventory import (
    ALL_CAPABILITIES,
    FilamentInventoryProvider,
    InventoryProviderError,
    InvMaterial,
    InvSpool,
    NotSupported,
    PROFILE_LINKS_READ,
    PROFILE_LINKS_WRITE,
    TRACKS_WEIGHT,
    WRITE_WEIGHT,
)


class FakeInventoryProvider(FilamentInventoryProvider):
    """In-memory provider. `capabilities` is per-instance so tests can build providers that lack any capability."""

    def __init__(self, materials: list[InvMaterial] | None = None, spools: list[InvSpool] | None = None,
                 capabilities=frozenset({TRACKS_WEIGHT, WRITE_WEIGHT, PROFILE_LINKS_READ, PROFILE_LINKS_WRITE})) -> None:
        self.capabilities = frozenset(capabilities)
        self.materials = {m.ref: m for m in (materials or [])}
        self.spools = {s.ref: s for s in (spools or [])}
        self.writes: list[tuple[str, float]] = []
        self.calls: list[str] = []
        self.fail_with: Exception | None = None

    def _enter(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_with is not None:
            raise self.fail_with

    async def test_connection(self) -> dict:
        self._enter("test_connection")
        return {"version": "fake"}

    async def list_materials(self) -> list[InvMaterial]:
        self._enter("list_materials")
        return list(self.materials.values())

    async def list_spools(self) -> list[InvSpool]:
        self._enter("list_spools")
        return list(self.spools.values())

    async def get_spool(self, spool_ref: str) -> InvSpool | None:
        self._enter("get_spool")
        return self.spools.get(spool_ref)

    async def set_remaining(self, spool_ref: str, remaining_g: float) -> None:
        if WRITE_WEIGHT not in self.capabilities:
            raise NotSupported(WRITE_WEIGHT)
        self._enter("set_remaining")
        if spool_ref not in self.spools:
            raise InventoryProviderError(f"Spool {spool_ref} not found", code="404", status=404)
        self.writes.append((spool_ref, remaining_g))
        self.spools[spool_ref].remaining_g = remaining_g

    async def set_profile_links(self, material_ref: str, links: dict[str, list[str]]) -> InvMaterial:
        if PROFILE_LINKS_WRITE not in self.capabilities:
            raise NotSupported(PROFILE_LINKS_WRITE)
        self._enter("set_profile_links")
        try:
            material = self.materials[material_ref]
        except KeyError:
            raise InventoryProviderError(f"Material {material_ref} not found", code="404", status=404)
        material.profile_links = {k: list(v) for k, v in links.items()}
        return material


# ---- slicing ----

from pathlib import Path  # noqa: E402

from app.services.providers.slicing import (  # noqa: E402
    Catalog,
    Preset,
    SliceSpec,
    SlicingProvider,
    SlicingProviderError,
)


def make_catalog(machines=(("Printer A", "m-1"),), processes=(("0.20mm Standard", "p-1"),),
                 filaments=(("PLA @A", "f-1"), ("PETG @A", "f-2"))) -> Catalog:
    """Items are (name, ref) or (name, ref, compatible_printers); non-machine presets default to
    compatible with "Printer A"."""
    def presets(items, kind):
        out = []
        for item in items:
            name, ref = item[0], item[1]
            compat = list(item[2]) if len(item) > 2 else ([] if kind == "machine" else ["Printer A"])
            raw = {"name": name, "uuid": ref, **({"compatible_printers": compat} if compat else {})}
            out.append(Preset(ref=ref, name=name, compatible_printers=compat, raw=raw))
        return out

    cat = Catalog(machines=presets(machines, "machine"), processes=presets(processes, "process"),
                  filaments=presets(filaments, "filament"))
    cat.raw = {"machine": [p.raw for p in cat.machines], "process": [p.raw for p in cat.processes],
               "filament": [p.raw for p in cat.filaments]}
    return cat


class FakeSlicingProvider(SlicingProvider):
    ARRANGE = True
    PACK_MODELS = True
    PREPARED_PROJECT = True

    def __init__(self, catalog: Catalog | None = None, identity: str = "fake://slicer") -> None:
        self.catalog = catalog or make_catalog()
        self._identity = identity
        self.calls: list[tuple] = []
        self.fail_with: SlicingProviderError | None = None
        self.fail_on: dict[str, SlicingProviderError] = {}   # per-method failures
        self.artifact_name = "fake.gcode"
        self.artifact_bytes = b"; fake gcode\n"
        self.merged: dict = {}
        self.health_body: dict = {"status": "ok"}
        self.estimates: tuple = (12.5, 3600, [12.5])     # canned parse_estimates() -> (grams, seconds, per-extruder)
        self.override_findings: dict = {"has_findings": False, "setting_changes": [], "slot_warning": None}
        self.override_keys: tuple = ("layer_height", "sparse_infill_density")
        self.health_script: list = []
        self.default_health: dict = {"catalog_loaded": True, "catalog_building": False, "catalog_profile_count": 3}
        self.rebuild_error: SlicingProviderError | None = None

    @property
    def identity(self) -> str:
        return self._identity

    def _enter(self, name: str, *args) -> None:
        self.calls.append((name, *args))
        if name in self.fail_on:
            raise self.fail_on[name]
        if self.fail_with is not None:
            raise self.fail_with

    def health(self, timeout: float | None = None) -> dict:
        self._enter("health", timeout)
        return self.health_body

    def get_catalog(self) -> Catalog:
        self._enter("get_catalog")
        return self.catalog

    def parse_estimates(self, artifact_path, plate_number=None):
        self._enter("parse_estimates", artifact_path, plate_number)
        return self.estimates

    def inspect_overrides(self, source_project, merged_config, slots) -> dict:
        self._enter("inspect_overrides", source_project, slots)
        return self.override_findings

    def curated_override_keys(self) -> tuple[str, ...]:
        return self.override_keys

    def catalog_health(self, timeout: float = 5.0) -> dict:
        """Scripted: pops from `health_script` (a dict is returned, an Exception raised), else `default_health`."""
        self._enter("catalog_health")
        if self.health_script:
            nxt = self.health_script.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        return self.default_health

    def request_catalog_rebuild(self, timeout: float = 10.0) -> None:
        self._enter("request_catalog_rebuild")
        if self.rebuild_error is not None:
            raise self.rebuild_error

    def merged_config(self, machine_ref, process_ref, filament_refs, timeout=None) -> dict:
        self._enter("merged_config", machine_ref, process_ref, list(filament_refs))
        return self.merged

    def slice(self, spec: SliceSpec, output_dir: Path) -> str:
        self._enter("slice", spec)
        dest = Path(output_dir) / self.artifact_name
        dest.write_bytes(self.artifact_bytes)
        return str(dest)

    def arrange(self, project_path, arrange=True, orient=True, timeout=130.0) -> bytes:
        self._enter("arrange", project_path)
        return b"ARRANGED"

    def pack_models(self, paths, *, machine_ref=None, process_ref=None, filament_refs=None, bed=None) -> bytes:
        self._enter("pack_models", list(paths), machine_ref, bed)
        return b"PACKED"


def fake_packer(packed: bytes) -> FakeSlicingProvider:
    """A fake slicing provider whose `pack_models` is a MagicMock returning `packed` (for call assertions)."""
    from unittest.mock import MagicMock

    fake = FakeSlicingProvider()
    fake.pack_models = MagicMock(return_value=packed)
    return fake


# ---- a provider that owns its library (what Local inventory is) ----

from app.plugins.kinds.filament_inventory import (  # noqa: E402
    LABEL_SCAN, MANAGE_MATERIALS, MANAGE_SPOOLS, MATERIAL_FIELDS, SPOOL_FIELDS, MaterialDraft, SpoolDraft,
)


class FakeLibraryProvider(FakeInventoryProvider):
    """In-memory provider with library management: create/update/archive materials and spools, weights, labels."""

    def __init__(self, **kw) -> None:
        kw.setdefault("capabilities", frozenset({TRACKS_WEIGHT, WRITE_WEIGHT, PROFILE_LINKS_READ, PROFILE_LINKS_WRITE,
                                                 LABEL_SCAN, MANAGE_MATERIALS, MANAGE_SPOOLS}))
        super().__init__(**kw)
        self._next = 100

    def _new_ref(self) -> str:
        self._next += 1
        return str(self._next)

    def _material(self, ref: str) -> InvMaterial:
        try:
            return self.materials[ref]
        except KeyError:
            raise InventoryProviderError(f"Material {ref} not found", code="404", status=404)

    def _spool(self, ref: str) -> InvSpool:
        try:
            return self.spools[ref]
        except KeyError:
            raise InventoryProviderError(f"Spool {ref} not found", code="404", status=404)

    @staticmethod
    def _check(patch: dict, allowed: tuple[str, ...]) -> None:
        bad = sorted(set(patch) - set(allowed))
        if bad:
            raise InventoryProviderError(f"cannot change {bad}", code="422", status=422)

    async def create_material(self, draft: MaterialDraft) -> InvMaterial:
        if MANAGE_MATERIALS not in self.capabilities:
            raise NotSupported(MANAGE_MATERIALS)
        self._enter("create_material")
        if not draft.name.strip():
            raise InventoryProviderError("a material needs a name", code="422", status=422)
        m = InvMaterial(ref=self._new_ref(), name=draft.name, material=draft.material, color_hex=draft.color_hex,
                        vendor=draft.vendor, density=draft.density, diameter=draft.diameter, profile_links={})
        self.materials[m.ref] = m
        return m

    async def update_material(self, ref: str, patch: dict) -> InvMaterial:
        if MANAGE_MATERIALS not in self.capabilities:
            raise NotSupported(MANAGE_MATERIALS)
        self._enter("update_material")
        self._check(patch, MATERIAL_FIELDS)
        if "name" in patch and not (patch["name"] or "").strip():
            raise InventoryProviderError("a material needs a name", code="422", status=422)
        m = self._material(ref)
        for k, v in patch.items():
            setattr(m, k, v)
        return m

    async def archive_material(self, ref: str, archived: bool = True) -> InvMaterial:
        if MANAGE_MATERIALS not in self.capabilities:
            raise NotSupported(MANAGE_MATERIALS)
        self._enter("archive_material")
        m = self._material(ref)
        m.archived = archived
        return m

    def _own(self, spool: InvSpool) -> InvSpool:
        m = self.materials.get(spool.material_ref or "")
        spool.material = m
        spool.label = spool.label or (" ".join(p for p in (m.vendor if m else None, m.name if m else None) if p) or f"spool {spool.ref}")
        return spool

    async def create_spool(self, draft: SpoolDraft) -> InvSpool:
        if MANAGE_SPOOLS not in self.capabilities:
            raise NotSupported(MANAGE_SPOOLS)
        self._enter("create_spool")
        if self._material(draft.material_ref).archived:
            raise InventoryProviderError("cannot add a spool to an archived material", code="422", status=422)
        remaining = draft.remaining_g if draft.remaining_g is not None else draft.initial_g
        if remaining is not None and draft.initial_g is not None and remaining > draft.initial_g:
            raise InventoryProviderError("remaining_g cannot exceed initial_g", code="422", status=422)
        s = self._own(InvSpool(ref=self._new_ref(), material_ref=draft.material_ref, remaining_g=remaining,
                               initial_g=draft.initial_g, location=draft.location, label=draft.label or ""))
        self.spools[s.ref] = s
        return s

    async def update_spool(self, ref: str, patch: dict) -> InvSpool:
        if MANAGE_SPOOLS not in self.capabilities:
            raise NotSupported(MANAGE_SPOOLS)
        self._enter("update_spool")
        self._check(patch, SPOOL_FIELDS)
        if "label" in patch and not (patch["label"] or "").strip():
            raise InventoryProviderError("a spool needs a label", code="422", status=422)
        s = self._spool(ref)
        for k, v in patch.items():
            setattr(s, k, v)
        return s

    async def archive_spool(self, ref: str, archived: bool = True) -> InvSpool:
        if MANAGE_SPOOLS not in self.capabilities:
            raise NotSupported(MANAGE_SPOOLS)
        self._enter("archive_spool")
        s = self._spool(ref)
        s.archived = archived
        return s

    async def get_spool(self, spool_ref: str) -> InvSpool | None:
        s = await super().get_spool(spool_ref)
        return self._own(s) if s is not None else None

    async def list_spools(self) -> list[InvSpool]:
        return [self._own(s) for s in await super().list_spools()]

    def parse_label(self, text: str) -> str | None:
        import re
        m = re.search(r"themis:s-(\d+)", text or "") or re.fullmatch(r"\s*(\d+)\s*", text or "")
        return m.group(1) if m else None
