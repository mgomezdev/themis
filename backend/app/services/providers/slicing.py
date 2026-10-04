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

from ...config import get_laminus_sidecar_url


class SlicingProviderError(Exception):
    """Neutral failure from a slicing provider (unreachable, bad response, failed/timed-out slice)."""


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

    def _by_kind(self, kind: str) -> list[Preset]:
        return {"machine": self.machines, "process": self.processes, "filament": self.filaments}[kind]

    def names(self, kind: str) -> set[str]:
        return {p.name for p in self._by_kind(kind) if p.name}

    def refs(self, kind: str) -> set[str]:
        return {p.ref for p in self._by_kind(kind) if p.ref}

    def ref_for(self, kind: str, name: str) -> str | None:
        """Name -> ref for a preset kind ("machine" | "process" | "filament"); None when unknown."""
        for p in self._by_kind(kind):
            if p.name == name and p.ref:
                return p.ref
        return None


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

    url = get_laminus_sidecar_url()
    if not url:
        return None
    return _REGISTRY["laminus"](url)


# name -> adapter class. Adding a provider = one adapter class + one entry here.
_REGISTRY: dict[str, type[SlicingProvider]] = {}


def register_slicing_provider(name: str, cls: type[SlicingProvider]) -> None:
    _REGISTRY[name] = cls
