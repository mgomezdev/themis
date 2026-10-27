"""Slicing provider interface (sync).

Sync on purpose: slicing runs in the queue's ThreadPoolExecutor (and via `asyncio.to_thread` from
routes), polling for up to ~620s. Callers must never invoke these on the event loop directly. Refs
(`Preset.ref`, the `*_ref` params) are opaque provider ids — for Laminus they are preset UUIDs.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from .format_provider import FormatProvider

logger = logging.getLogger(__name__)


class SlicingProviderError(Exception):
    """Neutral failure from a slicing provider (unreachable, bad response, failed/timed-out slice)."""


class SlicingProviderNotReady(SlicingProviderError):
    """The provider answered but says it can't slice yet (e.g. still starting / building its catalog)."""


@dataclass(frozen=True)
class PackItem:
    """One loose model to pack: `quantity` copies of the STL at `path`. `ref` (required) is a caller-stable id, unique within one pack call, (Themis: the
    library file id); a provider uses it to tell same-named models apart."""
    path: Path
    quantity: int
    ref: str


@dataclass
class Preset:
    ref: str
    name: str
    compatible_printers: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class MachineCandidate:
    """A machine profile a provider suggests for a printer model (BIZ-294). `name` is what Themis stores (profile names are how presets
    are addressed everywhere in Themis); `ref` is the provider's stable id for it. `confidence` is 0..1."""
    name: str
    nozzle_diameter_mm: float | None = None
    flow: str = "standard"                 # standard | high_flow
    ref: str = ""
    confidence: float = 0.0
    match_reason: str = ""                 # exact_model | fuzzy_name | vendor_only


@dataclass
class NozzleVariantOption:
    """One nozzle variant a provider knows for a printer model; `is_default_guess` marks the one to default to (0.4 standard)."""
    name: str
    nozzle_diameter_mm: float | None = None
    flow: str = "standard"
    ref: str = ""
    is_default_guess: bool = False


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


def default_compatible_presets(catalog: Catalog, machine_preset: str, kind: str) -> list[Preset]:
    """The default compatibility rule: presets of `kind` whose `compatible_printers` names the machine. Pure function of
    a (cached) catalog, so it works with no live server; `SlicingProvider.compatible_presets` delegates to it."""
    return [p for p in catalog.presets_of(kind) if machine_preset in p.compatible_printers]


class SlicingProvider(ABC):
    ARRANGE: ClassVar[bool] = False
    PACK_MODELS: ClassVar[bool] = False
    PREPARED_PROJECT: ClassVar[bool] = False
    TOOL_MAPPING: ClassVar[bool] = False       # can route a 3MF's filaments to physical tool heads (apply_tool_mapping)
    SUGGEST_PROFILES: ClassVar[bool] = False   # can suggest machine profiles / nozzle variants for a printer model (BIZ-294)

    def suggest_machines(self, manufacturer: str, model: str, model_key: str | None = None) -> list[MachineCandidate]:
        """Machine profiles that probably belong to this printer model, best first. Optional: manual mapping entry never needs it."""
        raise SlicingProviderError(f"{type(self).__name__} cannot suggest machine profiles")

    def nozzle_variants(self, manufacturer: str, model: str, model_key: str | None = None) -> list[NozzleVariantOption]:
        """The nozzle variants (diameter / flow) this provider has a machine profile for, for this printer model."""
        raise SlicingProviderError(f"{type(self).__name__} cannot list nozzle variants")

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

    def compatible_presets(self, catalog: Catalog, machine_preset: str, kind: str) -> list[Preset]:
        """Presets of `kind` ("process" | "filament") usable with `machine_preset`. Default: the preset names the
        machine in its `compatible_printers`; a provider with richer rules overrides this."""
        return default_compatible_presets(catalog, machine_preset, kind)

    def arrange(self, project_path: Path, arrange: bool = True, orient: bool = True, timeout: float = 130.0) -> bytes:
        raise NotImplementedError("provider does not support ARRANGE")

    def pack_models(
        self, items: list[PackItem], *, machine_ref: str | None = None, process_ref: str | None = None,
        filament_refs: list[str] | None = None, bed: tuple[float, float, float] | None = None,
    ) -> bytes:
        """Arrange loose models (each `PackItem` = one STL x quantity) into a multi-plate project: by presets (machine/process/filaments refs)
        or, without presets, by explicit bed dimensions (x, y, z)."""
        raise NotImplementedError("provider does not support PACK_MODELS")


# The slicing provider is whichever plugin the host resolves for the `gcode.produce` capability (BIZ-290): core names none. The plugin
# id is the provider name in cache keys, so a second slicing plugin is selected on the capability and the keys follow.
_CAP = "gcode.produce"
# TODO(follow-up): the offline FormatProvider is not a capability yet, so it (and the cache-name fallback) still names the bundled plugin;
# a second slicing plugin needs FormatProvider resolved the same way.
_FORMAT_PLUGIN = "laminus"


