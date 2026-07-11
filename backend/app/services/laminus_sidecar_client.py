from __future__ import annotations

import time
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 630  # seconds — slightly over sidecar's 600 s slice timeout


class SidecarError(Exception):
    pass


class LaminusSidecarClient:
    """Synchronous httpx client for the Laminus sidecar API.

    Synchronous because SlicerService runs inside a ThreadPoolExecutor — callers
    must wrap with asyncio.to_thread if calling from an async context.
    """

    def __init__(self, base_url: str, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
        )

    def health(self) -> dict:
        """GET /api/health — raises SidecarError on non-200."""
        try:
            r = self._client.get("/api/health")
        except httpx.HTTPError as e:
            raise SidecarError(f"health check request failed: {e}") from e
        if r.status_code != 200:
            raise SidecarError(f"health check returned {r.status_code}")
        return r.json()

    def slice_start(
        self,
        source_file: Path,
        machine_uuid: str,
        process_uuid: str,
        filament_uuids: list[str],
        plate: int,
        export_3mf: bool = False,
        extra_config: "dict | None" = None,
    ) -> str:
        """POST /api/slice/start → job_id string.

        Delegates profile resolution, 3MF assembly, and slicing entirely to the sidecar.
        ``extra_config`` is merged into the resolved project settings by the sidecar
        (bed type, job-level setting overrides, etc.).
        """
        import json as _json
        data: dict[str, str] = {
            "machine_uuid": machine_uuid,
            "process_uuid": process_uuid,
            "filament_uuids": _json.dumps(filament_uuids),
            "plate": str(plate),
            "geometry_only_retry": "true",
        }
        if export_3mf:
            data["export_3mf"] = source_file.stem + ".gcode.3mf"
        if extra_config:
            data["extra_config"] = _json.dumps(extra_config)

        try:
            with open(source_file, "rb") as fh:
                r = self._client.post(
                    "/api/slice/start",
                    files={"file": (source_file.name, fh, "application/octet-stream")},
                    data=data,
                )
        except httpx.HTTPError as e:
            raise SidecarError(f"slice/start request failed: {e}") from e

        if r.status_code != 200:
            raise SidecarError(f"slice/start returned {r.status_code}: {r.text[:300]}")
        return r.json()["job_id"]

    def slice_prepared(
        self,
        prepared_3mf: Path,
        plate: int,
        export_3mf: bool = False,
        geometry_only_retry: bool = False,
    ) -> str:
        """POST /api/slice/prepared → job_id string."""
        data: dict[str, str] = {
            "plate": str(plate),
            "geometry_only_retry": "false",  # Themis manages its own two-tier retry
        }
        if export_3mf:
            # Non-empty value activates gcode_3mf output; the sidecar picks the filename
            data["export_3mf"] = prepared_3mf.stem + ".gcode.3mf"

        try:
            with open(prepared_3mf, "rb") as fh:
                r = self._client.post(
                    "/api/slice/prepared",
                    files={"file": (prepared_3mf.name, fh, "application/octet-stream")},
                    data=data,
                )
        except httpx.HTTPError as e:
            raise SidecarError(f"slice/prepared request failed: {e}") from e

        if r.status_code != 200:
            raise SidecarError(
                f"slice/prepared returned {r.status_code}: {r.text[:300]}"
            )
        return r.json()["job_id"]

    def poll_status(
        self,
        job_id: str,
        poll_interval: float = 2.0,
        timeout: float = 620.0,
    ) -> dict:
        """Poll GET /api/slice/status/{job_id} until completed or failed.

        Returns the completed status dict. Raises SidecarError on failure or timeout.
        """
        deadline = time.monotonic() + timeout
        while True:
            try:
                r = self._client.get(f"/api/slice/status/{job_id}")
            except httpx.HTTPError as e:
                raise SidecarError(f"status poll request failed: {e}") from e

            if r.status_code != 200:
                raise SidecarError(f"status returned {r.status_code}: {r.text[:200]}")

            body = r.json()
            status = body.get("status")

            if status == "completed":
                return body
            if status == "failed":
                raise SidecarError(
                    f"sidecar slice failed: {body.get('error') or 'unknown error'}"
                )
            if time.monotonic() > deadline:
                raise SidecarError(
                    f"sidecar poll timed out after {timeout:.0f}s for job {job_id}"
                )

            time.sleep(poll_interval)

    def download(self, job_id: str, dest: Path) -> Path:
        """GET /api/slice/download/{job_id} → write bytes to dest, return dest.

        Note: downloading evicts the job on the sidecar side.
        """
        try:
            r = self._client.get(f"/api/slice/download/{job_id}")
        except httpx.HTTPError as e:
            raise SidecarError(f"download request failed: {e}") from e

        if r.status_code != 200:
            raise SidecarError(f"download returned {r.status_code}: {r.text[:200]}")

        dest.write_bytes(r.content)
        return dest

    def get_catalog(self) -> dict:
        """GET /api/profiles → full machine/process/filament catalog dict."""
        try:
            r = self._client.get("/api/profiles", timeout=30)
        except httpx.HTTPError as e:
            raise SidecarError(f"profiles request failed: {e}") from e
        if r.status_code != 200:
            raise SidecarError(f"profiles returned {r.status_code}: {r.text[:200]}")
        return r.json()

    def get_merged_config(
        self,
        machine_uuid: str,
        process_uuid: str,
        filament_uuids: list[str],
    ) -> dict:
        """POST /api/profiles/merged-config → merged project_settings dict.

        Returns the fully resolved, inheritance-flattened project config for the
        given profile UUIDs — the same dict that would be embedded in a 3MF before
        slicing. Used by check-overrides to diff against an uploaded 3MF's settings.
        """
        payload = {
            "machine_uuid": machine_uuid,
            "process_uuid": process_uuid,
            "filament_uuids": filament_uuids,
        }
        try:
            r = self._client.post("/api/profiles/merged-config", json=payload, timeout=30)
        except httpx.HTTPError as e:
            raise SidecarError(f"profiles/merged-config request failed: {e}") from e
        if r.status_code != 200:
            raise SidecarError(f"profiles/merged-config returned {r.status_code}: {r.text[:300]}")
        return r.json()

    def arrange(
        self,
        threemf_path: Path,
        arrange: bool = True,
        orient: bool = True,
        timeout: float = 130.0,
    ) -> bytes:
        """POST /api/arrange → arranged multi-plate 3MF bytes.

        Sends a pre-built 3MF (with model_settings.config extruder assignments) to
        the sidecar for plate arrangement and returns the rearranged 3MF bytes.
        Raises SidecarError on failure or timeout.
        """
        try:
            with open(threemf_path, "rb") as fh:
                r = self._client.post(
                    "/api/arrange",
                    files={"file": (threemf_path.name, fh, "application/octet-stream")},
                    data={
                        "arrange": "1" if arrange else "0",
                        "orient": "1" if orient else "0",
                    },
                    timeout=timeout,
                )
        except httpx.HTTPError as e:
            raise SidecarError(f"arrange request failed: {e}") from e
        if r.status_code == 408:
            raise SidecarError("arrange timed out on sidecar")
        if r.status_code != 200:
            raise SidecarError(f"arrange returned {r.status_code}: {r.text[:300]}")
        return r.content

    def pack_stls(
        self,
        stl_paths: list[Path],
        bed_x: float,
        bed_y: float,
        bed_z: float = 250.0,
    ) -> bytes:
        """POST /api/pack → multi-plate arranged 3MF bytes."""
        file_handles = [(p, open(p, "rb")) for p in stl_paths]
        try:
            files = [
                ("files", (p.name, fh, "application/octet-stream"))
                for p, fh in file_handles
            ]
            data = {"bed_x": str(bed_x), "bed_y": str(bed_y), "bed_z": str(bed_z)}
            try:
                r = self._client.post("/api/pack", files=files, data=data)
            except httpx.HTTPError as e:
                raise SidecarError(f"pack request failed: {e}") from e
        finally:
            for _, fh in file_handles:
                fh.close()

        if r.status_code != 200:
            raise SidecarError(f"pack returned {r.status_code}: {r.text[:300]}")
        return r.content

    def pack_stls_by_uuid(
        self,
        stl_paths: list[Path],
        machine_uuid: str,
        process_uuid: str,
        filament_uuids: list[str],
    ) -> bytes:
        """POST /api/pack (UUID mode) → multi-plate 3MF bytes with embedded settings.

        The sidecar resolves bed dimensions and profile settings from the given
        UUIDs, so no bed_x/bed_y is needed.
        """
        import json as _json
        file_handles = [(p, open(p, "rb")) for p in stl_paths]
        try:
            files = [
                ("files", (p.name, fh, "application/octet-stream"))
                for p, fh in file_handles
            ]
            data = {
                "machine_uuid": machine_uuid,
                "process_uuid": process_uuid,
                "filament_uuids": _json.dumps(filament_uuids),
            }
            try:
                r = self._client.post("/api/pack", files=files, data=data)
            except httpx.HTTPError as e:
                raise SidecarError(f"pack request failed: {e}") from e
        finally:
            for _, fh in file_handles:
                fh.close()

        if r.status_code != 200:
            raise SidecarError(f"pack returned {r.status_code}: {r.text[:300]}")
        return r.content
