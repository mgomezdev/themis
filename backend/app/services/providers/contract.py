"""Neutral Themis<->slicer contract (BIZ-248). `gcode.produce` selection is wired into the queue claim (`resolve_work` via
`services/slice_resolution.py`, BIZ-291); `3mf.construct` and `GcodeProducer.produce()` are not wired yet.

Spec: docs/architecture/laminus-contract.md. Two capabilities, both sync (same rule as `SlicingProvider`: call from a
worker thread, never the event loop):

- `3mf.construct` (exclusive, one active): arrange/combine 3MF/STL inputs into a 3MF project  -> `ProjectConstructor`
- `gcode.produce` (choose-one, many): turn a model input into G-code for a target printer     -> `GcodeProducer`

Pre-sliced G-code is pass-through and needs NO provider (`resolve_work`). Core imports these names; only adapter
packages implement them. Nothing here names a vendor.
"""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

CAP_3MF_CONSTRUCT = "3mf.construct"
CAP_GCODE_PRODUCE = "gcode.produce"

DEFAULT_HEALTH_TIMEOUT_S = 2.0       # queue preflight (unchanged from SlicingProvider)
DEFAULT_PRODUCE_TIMEOUT_S = 630.0    # one produce()/construct() call, incl. its internal polling (unchanged)


class InputKind(StrEnum):
    """What a provider may be handed. G-code kinds are listed for the resolver only; no provider accepts them."""
    MODEL_3MF = "model_3mf"      # unsliced project (Orca/Bambu 3MF layout)
    STL = "stl"
    GCODE = "gcode"              # pre-sliced: pass-through
    GCODE_3MF = "gcode_3mf"      # sliced archive: pass-through


class ArtifactKind(StrEnum):
    PROJECT_3MF = "project_3mf"  # construct output
    GCODE = "gcode"              # produce output
    GCODE_3MF = "gcode_3mf"      # produce output with embedded plate data/thumbnails


MODEL_INPUTS = frozenset({InputKind.MODEL_3MF, InputKind.STL})
PASS_THROUGH_INPUTS = frozenset({InputKind.GCODE, InputKind.GCODE_3MF})


# ── identity, health, errors ──────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProviderIdentity:
    """`provider_id` is the stable registry/plugin id (never a URL); `version` the adapter/contract build.
    `engine_id`/`engine_version` identify the slicing engine actually used (e.g. family + pinned release); None when the
    provider cannot say. `build` is the finer engine build hash. None of these are in the slice-cache key (it must be
    computable while the engine is down); they feed the staleness fingerprint instead (docs/architecture/laminus-contract.md §7)."""
    provider_id: str
    version: str
    engine_id: str | None = None
    engine_version: str | None = None
    build: str | None = None


