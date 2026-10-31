"""Laminus as a neutral `GcodeProducer` (BIZ-291, BIZ-295): identity, readiness (`status`), target support, advertised profiles and
`produce()` over the existing `LaminusSlicingProvider`. The queue selects and preflights it (`resolve_work`), then every slice runs
through `produce()` (via core's `SlicerService`): profile names -> catalog refs, curated overrides + plate type -> `extra_config`,
tool mapping on a job-scoped copy, `SlicingProvider.slice` with a cancel check between polls (docs/architecture/laminus-contract.md §8)."""
from __future__ import annotations

import re
import shutil
from collections.abc import Sequence
from pathlib import Path

from ..contract import (
    Artifact, ArtifactKind, CancelToken, CuratedSetting, DEFAULT_HEALTH_TIMEOUT_S, DEFAULT_PRODUCE_TIMEOUT_S, ErrorCode, GcodeProducer,
    InputKind, ProduceRequest, Profile, ProviderError, ProviderIdentity, ProviderStatus, Readiness, TargetPrinter,
)
from ..slicing import SliceSpec, SlicingProviderNotReady, SlicingProviderError, default_compatible_presets
from . import overrides
from .adapter import LaminusSlicingProvider
from .sidecar_client import SidecarCancelled, SidecarTimeout, SidecarUnreachable

PROVIDER_ID = "laminus"
ENGINE_ID = "orcaslicer"


def machine_ref(name: str) -> str:
    """Laminus's neutral profile ref, `<kind>:<slug(name)>` (its documented contract, `app/neutral_profiles.make_ref`). The
    profile list carries no `ref`, so Themis derives it from the machine profile name it stores."""
    return "machine:" + re.sub(r"[^a-z0-9.]+", "-", name.lower()).strip("-")


