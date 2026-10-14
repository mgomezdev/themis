"""Laminus as a neutral `GcodeProducer` (BIZ-291): identity, readiness (`status`), target support and advertised profiles over the
existing `LaminusSlicingProvider`. The queue uses it to *select* and *preflight* the provider (`resolve_work`); the slice itself still
runs through `SlicingProvider.slice` with the printer's machine/process/filament presets.

`produce()` is deliberately not implemented for Laminus yet: the neutral `TargetPrinter` carries a bed and a model UUID but no Orca
machine profile, which the sidecar needs to slice. It rejects model-less kinds exactly as the contract says and otherwise raises
`UNSUPPORTED_TARGET` until the target grows that mapping (docs/architecture/laminus-contract.md §8)."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..contract import (
    Artifact, CancelToken, CuratedSetting, DEFAULT_HEALTH_TIMEOUT_S, DEFAULT_PRODUCE_TIMEOUT_S, ErrorCode, GcodeProducer,
    InputKind, ProduceRequest, Profile, ProviderError, ProviderIdentity, ProviderStatus, Readiness, TargetPrinter,
)
from ..slicing import SlicingProviderNotReady, SlicingProviderError
from . import overrides
from .adapter import LaminusSlicingProvider

PROVIDER_ID = "laminus"
ENGINE_ID = "orcaslicer"


class LaminusGcodeProducer(GcodeProducer):
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
        # Local decision, no network. Laminus slices for any machine in its catalog; whether THIS printer's preset exists is a
        # slice-time check (a failure blocks the job on that printer), so only a nonsensical bed is refused here.
        return all(d > 0 for d in target.bed_mm)

    def profiles(self, kind: str, target: TargetPrinter) -> Sequence[Profile]:
        from ...catalog_service import cached_catalog

        catalog = cached_catalog()
        if catalog is None or kind not in ("process", "filament"):
            return []
        return [Profile(p.name, kind) for p in catalog.presets_of(kind) if p.name]

    def curated_settings(self) -> tuple[CuratedSetting, ...]:
        return overrides.CURATED_SETTINGS

    def produce(self, request: ProduceRequest, output_dir: Path, *, timeout: float = DEFAULT_PRODUCE_TIMEOUT_S,
                cancel: CancelToken | None = None) -> Artifact:
        if cancel is not None and cancel.cancelled:
            raise ProviderError(ErrorCode.CANCELLED)
        if request.input_kind not in self.accepted_inputs:
            raise ProviderError(ErrorCode.UNSUPPORTED_INPUT, f"{request.input_kind.value} is not a model input")
        raise ProviderError(
            ErrorCode.UNSUPPORTED_TARGET,
            "Laminus slices through SlicingProvider.slice with the printer's presets; the neutral target has no machine profile yet")
