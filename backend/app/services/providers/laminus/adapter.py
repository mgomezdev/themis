"""Laminus adapter for SlicingProvider. Owns the base URL, per-call timeouts and the slice poll loop
(defaults unchanged: 630s client timeout, 620s poll ceiling)."""
from __future__ import annotations

from pathlib import Path

from ...laminus_sidecar_client import LaminusSidecarClient, SidecarError, SidecarNotReady
from ..slicing import (
    Catalog,
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

    def __init__(self, url: str) -> None:
        self._url = url

    @property
    def identity(self) -> str:
        return self._url

    def _client(self, timeout: float | None = None) -> LaminusSidecarClient:
        return LaminusSidecarClient(self._url) if timeout is None else LaminusSidecarClient(self._url, timeout=timeout)

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
            status = client.poll_status(job_id)
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