class LaminusGcodeProducer(GcodeProducer):
    output_kinds = frozenset({ArtifactKind.GCODE, ArtifactKind.GCODE_3MF})

    def __init__(self, slicing: LaminusSlicingProvider, *, version: str) -> None:
        self._slicing = slicing
        self._version = version

    @property
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(PROVIDER_ID, self._version, engine_id=ENGINE_ID)

    def status(self, timeout: float | None = DEFAULT_HEALTH_TIMEOUT_S) -> ProviderStatus:
        try:
            info = self._slicing.health(timeout)
        except SlicingProviderNotReady as e:
            return ProviderStatus(Readiness.NOT_READY, self.identity, str(e))
        except SlicingProviderError as e:
            return ProviderStatus(Readiness.UNREACHABLE, self.identity, str(e))
        info = info if isinstance(info, dict) else {}
        engine_version = info.get("orca_version") or info.get("orcaslicer_version") or None
        identity = ProviderIdentity(PROVIDER_ID, self._version, engine_id=ENGINE_ID,
                                    engine_version=str(engine_version) if engine_version else None)
        return ProviderStatus(Readiness.READY, identity)

    def supports_target(self, target: TargetPrinter) -> bool:
        # Local decision, no network: a sensible bed and a machine profile Themis mapped for this printer (BIZ-294) that the cached
        # catalog knows. With no catalog cached yet (boot) the name can't be checked, so it is accepted; the slice then reports an
        # unknown profile as an INVALID_SETTINGS failure instead of every job hard-blocking until the catalog loads.
        if not all(d > 0 for d in target.bed_mm) or not target.machine_profile:
            return False
        from ...catalog_service import cached_catalog

        catalog = cached_catalog()
        return catalog is None or target.machine_profile in catalog.names("machine")

    def profiles(self, kind: str, target: TargetPrinter) -> Sequence[Profile]:
        from ...catalog_service import cached_catalog

        catalog = cached_catalog()
        if catalog is None or kind not in ("process", "filament"):
            return []
        if not target.machine_profile:
            return [Profile(p.name, kind) for p in catalog.presets_of(kind) if p.name]
        try:      # Laminus resolves compatibility itself (BIZ-293); the catalog filter is the fallback (e.g. 404 unknown_machine_ref)
            return [Profile(x["name"], kind) for x in self._slicing.machine_presets(machine_ref(target.machine_profile), kind)
                    if x.get("name")]
        except SlicingProviderError:
            pass
        return [Profile(p.name, kind) for p in default_compatible_presets(catalog, target.machine_profile, kind) if p.name]

    def curated_settings(self) -> tuple[CuratedSetting, ...]:
        return overrides.CURATED_SETTINGS

    def produce(self, request: ProduceRequest, output_dir: Path, *, timeout: float = DEFAULT_PRODUCE_TIMEOUT_S,
                cancel: CancelToken | None = None) -> Artifact:
        """`timeout` is advisory here: the wait is bounded by the plugin's own poll ceiling (`poll_timeout_s`)."""
        _check(cancel)
        if request.input_kind not in self.accepted_inputs:
            raise ProviderError(ErrorCode.UNSUPPORTED_INPUT, f"{request.input_kind.value} is not a model input")
        if request.want not in self.output_kinds:
            raise ProviderError(ErrorCode.INVALID_SETTINGS, f"cannot produce {request.want.value}")
        machine = request.target.machine_profile
        if not machine:
            raise ProviderError(ErrorCode.UNSUPPORTED_TARGET, "no machine profile is mapped for this printer")
        settings = request.settings
        unknown = sorted(set(settings.overrides) - {c.key for c in self.curated_settings()})
        if unknown:
            raise ProviderError(ErrorCode.INVALID_SETTINGS, f"not a curated setting: {', '.join(unknown)}")

        from ...slicer_service import ProfileCatalogUnavailable, SliceError, resolve_preset_uuids
        try:
            machine_ref, process_ref, filament_refs = resolve_preset_uuids(
                machine, settings.process_profile or "", [f.profile for f in settings.filaments if f.profile], self._slicing)
        except ProfileCatalogUnavailable as e:
            raise ProviderError(ErrorCode.UNREACHABLE, str(e)) from e
        except SliceError as e:
            raise ProviderError(ErrorCode.INVALID_SETTINGS, str(e)) from e

        source = Path(request.source)
        if settings.tool_index is not None or settings.filament_map:
            # Never remap the shared library file: one printer's tool routing would corrupt it for everyone else.
            prepared = Path(output_dir) / "prepared.3mf"
            shutil.copy2(source, prepared)
            fmap = [{"model_filament": m, "tool_index": t} for m, t in settings.filament_map] if settings.filament_map else None
            try:
                self._slicing.apply_tool_mapping(prepared, tool_index=settings.tool_index, filament_map=fmap)
            except SlicingProviderError as e:
                raise ProviderError(ErrorCode.INVALID_SETTINGS, str(e)) from e
            source = prepared
        _check(cancel)

        extra = {**({"curr_bed_type": settings.plate_type} if settings.plate_type else {}), **dict(settings.overrides)}
        spec = SliceSpec(source_file=source, plate=request.plate, machine_ref=machine_ref, process_ref=process_ref,
                         filament_refs=filament_refs, export_3mf=request.want is ArtifactKind.GCODE_3MF, extra_config=extra)
        try:
            path = Path(self._slicing.slice(spec, Path(output_dir),
                                            should_stop=(lambda: cancel.cancelled) if cancel is not None else None))
        except SlicingProviderNotReady as e:
            raise ProviderError(ErrorCode.NOT_READY, str(e)) from e
        except SlicingProviderError as e:
            cause = e.__cause__
            cancelled = isinstance(cause, SidecarCancelled) or (cancel is not None and cancel.cancelled)
            code = (ErrorCode.CANCELLED if cancelled
                    else ErrorCode.TIMEOUT if isinstance(cause, SidecarTimeout)
                    else ErrorCode.UNREACHABLE if isinstance(cause, SidecarUnreachable) else ErrorCode.FAILED)
            raise ProviderError(code, str(e)) from e
        if cancel is not None and cancel.cancelled:     # cancelled while downloading: never hand back a result
            path.unlink(missing_ok=True)
            raise ProviderError(ErrorCode.CANCELLED)
        return Artifact(path, request.want, self.identity)


def _check(cancel: CancelToken | None) -> None:
    if cancel is not None and cancel.cancelled:
        raise ProviderError(ErrorCode.CANCELLED)
