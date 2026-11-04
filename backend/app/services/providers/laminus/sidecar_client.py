from __future__ import annotations

import json
import re
import time
import logging
from pathlib import Path
from typing import Callable

import httpx

from ..contract import ConstructInput

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT_S = 630.0  # seconds — slightly over sidecar's 600 s slice timeout
DEFAULT_POLL_TIMEOUT_S = 620.0
PROFILES_PAGE_SIZE = 200  # v1 max page size
# Characters that break a pack upload name: `;` is rejected by the sidecar; `"` and control characters are percent-encoded by httpx in
# the multipart header and the sidecar doesn't decode them, so the name would no longer match its `items` entry.
_UNSAFE_UPLOAD_CHARS = re.compile(r'[";\x00-\x1f\x7f]')


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


class SidecarNothingFits(SidecarError):
    """POST /api/v1/pack/next answered 422 `nothing_fits` (BIZ-362)."""


class SidecarTimeout(SidecarError):
    """The request (or the sidecar's own work) ran out of time."""


class SidecarUnreachable(SidecarError):
    """No answer: connection refused/reset or another transport failure."""


class SidecarCancelled(SidecarError):
    """The caller asked the wait to stop (`should_stop` returned True)."""


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
        should_stop: "Callable[[], bool] | None" = None,
    ) -> dict:
        """Poll GET /api/v1/jobs/{job_id} until completed or failed.

        Returns the completed job view (the output name is ``artifact["name"]``). Raises SidecarError on
        failure, SidecarTimeout past `timeout`, SidecarCancelled as soon as `should_stop()` is true (checked before each poll).
        """
        deadline = time.monotonic() + timeout
        while True:
            if should_stop is not None and should_stop():
                raise SidecarCancelled(f"slice job {job_id} cancelled")
            try:
                r = self._client.get(f"/api/v1/jobs/{job_id}")
            except httpx.TimeoutException as e:
                raise SidecarTimeout(f"status poll timed out: {e}") from e
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
                raise SidecarTimeout(
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

    def machine_presets(self, machine_ref: str, kind: str) -> list[dict]:
        """GET /api/v1/profiles/machines/{ref}/presets?kind= -> the process/filament presets compatible with that machine
        (`[{ref, uuid, name, kind, compatible_models?}]`). 404 = unknown machine ref (a bare `{"error": ...}` body)."""
        try:
            r = self._client.get(f"/api/v1/profiles/machines/{machine_ref}/presets", params={"kind": kind}, timeout=15)
        except httpx.HTTPError as e:
            raise SidecarError(f"machine presets request failed: {e}") from e
        if r.status_code != 200:
            raise SidecarError(f"machine presets returned {r.status_code}: {r.text[:200]}")
        return list(r.json().get("presets") or [])

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

    def pack_stls(self, items: list[ConstructInput], bed_x: float, bed_y: float, bed_z: float = 250.0) -> bytes:
        """POST /api/v1/pack by bed size -> multi-plate arranged 3MF bytes. STL uploads only (the sidecar rejects others)."""
        return self._pack(items, {"bed_x": str(bed_x), "bed_y": str(bed_y), "bed_z": str(bed_z)})

    def pack_mixed(self, items: list[ConstructInput], bed_x: float, bed_y: float, bed_z: float = 250.0) -> bytes:
        """POST /api/v1/pack/mixed by bed size (BIZ-356): STLs, 3MF objects and 3MF plate bundles with split/together/solo rules
        -> one multi-plate 3MF with the plate manifest. Each source file is uploaded once; the selection is in `items` order."""
        uploads: dict[str, tuple[str, ConstructInput]] = {}      # one upload per source file, named uniquely
        selection = []
        for it in items:
            key = str(it.path)
            if key not in uploads:
                uploads[key] = (_UNSAFE_UPLOAD_CHARS.sub("_", f"{len(uploads)}_{it.path.name}"), it)
            entry: dict = {"file": uploads[key][0], "qty": it.quantity}
            if it.object_id is not None:
                entry["id"] = int(it.object_id) if str(it.object_id).isdigit() else it.object_id
            if it.plate is not None:
                entry["plate"] = it.plate
            if it.rule != "split":
                entry["rule"] = it.rule
            selection.append(entry)
        data = {"bed_x": str(bed_x), "bed_y": str(bed_y), "bed_z": str(bed_z), "selection": json.dumps(selection)}
        return self._post_files("/api/v1/pack/mixed", [(n, it.path) for n, it in uploads.values()], data, "pack/mixed")

    def pack_next(self, items: list[ConstructInput], machine_uuid: str, max_parts: int | None = None) -> tuple[bytes, list[int]]:
        """POST /api/v1/pack/next (BIZ-362): the fullest single plate for one machine -> (3MF bytes, consumed per selection entry).
        422 `nothing_fits` raises SidecarNothingFits."""
        uploads: dict[str, tuple[str, ConstructInput]] = {}
        selection = []
        for it in items:
            key = str(it.path)
            if key not in uploads:
                uploads[key] = (_UNSAFE_UPLOAD_CHARS.sub("_", f"{len(uploads)}_{it.path.name}"), it)
            entry: dict = {"file": uploads[key][0], "qty": it.quantity}
            if it.object_id is not None:
                entry["id"] = int(it.object_id) if str(it.object_id).isdigit() else it.object_id
            if it.plate is not None:
                entry["plate"] = it.plate
            if it.rule != "split":
                entry["rule"] = it.rule
            selection.append(entry)
        data = {"machine_uuid": machine_uuid, "selection": json.dumps(selection),
                **({"max_parts": str(max_parts)} if max_parts else {})}
        handles = [open(it.path, "rb") for _, it in uploads.values()]
        try:
            files = [("files", (n, fh, "application/octet-stream")) for (n, _), fh in zip(uploads.values(), handles)]
            try:
                r = self._client.post("/api/v1/pack/next", files=files, data=data)
            except httpx.TimeoutException as e:
                raise SidecarTimeout(f"pack/next timed out: {e}") from e
            except httpx.HTTPError as e:
                raise SidecarUnreachable(f"pack/next request failed: {e}") from e
        finally:
            for fh in handles:
                fh.close()
        if r.status_code == 422:
            try:
                code = r.json().get("error", {}).get("code")
            except ValueError:
                code = None
            if code == "nothing_fits":
                raise SidecarNothingFits(_detail(r))
        if r.status_code == 503:
            raise SidecarNotReady(f"pack/next returned 503: {_detail(r)}")
        if r.status_code != 200:
            raise SidecarError(f"pack/next returned {r.status_code}: {_detail(r)}")
        consumed = json.loads(r.headers.get("X-Consumed") or "[]")
        return r.content, [int(n) for n in consumed]

    def inspect_objects(self, model_3mf) -> list[dict]:
        """POST /api/v1/projects/inspect -> the 3MF's objects (id, name, instances, ...)."""
        from pathlib import Path as _P
        path = _P(model_3mf)
        with open(path, "rb") as fh:
            try:
                r = self._client.post("/api/v1/projects/inspect", files=[("file", (path.name, fh, "application/octet-stream"))])
            except httpx.HTTPError as e:
                raise SidecarUnreachable(f"inspect request failed: {e}") from e
        if r.status_code == 503:
            raise SidecarNotReady(f"inspect returned 503: {_detail(r)}")
        if r.status_code != 200:
            raise SidecarError(f"inspect returned {r.status_code}: {_detail(r)}")
        return list(r.json().get("objects", []))

    def _post_files(self, url: str, named_paths: list, data: dict[str, str], what: str) -> bytes:
        handles = [open(p, "rb") for _, p in named_paths]
        try:
            files = [("files", (n, fh, "application/octet-stream")) for (n, _), fh in zip(named_paths, handles)]
            try:
                r = self._client.post(url, files=files, data=data)
            except httpx.TimeoutException as e:
                raise SidecarTimeout(f"{what} timed out: {e}") from e
            except httpx.HTTPError as e:
                raise SidecarUnreachable(f"{what} request failed: {e}") from e
        finally:
            for fh in handles:
                fh.close()
        if r.status_code == 408:
            raise SidecarTimeout(f"{what} timed out on the sidecar")
        if r.status_code == 503:
            raise SidecarNotReady(f"{what} returned 503: {_detail(r)}")
        if r.status_code != 200:
            raise SidecarError(f"{what} returned {r.status_code}: {_detail(r)}")
        return r.content

    def _pack(self, items: list[ConstructInput], data: dict[str, str]) -> bytes:
        """Upload each item's STL once and ask for `quantity` copies via `items`. The sidecar keys quantities by upload
        filename and needs them unique, so the item's `ref` prefixes the name (same-named models stay distinct)."""
        names = [_UNSAFE_UPLOAD_CHARS.sub("_", f"{it.ref}_{it.path.name}") for it in items]
        data = {**data, "items": json.dumps([{"file": n, "quantity": it.quantity} for n, it in zip(names, items)])}
        handles = [open(it.path, "rb") for it in items]
        try:
            files = [("files", (n, fh, "application/octet-stream")) for n, fh in zip(names, handles)]
            try:
                r = self._client.post("/api/v1/pack", files=files, data=data)
            except httpx.TimeoutException as e:
                raise SidecarTimeout(f"pack timed out: {e}") from e
            except httpx.HTTPError as e:
                raise SidecarUnreachable(f"pack request failed: {e}") from e
        finally:
            for fh in handles:
                fh.close()

        if r.status_code == 408:
            raise SidecarTimeout("pack timed out on the sidecar")
        if r.status_code == 503:
            raise SidecarNotReady(f"pack returned 503: {_detail(r)}")
        if r.status_code != 200:
            raise SidecarError(f"pack returned {r.status_code}: {_detail(r)}")
        return r.content
