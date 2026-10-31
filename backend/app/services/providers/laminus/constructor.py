"""Laminus as the neutral `ProjectConstructor` (`3mf.construct`, BIZ-296): packs loose STLs onto as many plates of the target's bed
as they need (`POST /api/v1/pack` by bed size) and writes one `PROJECT_3MF`.

STL only: `/pack` drops a 3MF's geometry, modifiers and per-object settings, so a 3MF input is `UNSUPPORTED_INPUT` (the contract's
"cannot preserve it" rule). `target.model_uuid` is not needed: the pack is geometry-only, slicing profiles apply at dispatch."""
from __future__ import annotations

from pathlib import Path

from ..contract import (
    Artifact, ArtifactKind, CancelToken, ConstructRequest, DEFAULT_HEALTH_TIMEOUT_S, DEFAULT_PRODUCE_TIMEOUT_S, ErrorCode,
    InputKind, ProjectConstructor, ProviderError, ProviderIdentity, ProviderStatus, Readiness,
)
from .sidecar_client import (
    DEFAULT_REQUEST_TIMEOUT_S, LaminusSidecarClient, SidecarError, SidecarNotReady, SidecarTimeout, SidecarUnreachable,
)

PROVIDER_ID = "laminus"
OUTPUT_NAME = "project.3mf"


class LaminusProjectConstructor(ProjectConstructor):
    accepted_inputs = frozenset({InputKind.STL})

    def __init__(self, url: str, *, version: str, request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S) -> None:
        self._url = url
        self._version = version
        self._request_timeout_s = request_timeout_s     # the plugin's per-request ceiling; a call's `timeout` can only lower it

    @property
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(PROVIDER_ID, self._version)

    def status(self, timeout: float | None = DEFAULT_HEALTH_TIMEOUT_S) -> ProviderStatus:
        try:
            LaminusSidecarClient(self._url, timeout=timeout).health()
        except SidecarNotReady as e:
            return ProviderStatus(Readiness.NOT_READY, self.identity, str(e))
        except SidecarError as e:
            return ProviderStatus(Readiness.UNREACHABLE, self.identity, str(e))
        return ProviderStatus(Readiness.READY, self.identity)

    def construct(self, request: ConstructRequest, output_dir: Path, *, timeout: float = DEFAULT_PRODUCE_TIMEOUT_S,
                  cancel: CancelToken | None = None) -> Artifact:
        if cancel is not None and cancel.cancelled:
            raise ProviderError(ErrorCode.CANCELLED)
        if not all(d > 0 for d in request.target.bed_mm):
            raise ProviderError(ErrorCode.UNSUPPORTED_TARGET, f"bed {request.target.bed_mm} has no positive size")
        if not request.inputs:
            raise ProviderError(ErrorCode.UNSUPPORTED_INPUT, "nothing to pack")
        others = [i.path.name for i in request.inputs if i.path.suffix.lower() != ".stl"]
        if others:
            raise ProviderError(ErrorCode.UNSUPPORTED_INPUT, f"only STL models can be packed: {', '.join(others)}")
        try:
            packed = LaminusSidecarClient(self._url, timeout=min(timeout, self._request_timeout_s)).pack_stls(list(request.inputs), *request.target.bed_mm)
        except SidecarTimeout as e:
            raise ProviderError(ErrorCode.TIMEOUT, str(e)) from e
        except SidecarUnreachable as e:
            raise ProviderError(ErrorCode.UNREACHABLE, str(e)) from e
        except SidecarNotReady as e:
            raise ProviderError(ErrorCode.NOT_READY, str(e)) from e
        except SidecarError as e:
            raise ProviderError(ErrorCode.FAILED, str(e)) from e
        # The pack is one blocking request: a cancel that arrived meanwhile discards its result rather than publishing it.
        if cancel is not None and cancel.cancelled:
            raise ProviderError(ErrorCode.CANCELLED)
        out = Path(output_dir) / OUTPUT_NAME
        out.write_bytes(packed)
        return Artifact(out, ArtifactKind.PROJECT_3MF, self.identity)
