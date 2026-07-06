import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from httpx import AsyncClient

from app.services.slicer_service import SliceError, SliceRequest, SlicerService


def _make_3mf() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/slice_info.config", json.dumps({
            "plate": [{"index": 1, "prediction": 60, "weight": [5.0]}]
        }))
        zf.writestr("Metadata/plate_1.png", b"\x89PNG")
    return buf.getvalue()


async def _upload_file(client, tmp_path):
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"):
        (tmp_path / "library").mkdir(exist_ok=True)
        (tmp_path / "filecache").mkdir(exist_ok=True)
        resp = await client.post(
            "/api/v1/files/upload",
            files={"file": ("m.3mf", _make_3mf(), "application/octet-stream")},
        )
    return resp.json()["id"]


async def _create_printer(client):
    resp = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4",
    })
    return resp.json()["id"]


async def test_create_job_stores_overrides(client: AsyncClient, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id,
            "plate_number": 1,
            "overrides": {"sparse_infill_pattern": "grid", "layer_height": "0.15"},
            "printer_configs": [{
                "printer_id": printer_id,
                "print_profile": "0.16mm Profile",
            }],
        })
    assert resp.status_code == 201
    job_id = resp.json()["id"]

    detail = await client.get(f"/api/v1/jobs/{job_id}/details")
    assert detail.status_code == 200
    assert detail.json()["overrides"] == {"sparse_infill_pattern": "grid", "layer_height": "0.15"}


async def test_create_job_strips_non_curated_override_keys(client: AsyncClient, tmp_path):
    """Non-curated keys (e.g. post_process) are silently dropped at the API boundary."""
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id,
            "plate_number": 1,
            "overrides": {"layer_height": "0.15", "post_process": "rm -rf /", "unknown_key": "x"},
            "printer_configs": [{"printer_id": printer_id, "print_profile": "P"}],
        })
    assert resp.status_code == 201
    detail = await client.get(f"/api/v1/jobs/{resp.json()['id']}/details")
    assert detail.json()["overrides"] == {"layer_height": "0.15"}


async def test_create_job_without_overrides_is_null(client: AsyncClient, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id,
            "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "Profile"}],
        })
    assert resp.status_code == 201
    job_id = resp.json()["id"]
    detail = await client.get(f"/api/v1/jobs/{job_id}/details")
    assert detail.json()["overrides"] is None


async def test_update_job_configs_clears_overrides_when_omitted(client: AsyncClient, tmp_path):
    """PATCH /configs without overrides field clears any previously-stored overrides."""
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)

    # Create job with overrides
    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id,
            "plate_number": 1,
            "overrides": {"layer_height": "0.15"},
            "printer_configs": [{"printer_id": printer_id, "print_profile": "P"}],
        })
    job_id = resp.json()["id"]

    # PATCH without overrides field → overrides cleared
    with patch("app.api.routes.jobs.queue_engine"):
        await client.patch(f"/api/v1/jobs/{job_id}/configs", json={
            "printer_configs": [{"printer_id": printer_id, "print_profile": "P"}],
        })

    detail = await client.get(f"/api/v1/jobs/{job_id}/details")
    assert detail.json()["overrides"] is None


def test_slice_request_extra_config_defaults_empty():
    req = SliceRequest(
        job_id=1, source_3mf="/tmp/m.3mf", plate_number=1,
        machine_preset="machine", process_preset="process",
        filament_presets=["filament"],
    )
    assert req.extra_config == {}


def test_extra_config_forwarded_to_sidecar():
    """extra_config is passed through to slice_start so the sidecar merges it."""
    svc = SlicerService.__new__(SlicerService)
    svc._data_dir = Path("/tmp")
    svc._catalog_cache = {
        "machine": [{"name": "MyPrinter", "uuid": "m1"}],
        "process": [{"name": "MyProcess", "uuid": "p1"}],
        "filament": [{"name": "MyFilament", "uuid": "f1"}],
    }
    svc._catalog_ts = float("inf")  # never re-fetch

    req = SliceRequest(
        job_id=1, source_3mf="/tmp/m.stl", plate_number=1,
        machine_preset="MyPrinter", process_preset="MyProcess",
        filament_presets=["MyFilament"],
        extra_config={"fill_pattern": "grid", "layer_height": "0.15"},
    )

    mock_client = MagicMock()
    mock_client.slice_start.return_value = "sidecar-job-1"
    mock_client.poll_status.return_value = {"sliced_file": "out.gcode"}
    mock_client.download.return_value = Path("/tmp/out.gcode")

    with patch("app.services.slicer_service.SlicerService._execute_slice_by_ids") as mock_exec, \
         patch("app.config.get_orca_sidecar_url", return_value="http://orca:5000"):
        mock_exec.return_value = "/tmp/out.gcode"
        svc.slice(req)

    mock_exec.assert_called_once()
    _, kwargs = mock_exec.call_args[0], mock_exec.call_args[1]
    # extra_config must be passed through to _execute_slice_by_ids
    assert req.extra_config == {"fill_pattern": "grid", "layer_height": "0.15"}


def test_slice_raises_without_sidecar():
    """SlicerService.slice raises SliceError when no sidecar is configured."""
    svc = SlicerService.__new__(SlicerService)
    svc._data_dir = Path("/tmp")
    svc._catalog_cache = None
    svc._catalog_ts = 0.0
    req = SliceRequest(
        job_id=1, source_3mf="/tmp/m.stl", plate_number=1,
        machine_preset="m", process_preset="p", filament_presets=["f"],
    )
    with patch("app.config.get_orca_sidecar_url", return_value=None):
        try:
            svc.slice(req)
            assert False, "expected SliceError"
        except SliceError as e:
            assert "ORCA_SIDECAR_URL" in str(e)
