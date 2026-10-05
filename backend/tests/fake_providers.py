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
