"""Slicing provider interface (sync).

Sync on purpose: slicing runs in the queue's ThreadPoolExecutor (and via `asyncio.to_thread` from
routes), polling for up to ~620s. Callers must never invoke these on the event loop directly. Refs
(`Preset.ref`, the `*_ref` params) are opaque provider ids — for Laminus they are preset UUIDs.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from ... import config


class SlicingProviderError(Exception):
    """Neutral failure from a slicing provider (unreachable, bad response, failed/timed-out slice)."""


class SlicingProviderNotReady(SlicingProviderError):
    """The provider answered but says it can't slice yet (e.g. still starting / building its catalog)."""


@dataclass
class Preset:
    ref: str
    name: str
    compatible_printers: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class Catalog:
    machines: list[Preset] = field(default_factory=list)
    processes: list[Preset] = field(default_factory=list)
    filaments: list[Preset] = field(default_factory=list)
    # Legacy vendor payload ({"machine": [...], "process": [...], "filament": [...]}), kept so routes can
    # re-serialize the exact shape they always returned.
    raw: dict = field(default_factory=dict)

    def presets_of(self, kind: str) -> list[Preset]:
        return {"machine": self.machines, "process": self.processes, "filament": self.filaments}[kind]

    def names(self, kind: str) -> set[str]:
        return {p.name for p in self.presets_of(kind) if p.name}

    def refs(self, kind: str) -> set[str]:
        return {p.ref for p in self.presets_of(kind) if p.ref}

    def ref_for(self, kind: str, name: str) -> str | None:
        """Name -> ref for a preset kind ("machine" | "process" | "filament"); None when unknown."""
        found = None
        for p in self.presets_of(kind):
            if p.name == name and p.ref:
                found = p.ref          # last wins on duplicate names, as the old {name: uuid} maps did
        return found


@dataclass
class SliceSpec:
    """One slice, with presets already resolved to refs."""
    source_file: Path
    plate: int
    machine_ref: str
    process_ref: str
    filament_refs: list[str]
    export_3mf: bool = False
    extra_config: dict = field(default_factory=dict)
    prepared: bool = False   # source_file is a ready-to-slice project (needs PREPARED_PROJECT)


class SlicingProvider(ABC):
    ARRANGE: ClassVar[bool] = False
    PACK_MODELS: ClassVar[bool] = False
    PREPARED_PROJECT: ClassVar[bool] = False
    TOOL_MAPPING: ClassVar[bool] = False       # can route a 3MF's filaments to physical tool heads (apply_tool_mapping)

    def apply_tool_mapping(self, source_3mf: Path, *, tool_index: int | None = None,
                           filament_map: list[dict] | None = None) -> None:
        """Rewrite the prepared 3MF at `source_3mf` in place so its filament(s) print on the chosen physical tool(s) of a
        tool-changer printer. `tool_index` (0-based) puts every object on one tool; `filament_map` is
        `[{model_filament (1-based), tool_index (0-based)}]`. They are mutually exclusive; neither is a no-op."""
        raise SlicingProviderError(f"{type(self).__name__} does not support tool mapping")

    @property
    @abstractmethod
    def identity(self) -> str:
        """Stable id of this provider instance (e.g. the base URL); keys per-provider memoization."""

    @abstractmethod
    def health(self, timeout: float | None = None) -> dict: ...

    @abstractmethod
    def get_catalog(self) -> Catalog: ...

    @abstractmethod
    def merged_config(
        self, machine_ref: str, process_ref: str, filament_refs: list[str], timeout: float | None = None,
    ) -> dict: ...

    @abstractmethod
    def catalog_health(self, timeout: float = 5.0) -> dict:
        """Catalog readiness: {catalog_loaded, catalog_building, catalog_profile_count?}. A provider that is
        still building reports catalog_building=True instead of raising; raises SlicingProviderError when
        unreachable/unhealthy."""

    @abstractmethod
    def request_catalog_rebuild(self, timeout: float = 10.0) -> None:
        """Ask the provider to rebuild its catalog from source (returns immediately; poll `catalog_health`)."""

    @abstractmethod
    def slice(self, spec: SliceSpec, output_dir: Path) -> str:
        """Slice and write the artifact into `output_dir`; returns its path."""

    # ── slicer-specific file-format knowledge ─────────────────────────────────────────────────────────────────
    # Pure local work on files Themis already holds — no calls to the slicing server, so they work (via
    # `get_format_provider()`) even when no server is configured, e.g. when scanning a library of sliced gcode.

    @abstractmethod
    def parse_estimates(
        self, artifact_path: str, plate_number: int | None = None,
    ) -> tuple[float | None, int | None, list[float] | None]:
        """(total_grams, seconds, per_extruder_grams) from a sliced artifact (gcode or sliced archive); each
        None independently when it can't be read. `plate_number` picks the plate inside a multi-plate archive."""

    @abstractmethod
    def inspect_overrides(self, source_project: str, merged_config: dict, slots: int) -> dict:
        """Compare a project file's embedded settings with `merged_config` (what the chosen presets would
        produce); returns {has_findings, setting_changes, slot_warning}."""

    @abstractmethod
    def curated_override_keys(self) -> tuple[str, ...]:
        """The high-impact setting keys job-level overrides / embedded-settings display may touch."""

    def compatible_presets(self, catalog: Catalog, machine_preset: str, kind: str) -> list[Preset]:
        """Presets of `kind` ("process" | "filament") usable with `machine_preset`. Default: the preset names the
        machine in its `compatible_printers`; a provider with richer rules overrides this."""
        return [p for p in catalog.presets_of(kind) if machine_preset in p.compatible_printers]

    def arrange(self, project_path: Path, arrange: bool = True, orient: bool = True, timeout: float = 130.0) -> bytes:
        raise NotImplementedError("provider does not support ARRANGE")

    def pack_models(
        self, paths: list[Path], *, machine_ref: str | None = None, process_ref: str | None = None,
        filament_refs: list[str] | None = None, bed: tuple[float, float, float] | None = None,
    ) -> bytes:
        """Arrange loose models into a multi-plate project: by presets (machine/process/filaments refs)
        or, without presets, by explicit bed dimensions (x, y, z)."""
        raise NotImplementedError("provider does not support PACK_MODELS")


def get_slicing_provider() -> SlicingProvider | None:
    """The configured slicing provider, or None when none is configured."""
    from . import laminus  # noqa: F401  (registers the adapter)

    url = config.get_laminus_sidecar_url()
    if not url:
        return None
    return _REGISTRY["laminus"](url)


def get_format_provider() -> SlicingProvider:
    """The slicing provider to use for local file-format work (estimates, override inspection). Unlike
    `get_slicing_provider()` it never returns None: format methods don't need a configured server."""
    from . import laminus  # noqa: F401  (registers the adapter)

    return _REGISTRY["laminus"](config.get_laminus_sidecar_url() or "")


# name -> adapter class. Adding a provider = one adapter class + one entry here.
_REGISTRY: dict[str, type[SlicingProvider]] = {}


def register_slicing_provider(name: str, cls: type[SlicingProvider]) -> None:
    _REGISTRY[name] = cls
