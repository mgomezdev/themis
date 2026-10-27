from __future__ import annotations

import json
import time
import logging
from pathlib import Path

import httpx

from ..slicing import PackItem

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT_S = 630.0  # seconds — slightly over sidecar's 600 s slice timeout
DEFAULT_POLL_TIMEOUT_S = 620.0
PROFILES_PAGE_SIZE = 200  # v1 max page size


def _detail(r: httpx.Response, limit: int = 300) -> str:
    """Laminus v1 errors are {"error": {"code", "message", "status"}, "request_id"}; fall back to the raw body."""
    try:
        err = r.json()["error"]
        return f"{err['code']}: {err['message']}"[:limit]
    except (ValueError, KeyError, TypeError):
        return r.text[:limit]


class SidecarError(Exception):
    pass


class SidecarNotReady(SidecarError):
    """The sidecar answered /api/v1/health with a non-200 (still starting or building its catalog)."""


class LaminusSidecarClient:
    """Synchronous httpx client for the Laminus sidecar API.

    Synchronous because SlicerService runs inside a ThreadPoolExecutor — callers
    must wrap with asyncio.to_thread if calling from an async context.
    """

    def __init__(self, base_url: str, timeout: float = DEFAULT_REQUEST_TIMEOUT_S) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
        )

    def health(self) -> dict:
        """GET /api/v1/health — raises SidecarError on non-200."""
        try:
            r = self._client.get("/api/v1/health")
        except httpx.HTTPError as e:
            raise SidecarError(f"health check request failed: {e}") from e
        if r.status_code != 200:
            raise SidecarNotReady(f"health check returned {r.status_code}")
        return r.json()

    def catalog_state(self, timeout: float = 5.0) -> dict:
        """GET /api/v1/health as catalog readiness: the body on 200, a "building" marker on 503
        (the sidecar answers 503 while it builds its catalog), SidecarError otherwise."""
        try:
            r = self._client.get("/api/v1/health", timeout=timeout)
        except httpx.HTTPError as e:
            raise SidecarError(f"health check request failed: {e}") from e
        if r.status_code == 200:
            return r.json()
        if r.status_code == 503:
            return {"catalog_loaded": False, "catalog_building": True}
        raise SidecarError(f"health check returned {r.status_code}")

    def request_catalog_rebuild(self, timeout: float = 10.0) -> None:
        """POST /api/v1/profiles/rescan — kicks off a background catalog rebuild (200 whether it started
        or one was already running)."""
        try:
            r = self._client.post("/api/v1/profiles/rescan", timeout=timeout)
        except httpx.HTTPError as e:
            raise SidecarError(f"Could not reach Laminus sidecar: {e}") from e
        if r.status_code != 200:
            raise SidecarError(f"Laminus rescan trigger returned {r.status_code}: {_detail(r)}")

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
        """POST /api/v1/jobs → job id string.

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
                    "/api/v1/jobs",
                    files={"file": (source_file.name, fh, "application/octet-stream")},
                    data=data,
                )
        except httpx.HTTPError as e:
            raise SidecarError(f"jobs request failed: {e}") from e

        if r.status_code != 202:
            raise SidecarError(f"jobs returned {r.status_code}: {_detail(r)}")
        return r.json()["id"]

    def slice_prepared(
        self,
        prepared_3mf: Path,
        plate: int,
        export_3mf: bool = False,
        geometry_only_retry: bool = False,
    ) -> str:
        """POST /api/v1/slice/prepared → job_id string."""
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
                    "/api/v1/slice/prepared",
                    files={"file": (prepared_3mf.name, fh, "application/octet-stream")},
                    data=data,
                )
        except httpx.HTTPError as e:
            raise SidecarError(f"slice/prepared request failed: {e}") from e

        if r.status_code != 200:
            raise SidecarError(
                f"slice/prepared returned {r.status_code}: {_detail(r)}"
            )
        return r.json()["job_id"]

    def poll_status(
        self,
        job_id: str,
        poll_interval: float = 2.0,
        timeout: float = DEFAULT_POLL_TIMEOUT_S,
    ) -> dict:
        """Poll GET /api/v1/jobs/{job_id} until completed or failed.

        Returns the completed job view (the output name is ``artifact["name"]``). Raises SidecarError on
        failure or timeout.
        """
        deadline = time.monotonic() + timeout
        while True:
            try:
                r = self._client.get(f"/api/v1/jobs/{job_id}")
            except httpx.HTTPError as e:
                raise SidecarError(f"status poll request failed: {e}") from e

            if r.status_code != 200:
                raise SidecarError(f"job status returned {r.status_code}: {_detail(r, 200)}")

            body = r.json()
            status = body.get("status")

            if status == "completed":
                if not (body.get("artifact") or {}).get("name"):
                    raise SidecarError(f"sidecar job {job_id} completed without an artifact")
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
        """GET /api/v1/jobs/{job_id}/artifact → write bytes to dest, return dest.

        Note: downloading evicts the job on the sidecar side.
        """
        try:
            r = self._client.get(f"/api/v1/jobs/{job_id}/artifact")
        except httpx.HTTPError as e:
            raise SidecarError(f"download request failed: {e}") from e

        if r.status_code != 200:
            raise SidecarError(f"download returned {r.status_code}: {_detail(r, 200)}")

        dest.write_bytes(r.content)
        return dest

    def get_catalog(self) -> dict:
        """GET /api/v1/profiles (cursor-paginated flat list) → catalog dict grouped by ``type``:
        {"machine": [...], "process": [...], "filament": [...]}."""
        catalog: dict[str, list] = {"machine": [], "process": [], "filament": []}
        cursor: str | None = None
        while True:
            params: dict[str, str | int] = {"limit": PROFILES_PAGE_SIZE}
            if cursor:
                params["cursor"] = cursor
            try:
                r = self._client.get("/api/v1/profiles", params=params, timeout=30)
            except httpx.HTTPError as e:
                raise SidecarError(f"profiles request failed: {e}") from e
            if r.status_code != 200:
                raise SidecarError(f"profiles returned {r.status_code}: {_detail(r, 200)}")
            body = r.json()
            for item in body["data"]:
                catalog.setdefault(item["type"], []).append(item)
            cursor = body["page"]["next_cursor"]
            if not cursor:
                return catalog

    def _post_json(self, path: str, payload: dict, what: str, timeout: float = 15) -> dict:
        try:
            r = self._client.post(path, json=payload, timeout=timeout)
        except httpx.HTTPError as e:
            raise SidecarError(f"{what} request failed: {e}") from e
        if r.status_code != 200:
            raise SidecarError(f"{what} returned {r.status_code}: {_detail(r, 200)}")
        return r.json()

    def suggest_machines(self, manufacturer: str, model: str, model_key: str | None = None) -> list[dict]:
        """POST /api/v1/profiles/machines/suggest -> candidates, best first (empty list when nothing matches)."""
        body = self._post_json("/api/v1/profiles/machines/suggest",
                               {"manufacturer": manufacturer, "model": model, "model_key": model_key}, "profiles/machines/suggest")
        return list(body.get("candidates") or [])

    def model_variants(self, manufacturer: str, model: str, model_key: str | None = None) -> list[dict]:
        """POST /api/v1/profiles/models/variants -> the model's nozzle variants (empty list for an unknown model)."""
        body = self._post_json("/api/v1/profiles/models/variants",
                               {"manufacturer": manufacturer, "model": model, "model_key": model_key}, "profiles/models/variants")
        return list(body.get("variants") or [])

    def get_merged_config(
        self,
        machine_uuid: str,
        process_uuid: str,
        filament_uuids: list[str],
    ) -> dict:
        """POST /api/v1/profiles/merged-config → merged project_settings dict.

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
            r = self._client.post("/api/v1/profiles/merged-config", json=payload, timeout=30)
        except httpx.HTTPError as e:
            raise SidecarError(f"profiles/merged-config request failed: {e}") from e
        if r.status_code != 200:
            raise SidecarError(f"profiles/merged-config returned {r.status_code}: {_detail(r)}")
        return r.json()

    def arrange(
        self,
        threemf_path: Path,
        arrange: bool = True,
        orient: bool = True,
        timeout: float = 130.0,
    ) -> bytes:
        """POST /api/v1/arrange → arranged multi-plate 3MF bytes.

        Sends a pre-built 3MF (with model_settings.config extruder assignments) to
        the sidecar for plate arrangement and returns the rearranged 3MF bytes.
        Raises SidecarError on failure or timeout.
        """
        try:
            with open(threemf_path, "rb") as fh:
                r = self._client.post(
                    "/api/v1/arrange",
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
            raise SidecarError(f"arrange returned {r.status_code}: {_detail(r)}")
        return r.content

    def pack_stls(self, items: list[PackItem], bed_x: float, bed_y: float, bed_z: float = 250.0) -> bytes:
        """POST /api/v1/pack → multi-plate arranged 3MF bytes."""
        return self._pack(items, {"bed_x": str(bed_x), "bed_y": str(bed_y), "bed_z": str(bed_z)})

    def pack_stls_by_uuid(
        self,
        items: list[PackItem],
        machine_uuid: str,
        process_uuid: str,
        filament_uuids: list[str],
    ) -> bytes:
        """POST /api/v1/pack (UUID mode) → multi-plate 3MF bytes with embedded settings.

        The sidecar resolves bed dimensions and profile settings from the given
        UUIDs, so no bed_x/bed_y is needed.
        """
        return self._pack(items, {
            "machine_uuid": machine_uuid,
            "process_uuid": process_uuid,
            "filament_uuids": json.dumps(filament_uuids),
        })

    def _pack(self, items: list[PackItem], data: dict[str, str]) -> bytes:
        """Upload each item's STL once and ask for `quantity` copies via `items`. The sidecar keys quantities by upload
        filename and needs them unique, so the item's `ref` prefixes the name (same-named models stay distinct)."""
        names = [f"{it.ref}_{it.path.name}" for it in items]
        data = {**data, "items": json.dumps([{"file": n, "quantity": it.quantity} for n, it in zip(names, items)])}
        handles = [open(it.path, "rb") for it in items]
        try:
            files = [("files", (n, fh, "application/octet-stream")) for n, fh in zip(names, handles)]
            try:
                r = self._client.post("/api/v1/pack", files=files, data=data)
            except httpx.HTTPError as e:
                raise SidecarError(f"pack request failed: {e}") from e
        finally:
            for fh in handles:
                fh.close()

        if r.status_code != 200:
            raise SidecarError(f"pack returned {r.status_code}: {_detail(r)}")
        return r.content