class Readiness(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"        # answered, cannot work yet (starting, building its catalog)
    UNREACHABLE = "unreachable"    # no answer (network, not configured)


@dataclass(frozen=True)
class ProviderStatus:
    state: Readiness
    identity: ProviderIdentity | None = None
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.state is Readiness.READY


class ErrorCode(StrEnum):
    NOT_READY = "not_ready"
    UNREACHABLE = "unreachable"
    UNSUPPORTED_INPUT = "unsupported_input"      # kind this provider never accepts
    UNSUPPORTED_TARGET = "unsupported_target"    # cannot serve this printer model/bed
    INVALID_SETTINGS = "invalid_settings"        # unknown profile/key, value out of range
    FAILED = "failed"                            # engine ran and failed (slice error)
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ProviderError(Exception):
    """Neutral provider failure. Core branches on `code`, never on message text or a vendor exception type."""

    def __init__(self, code: ErrorCode, message: str = "") -> None:
        super().__init__(code, message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}" if self.message else self.code.value


class CancelToken:
    """Cooperative cancellation. Core sets it (job cancelled / printer stopped); providers poll `cancelled` between
    network round-trips, abort the remote work best-effort, delete partial output and raise
    `ProviderError(ErrorCode.CANCELLED)`."""

    def __init__(self) -> None:
        self._set = False

    def cancel(self) -> None:
        self._set = True

    @property
    def cancelled(self) -> bool:
        return self._set


# ── targets, settings, requests, results ──────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TargetPrinter:
    """Construction/production target. Decision: BOTH. `bed_mm` (x, y, z) is always supplied by Themis (from the model
    registry entry, or the user's value for a custom model) so a provider never looks a model up. `model_uuid` is the core
    printer-model UUID (BIZ-262); it is None only for printer-less work (e.g. packing a library project by bed size)."""
    bed_mm: tuple[float, float, float]
    model_uuid: str | None = None
    toolheads: int = 1
    # The provider's machine profile for this printer, resolved by Themis from the model mapping + nozzle variant (BIZ-294). None for
    # printer-less work or an unmapped model/variant (which makes the provider ineligible before produce() is reached).
    machine_profile: str | None = None


@dataclass(frozen=True)
class FilamentChoice:
    """One model-filament slot. `profile` is the provider-advertised filament profile NAME (see `Profile`), chosen by
    Themis from inventory `profile_links`; None = provider default for `material`."""
    slot: int
    material: str
    profile: str | None = None
    color_hex: str | None = None     # preview only; never affects output, so never in the cache key


@dataclass(frozen=True)
class SliceSettings:
    """Themis-owned neutral settings (decision A). Everything here is persisted by Themis on the job/printer config and
    is a plain value: profile NAMES (resolved to provider refs at call time), neutral filament choices, and `overrides`
    limited to keys the provider advertises through `curated_settings()`."""
    process_profile: str | None = None
    filaments: tuple[FilamentChoice, ...] = ()
    overrides: Mapping[str, str] = field(default_factory=dict)
    tool_index: int | None = None
    filament_map: tuple[tuple[int, int], ...] | None = None   # (model_filament 1-based, tool_index 0-based)

    def fingerprint(self) -> str:
        return _sha({
            "process_profile": self.process_profile,
            "filaments": [[f.slot, f.material, f.profile] for f in self.filaments],   # colour excluded
            "overrides": dict(self.overrides),
            "tool_index": self.tool_index,
            "filament_map": [list(p) for p in self.filament_map] if self.filament_map else None,
        })


@dataclass(frozen=True)
class Profile:
    """A named, provider-advertised choice (process or filament profile). `compatible_models` are core model UUIDs the
    provider says it fits; empty = unrestricted."""
    name: str
    kind: str                         # "process" | "filament"
    compatible_models: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConstructRequest:
    inputs: tuple[Path, ...]          # accepted: MODEL_3MF, STL
    target: TargetPrinter


@dataclass(frozen=True)
class ProduceRequest:
    source: Path                      # accepted: MODEL_3MF, STL (never G-code)
    input_kind: InputKind
    plate: int
    target: TargetPrinter
    settings: SliceSettings = field(default_factory=SliceSettings)
    want: ArtifactKind = ArtifactKind.GCODE
    pin_engine_version: str | None = None    # None = provider's current engine


@dataclass(frozen=True)
class Artifact:
    path: Path
    kind: ArtifactKind
    produced_by: ProviderIdentity


# ── neutral inspection DTOs (BIZ-259 decision; offline FormatProvider is BIZ-281) ─────────────────────────────────

@dataclass(frozen=True)
class PlateEstimate:
    grams: float | None = None
    seconds: int | None = None
    per_extruder_grams: tuple[float, ...] | None = None


@dataclass(frozen=True)
class SettingChange:
    key: str
    from_value: str      # serialized as "from"
    to_value: str        # serialized as "to"


@dataclass(frozen=True)
class SlotWarning:
    used_slots: int
    printer_slots: int


@dataclass(frozen=True)
class OverrideInspection:
    has_embedded_settings: bool = False
    setting_changes: tuple[SettingChange, ...] = ()
    slot_warning: SlotWarning | None = None

    @property
    def has_findings(self) -> bool:
        return bool(self.setting_changes or self.slot_warning)


@dataclass(frozen=True)
class CuratedSetting:
    key: str
    label: str


# ── provider ABCs ─────────────────────────────────────────────────────────────────────────────────────────────────

class _Provider(ABC):
    @property
    @abstractmethod
    def identity(self) -> ProviderIdentity:
        """Cheap and local (no network): who this is. `engine_*` may be filled in by `status()` once the server answers."""

    @abstractmethod
    def status(self, timeout: float | None = DEFAULT_HEALTH_TIMEOUT_S) -> ProviderStatus:
        """Never raises for 'not usable': returns NOT_READY / UNREACHABLE. Queue preflight calls this."""


class ProjectConstructor(_Provider):
    """`3mf.construct`. One active provider. Preserving plates, instances, modifiers, painted supports and per-object
    settings across `construct` is a provider guarantee; an operation it cannot preserve must raise UNSUPPORTED_INPUT."""
    accepted_inputs: frozenset[InputKind] = frozenset({InputKind.MODEL_3MF, InputKind.STL})

    @abstractmethod
    def construct(self, request: ConstructRequest, output_dir: Path, *, timeout: float = DEFAULT_PRODUCE_TIMEOUT_S,
                  cancel: CancelToken | None = None) -> Artifact:
        """Writes one `PROJECT_3MF` into `output_dir`. Raises ProviderError."""


class GcodeProducer(_Provider):
    """`gcode.produce`. Many may be enabled; Themis picks one per (model input, target printer) via `resolve_work`."""
    accepted_inputs: frozenset[InputKind] = frozenset({InputKind.MODEL_3MF, InputKind.STL})
    output_kinds: frozenset[ArtifactKind] = frozenset({ArtifactKind.GCODE})

    @abstractmethod
    def supports_target(self, target: TargetPrinter) -> bool:
        """Local/cached decision (no network): can this provider produce for this printer model/bed?"""

    @abstractmethod
    def profiles(self, kind: str, target: TargetPrinter) -> Sequence[Profile]:
        """Profiles of `kind` usable for `target` (replaces `compatible_presets` for the neutral path)."""

    @abstractmethod
    def curated_settings(self) -> tuple[CuratedSetting, ...]:
        """The only keys `SliceSettings.overrides` may carry."""

    @abstractmethod
    def produce(self, request: ProduceRequest, output_dir: Path, *, timeout: float = DEFAULT_PRODUCE_TIMEOUT_S,
                cancel: CancelToken | None = None) -> Artifact:
        """Writes one artifact of `request.want` into `output_dir`. Raises ProviderError (TIMEOUT / CANCELLED included)."""


# ── queue-pull resolution (pure; reference implementation of the spec table) ─────────────────────────────────────

class Action(StrEnum):
    PASS_THROUGH = "pass_through"   # dispatch as-is; no provider touched
    PRODUCE = "produce"             # produce with `provider_id`
    PROCEED = "proceed"             # construct with `provider_id` (3mf.construct has no produce step)
    BLOCKED = "blocked"             # job stays queued/blocked for this printer; `reason` says why


class Reason(StrEnum):
    NONE = ""
    MODEL_INELIGIBLE = "model_ineligible"          # G-code not eligible for this printer model
    ELIGIBILITY_UNKNOWN = "eligibility_unknown"    # legacy G-code with no recorded models (never a match)
    NO_PROVIDER = "no_provider"                    # nothing enabled, or none supports this input+printer
    PREFERENCE_DORMANT = "preference_dormant"      # preferred provider disabled/removed
    PREFERENCE_INELIGIBLE = "preference_ineligible"
    PREFERENCE_UNAVAILABLE = "preference_unavailable"   # preferred provider not READY: wait, never fall back
    DEFAULT_UNAVAILABLE = "default_unavailable"    # also used by resolve_construct for its one active provider
    DEFAULT_INELIGIBLE = "default_ineligible"


@dataclass(frozen=True)
class WorkOutcome:
    action: Action
    provider_id: str | None = None
    reason: Reason = Reason.NONE


@dataclass(frozen=True)
class WorkItem:
    input_kind: InputKind
    gcode_models: frozenset[str] | None = None   # pass-through only: eligible model UUIDs; None/empty = unknown


def _eligible(p: GcodeProducer, item: WorkItem, target: TargetPrinter) -> bool:
    return item.input_kind in p.accepted_inputs and p.supports_target(target)


def resolve_work(
    item: WorkItem, target: TargetPrinter, providers: Mapping[str, GcodeProducer],
    status: Mapping[str, ProviderStatus], *, preferred: str | None = None, default: str | None = None,
) -> WorkOutcome:
    """One printer pulling one job. `providers` = the ENABLED gcode.produce providers; `status` their latest readiness
    (checked by the caller, outside this pure function). Order: pass-through, preference, default, else blocked. An
    explicit preference that is dormant/ineligible/unavailable blocks; it never falls back (BIZ-250)."""
    if item.input_kind in PASS_THROUGH_INPUTS:
        if not item.gcode_models:
            return WorkOutcome(Action.BLOCKED, reason=Reason.ELIGIBILITY_UNKNOWN)
        if target.model_uuid not in item.gcode_models:
            return WorkOutcome(Action.BLOCKED, reason=Reason.MODEL_INELIGIBLE)
        return WorkOutcome(Action.PASS_THROUGH)
    if not providers:
        return WorkOutcome(Action.BLOCKED, reason=Reason.NO_PROVIDER)

    def ready(pid: str) -> bool:
        st = status.get(pid)
        return st is not None and st.ready

    if preferred is not None:
        p = providers.get(preferred)
        if p is None:
            return WorkOutcome(Action.BLOCKED, preferred, Reason.PREFERENCE_DORMANT)
        if not _eligible(p, item, target):
            return WorkOutcome(Action.BLOCKED, preferred, Reason.PREFERENCE_INELIGIBLE)
        if not ready(preferred):
            return WorkOutcome(Action.BLOCKED, preferred, Reason.PREFERENCE_UNAVAILABLE)
        return WorkOutcome(Action.PRODUCE, preferred)
    if default is None:
        return WorkOutcome(Action.BLOCKED, reason=Reason.NO_PROVIDER)
    p = providers.get(default)
    if p is None or not ready(default):
        return WorkOutcome(Action.BLOCKED, default, Reason.DEFAULT_UNAVAILABLE)
    if not _eligible(p, item, target):
        return WorkOutcome(Action.BLOCKED, default, Reason.DEFAULT_INELIGIBLE)
    return WorkOutcome(Action.PRODUCE, default)


def resolve_job(
    item: WorkItem, targets: Mapping[str, TargetPrinter], providers: Mapping[str, GcodeProducer],
    status: Mapping[str, ProviderStatus], *, preferred: Mapping[str, str | None] | None = None, default: str | None = None,
) -> dict[str, WorkOutcome]:
    """Multiple target printers: each is resolved independently (own model, own preference). The job is claimable by
    any printer whose outcome is not BLOCKED; one blocked printer never blocks the others."""
    preferred = preferred or {}
    return {pid: resolve_work(item, t, providers, status, preferred=preferred.get(pid), default=default)
            for pid, t in targets.items()}


def resolve_construct(active: ProjectConstructor | None, status: ProviderStatus | None) -> WorkOutcome:
    """`3mf.construct` is exclusive: the one active provider or blocked (no fallback, no preference)."""
    if active is None:
        return WorkOutcome(Action.BLOCKED, reason=Reason.NO_PROVIDER)
    if status is None or not status.ready:
        return WorkOutcome(Action.BLOCKED, active.identity.provider_id, Reason.DEFAULT_UNAVAILABLE)
    return WorkOutcome(Action.PROCEED, active.identity.provider_id)
