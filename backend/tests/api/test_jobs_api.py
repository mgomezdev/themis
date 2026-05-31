# backend/tests/api/test_jobs_api.py
import json
import io
import zipfile
import pytest
from unittest.mock import patch
from app.models import Job


def _make_3mf() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/slice_info.config", json.dumps({
            "plate": [{"index": 1, "prediction": 60, "weight": [5.0]}]
        }))
        zf.writestr("Metadata/plate_1.png", b"\x89PNG")
    return buf.getvalue()


async def _upload_file(client, tmp_path):
    with patch("app.api.routes.files.get_data_dir", return_value=tmp_path):
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


async def test_create_job(client, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    payload = {
        "uploaded_file_id": file_id,
        "plate_number": 1,
        "order_id": None,
        "printer_configs": [
            {"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA"}
        ],
    }
    with patch("app.api.routes.jobs.queue_engine") as mock_qe:
        response = await client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "queued"
    assert data["id"] is not None
    assert data["order_id"] is None
    mock_qe.wake.assert_called_once()


async def test_create_job_invalid_file(client):
    response = await client.post("/api/v1/jobs", json={
        "uploaded_file_id": 9999, "plate_number": 1, "printer_configs": [],
    })
    assert response.status_code == 404


async def test_list_jobs_empty(client):
    response = await client.get("/api/v1/jobs")
    assert response.status_code == 200
    assert response.json() == []


async def test_get_job(client, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    with patch("app.api.routes.jobs.queue_engine"):
        create = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id,
            "plate_number": 1,
            "printer_configs": [
                {"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA"}
            ],
        })
    job_id = create.json()["id"]
    response = await client.get(f"/api/v1/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["id"] == job_id


async def test_cancel_job(client, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    with patch("app.api.routes.jobs.queue_engine"):
        create = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id,
            "plate_number": 1,
            "printer_configs": [
                {"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA"}
            ],
        })
    job_id = create.json()["id"]
    response = await client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


async def test_cancel_complete_job_fails(client, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    with patch("app.api.routes.jobs.queue_engine"):
        create = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id,
            "plate_number": 1,
            "printer_configs": [
                {"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA"}
            ],
        })
    job_id = create.json()["id"]
    await client.post(f"/api/v1/jobs/{job_id}/cancel")   # transitions to "cancelled"
    response = await client.post(f"/api/v1/jobs/{job_id}/cancel")  # "cancelled" not in _CANCELLABLE_STATUSES
    assert response.status_code == 422


async def test_get_slice_failures(client, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    with patch("app.api.routes.jobs.queue_engine"):
        create = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id,
            "plate_number": 1,
            "printer_configs": [
                {"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA"}
            ],
        })
    job_id = create.json()["id"]
    response = await client.get(f"/api/v1/jobs/{job_id}/slice-failures")
    assert response.status_code == 200
    assert response.json() == []
