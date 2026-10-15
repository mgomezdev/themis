"""Laminus adapter for SlicingProvider. Owns the base URL, per-call timeouts and the slice poll loop
(defaults: 630s client timeout, 620s poll ceiling; both are Laminus plugin settings, BIZ-288)."""
from __future__ import annotations

from pathlib import Path

from .sidecar_client import (
    DEFAULT_POLL_TIMEOUT_S, DEFAULT_REQUEST_TIMEOUT_S, LaminusSidecarClient, SidecarError, SidecarNotReady,
)
from ..slicing import (
    Catalog,
    MachineCandidate,
    NozzleVariantOption,
    Preset,
    SliceSpec,
    SlicingProvider,
    SlicingProviderError,
    SlicingProviderNotReady,
)


def _preset(raw: dict) -> Preset:
    return Preset(
        ref=raw.get("uuid") or "",
        name=raw.get("name") or "",
        compatible_printers=list(raw.get("compatible_printers") or []),
        raw=raw,
    )


def catalog_from_legacy(raw: dict) -> Catalog:
    return Catalog(
        machines=[_preset(m) for m in raw.get("machine", [])],
        processes=[_preset(p) for p in raw.get("process", [])],
        filaments=[_preset(f) for f in raw.get("filament", [])],
        raw=raw,
    )


class LaminusSlicingProvider(SlicingProvider):
    ARRANGE = True
    PACK_MODELS = True
    PREPARED_PROJECT = True
    TOOL_MAPPING = True
    SUGGEST_PROFILES = True

    def __init__(self, url: str, *, request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
                 poll_timeout_s: float = DEFAULT_POLL_TIMEOUT_S) -> None:
        self._url = url
        self._request_timeout_s = request_timeout_s
        self._poll_timeout_s = poll_timeout_s

    def apply_tool_mapping(self, source_3mf: Path, *, tool_index: int | None = None,
                           filament_map: list[dict] | None = None) -> None:
        if tool_index is not None and filament_map:
            raise SlicingProviderError("tool_index and filament_map are mutually exclusive")
        if tool_index is None and not filament_map:
            return
        from ...snapmaker.remap import remap_3mf      # the Orca paint_color codec lives in Themis for now (BIZ-248)
        remap_3mf(source_3mf, tool_index=tool_index, filament_map=filament_map)

    @property
    def identity(self) -> str:
        return self._url

    def _client(self, timeout: float | None = None) -> LaminusSidecarClient:
        return LaminusSidecarClient(self._url, timeout=self._request_timeout_s if timeout is None else timeout)

    def health(self, timeout: float | None = None) -> dict:
        try:
            return self._client(timeout).health()
        except SidecarNotReady as e:
            raise SlicingProviderNotReady(str(e)) from e
        except SidecarError as e:
            raise SlicingProviderError(str(e)) from e

    def catalog_health(self, timeout: float = 5.0) -> dict:
        try:
            return self._client().catalog_state(timeout)
        except SidecarError as e:
            raise SlicingProviderError(str(e)) from e

    def request_catalog_rebuild(self, timeout: float = 10.0) -> None:
        try:
            self._client().request_catalog_rebuild(timeout)
        except SidecarError as e:
            raise SlicingProviderError(str(e)) from e

    def get_catalog(self) -> Catalog:
        try:
            return catalog_from_legacy(self._client().get_catalog())
        except SidecarError as e:
            raise SlicingProviderError(str(e)) from e

    def suggest_machines(self, manufacturer: str, model: str, model_key: str | None = None) -> list[MachineCandidate]:
        try:
            raw = self._client(15).suggest_machines(manufacturer, model, model_key)
        except SidecarError as e:
            raise SlicingProviderError(str(e)) from e
        return [MachineCandidate(name=c.get("name") or "", nozzle_diameter_mm=c.get("nozzle_diameter_mm"),
                                 flow=c.get("flow") or "standard", ref=c.get("ref") or "",
                                 confidence=float(c.get("confidence") or 0.0), match_reason=c.get("match_reason") or "")
                for c in raw if c.get("name")]

    def nozzle_variants(self, manufacturer: str, model: str, model_key: str | None = None) -> list[NozzleVariantOption]:
        try:
            raw = self._client(15).model_variants(manufacturer, model, model_key)
        except SidecarError as e:
            raise SlicingProviderError(str(e)) from e
        return [NozzleVariantOption(name=v.get("name") or "", nozzle_diameter_mm=v.get("nozzle_diameter_mm"),
                                    flow=v.get("flow") or "standard", ref=v.get("ref") or "",
                                    is_default_guess=bool(v.get("is_default_guess")))
                for v in raw if v.get("name")]

    def merged_config(
        self, machine_ref: str, process_ref: str, filament_refs: list[str], timeout: float | None = None,
    ) -> dict:
        try:
            return self._client(timeout).get_merged_config(machine_ref, process_ref, filament_refs)
        except SidecarError as e:
            raise SlicingProviderError(str(e)) from e

    def slice(self, spec: SliceSpec, output_dir: Path) -> str:
        client = self._client()
        try:
            if spec.prepared:
                job_id = client.slice_prepared(spec.source_file, spec.plate, export_3mf=spec.export_3mf)
            else:
                job_id = client.slice_start(
                    spec.source_file, spec.machine_ref, spec.process_ref, spec.filament_refs,
                    spec.plate, export_3mf=spec.export_3mf,
                    extra_config=spec.extra_config or None,
                )
            status = client.poll_status(job_id, timeout=self._poll_timeout_s)
            return str(client.download(job_id, output_dir / status["sliced_file"]))
        except SidecarError as e:
            raise SlicingProviderError(str(e)) from e

    def arrange(self, project_path: Path, arrange: bool = True, orient: bool = True, timeout: float = 130.0) -> bytes:
        try:
            return self._client().arrange(project_path, arrange=arrange, orient=orient, timeout=timeout)
        except SidecarError as e:
            raise SlicingProviderError(str(e)) from e

    def pack_models(
        self, paths: list[Path], *, machine_ref: str | None = None, process_ref: str | None = None,
        filament_refs: list[str] | None = None, bed: tuple[float, float, float] | None = None,
    ) -> bytes:
        client = self._client()
        try:
            if machine_ref is not None:
                return client.pack_stls_by_uuid(paths, machine_ref, process_ref or "", filament_refs or [])
            if bed is None:
                raise SlicingProviderError("pack_models needs presets or bed dimensions")
            return client.pack_stls(paths, *bed)
        except SidecarError as e:
            raise SlicingProviderError(str(e)) from e
