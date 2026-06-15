import io
import json
import zipfile
from unittest.mock import patch

import pytest
from httpx import AsyncClient


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
            "overrides": {"fill_pattern": "grid", "layer_height": "0.15"},
            "printer_configs": [{
                "printer_id": printer_id,
                "print_profile": "0.16mm Profile",
            }],
        })
    assert resp.status_code == 201
    job_id = resp.json()["id"]

    detail = await client.get(f"/api/v1/jobs/{job_id}/details")
    assert detail.status_code == 200
    assert detail.json()["overrides"] == {"fill_pattern": "grid", "layer_height": "0.15"}


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