def default_provider_id() -> str | None:
    """The plugin serving `gcode.produce` by default: the host's selection row (capability default) when it is enabled, else the
    one enabled provider, else None (none, or several with no choice made). Reads manifests + stored config only, so it also
    answers before the host has loaded (startup, tests)."""
    from ...plugins import providers_of
    from ...plugins.host import plugin_host

    chosen = plugin_host.selected(_CAP)
    if chosen and plugin_host.is_enabled(chosen):
        return chosen
    enabled = [m.id for m in providers_of(_CAP) if plugin_host.is_enabled(m.id)]
    return enabled[0] if len(enabled) == 1 else None


def effective_provider_id(preferred: str | None) -> str | None:
    """The plugin a printer slices with: its own preference, else the capability default."""
    return preferred or default_provider_id()


def enabled_provider_ids() -> list[str]:
    """Ids of the enabled `gcode.produce` plugins (what a printer may name as its preferred slicer)."""
    from ...plugins import providers_of
    from ...plugins.host import plugin_host

    return [m.id for m in providers_of(_CAP) if plugin_host.is_enabled(m.id)]


def slicing_provider_name() -> str:
    """The plugin id of the default slicing provider (the cache-key identity); the bundled one when none resolves."""
    return default_provider_id() or _FORMAT_PLUGIN


def provider_url(plugin_id: str | None) -> str | None:
    """A slicing plugin's service URL as the host resolves it (BIZ-289): saved setting > deprecated env override read by core >
    the plugin's defaults.toml. None when the plugin is unknown, disabled or unconfigured. Read at call time."""
    from .laminus.connection import clean_url
    from ...plugins.host import plugin_host

    if not plugin_id or not plugin_host.is_enabled(plugin_id):
        return None
    return clean_url(plugin_host.effective_settings(plugin_id).get("url"))


def configured_server_url() -> str | None:
    """The default slicing provider's URL (see `provider_url`)."""
    return provider_url(default_provider_id())


def _plugin_part(plugin_id: str, what: str, url: str | None = None):
    """The throw-away plugin instance's accessor `what` (`slicing_provider` | `gcode_producer`) called with the resolved URL (given,
    else `provider_url`), or None when the plugin is unconfigured, has invalid settings, or lacks the accessor."""
    from ...plugins import PluginError
    from ...plugins.host import plugin_host

    url = url or provider_url(plugin_id)
    if not url:
        return None
    # The plugin instance (built from the host-resolved settings: saved > deprecated env via core > defaults.toml) owns the
    # adapter and its connection options (timeouts); core only asks the host for it (BIZ-288).
    try:
        instance = plugin_host.build_candidate(plugin_id)
    except PluginError as e:                        # invalid saved settings: treat like unconfigured rather than crash the caller
        logger.warning("Laminus settings are invalid, slicing is unavailable until they are fixed: %s", e)
        return None
    make = getattr(instance, what, None)
    if make is None:                                # a plugin that declares gcode.produce but lacks the accessor: unavailable
        logger.warning("Plugin %s provides gcode.produce but exposes no %s(); slicing is unavailable", plugin_id, what)
        return None
    return make(url)


def slicing_provider_for(plugin_id: str | None) -> SlicingProvider | None:
    """The `SlicingProvider` of one specific `gcode.produce` plugin (BIZ-291: the queue slices with the provider it resolved for
    this printer, not the global default). None when that plugin is unavailable."""
    return _plugin_part(plugin_id, "slicing_provider") if plugin_id else None


def gcode_producer_for(plugin_id: str | None):
    """The neutral `GcodeProducer` of one specific plugin, or None while it is unconfigured/unavailable."""
    return _plugin_part(plugin_id, "gcode_producer") if plugin_id else None


def get_slicing_provider() -> SlicingProvider | None:
    """The default slicing provider, or None when none is configured."""
    pid = default_provider_id()
    return _plugin_part(pid, "slicing_provider", configured_server_url()) if pid else None


def get_format_provider() -> FormatProvider:
    """The offline `FormatProvider` for local file-format work (curated settings, estimates, override inspection).
    Unlike `get_slicing_provider()` it never returns None and never needs a configured server: it is a different
    object from the slicing provider (no URL, no network). `compatible_presets` needs a catalog, so it stays on
    `SlicingProvider` (use `get_slicing_provider()`)."""
    from . import laminus  # noqa: F401  (registers the adapter)

    # The format reader follows the default slicing plugin (it speaks that engine's vocabulary); a plugin with no reader of its own
    # falls back to the bundled one, so this never returns None.
    cls = _FORMAT_REGISTRY.get(default_provider_id() or "") or _FORMAT_REGISTRY[_FORMAT_PLUGIN]
    return cls()


# name -> format provider class (no-arg constructor). Registered next to the slicing adapter of the same name.
_FORMAT_REGISTRY: dict[str, type[FormatProvider]] = {}


def register_format_provider(name: str, cls: type[FormatProvider]) -> None:
    _FORMAT_REGISTRY[name] = cls
