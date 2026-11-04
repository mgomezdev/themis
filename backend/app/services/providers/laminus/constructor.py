"""Laminus as the neutral `ProjectConstructor` (`3mf.construct`, BIZ-296): packs loose STLs onto as many plates of the target's bed
as they need (`POST /api/v1/pack` by bed size) and writes one `PROJECT_3MF`.

STL only: `/pack` drops a 3MF's geometry, modifiers and per-object settings, so a 3MF input is `UNSUPPORTED_INPUT` (the contract's
"cannot preserve it" rule). `target.model_uuid` is not needed: the pack is geometry-only, slicing profiles apply at dispatch."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ..contract import (
    Artifact, ArtifactKind, CancelToken, ConstructRequest, DEFAULT_HEALTH_TIMEOUT_S, DEFAULT_PRODUCE_TIMEOUT_S, ErrorCode,
    InputKind, ModelObject, NothingFits, PlateManifest, ProjectConstructor, ProviderError, ProviderIdentity, ProviderStatus, Readiness,
)
from .sidecar_client import (
    DEFAULT_REQUEST_TIMEOUT_S, LaminusSidecarClient, SidecarError, SidecarNotReady, SidecarTimeout, SidecarUnreachable,
)

PROVIDER_ID = "laminus"
OUTPUT_NAME = "project.3mf"
MANIFEST_ENTRY = "Metadata/laminus_manifest.json"      # written by /pack, /projects/subset, /projects/merge (feature `pack.manifest`)


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
            LaminusSidecarClient(self._url, timeout=DEFAULT_HEALTH_TIMEOUT_S if timeout is None else timeout).health()
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
        # Plain split STLs keep /pack; anything else (3MF objects/plates, together/solo rules) needs /pack/mixed (BIZ-356).
        mixed = any(i.path.suffix.lower() != ".stl" or i.rule != "split" or i.object_id or i.plate for i in request.inputs)
        try:
            client = LaminusSidecarClient(self._url, timeout=min(timeout, self._request_timeout_s))
            packed = (client.pack_mixed(list(request.inputs), *request.target.bed_mm) if mixed
                      else client.pack_stls(list(request.inputs), *request.target.bed_mm))
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

    def plate_manifest(self, project_3mf: Path) -> PlateManifest | None:
        """Read the sidecar's manifest (schema version 1) out of a packed project; None when absent, unreadable or unknown."""
        try:
            with zipfile.ZipFile(project_3mf) as zf:
                raw = json.loads(zf.read(MANIFEST_ENTRY))
            if raw.get("version") != 1 or not isinstance(raw.get("plates"), list):
                return None
            plates: dict[int, dict[int, int]] = {}
            for plate in raw["plates"]:
                counts = plates.setdefault(int(plate["plate"]), {})
                for inst in plate.get("instances", []):
                    counts[int(inst["item"])] = counts.get(int(inst["item"]), 0) + 1
            requested = {int(i["item"]): int(i["requested"]) for i in raw.get("items", []) if "requested" in i}
            return PlateManifest(plates=plates, requested=requested)
        except (OSError, KeyError, ValueError, TypeError, AttributeError, zipfile.BadZipFile):
            return None

    def next_plate(self, request: ConstructRequest, output_dir: Path, *, max_parts: int | None = None,
                   timeout: float = DEFAULT_PRODUCE_TIMEOUT_S, cancel: CancelToken | None = None):
        """/pack/next for the target's machine profile (resolved to its catalog uuid)."""
        from ...catalog_service import cached_catalog
        from .sidecar_client import SidecarNothingFits
        machine = request.target.machine_profile
        catalog = cached_catalog()
        uuid = catalog.ref_for("machine", machine) if catalog is not None and machine else None
        if not uuid:
            raise ProviderError(ErrorCode.UNSUPPORTED_TARGET, f"no machine profile uuid for {machine!r}")
        try:
            packed, consumed = LaminusSidecarClient(self._url, timeout=min(timeout, self._request_timeout_s)).pack_next(
                list(request.inputs), uuid, max_parts)
        except SidecarNothingFits as e:
            raise NothingFits(str(e)) from e
        except SidecarTimeout as e:
            raise ProviderError(ErrorCode.TIMEOUT, str(e)) from e
        except SidecarUnreachable as e:
            raise ProviderError(ErrorCode.UNREACHABLE, str(e)) from e
        except SidecarNotReady as e:
            raise ProviderError(ErrorCode.NOT_READY, str(e)) from e
        except SidecarError as e:
            raise ProviderError(ErrorCode.FAILED, str(e)) from e
        if cancel is not None and cancel.cancelled:
            raise ProviderError(ErrorCode.CANCELLED)
        out = Path(output_dir) / "next-plate.3mf"
        out.write_bytes(packed)
        return Artifact(out, ArtifactKind.PROJECT_3MF, self.identity), {i: n for i, n in enumerate(consumed) if n}

    def list_objects(self, model_3mf: Path) -> list[ModelObject]:
        try:
            raw = LaminusSidecarClient(self._url, timeout=self._request_timeout_s).inspect_objects(model_3mf)
        except SidecarError as e:
            code = (ErrorCode.UNREACHABLE if isinstance(e, SidecarUnreachable) else
                    ErrorCode.NOT_READY if isinstance(e, SidecarNotReady) else ErrorCode.FAILED)
            raise ProviderError(code, str(e)) from e
        return [ModelObject(str(o["id"]), str(o.get("name") or f"object {o['id']}"), int(o.get("instances") or 1)) for o in raw]
