# backend/tests/api/test_queue_api.py
import json
import io
import zipfile
import pytest
from unittest.mock import patch


def _make_3mf() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/slice_info.config", json.dumps({
            "plate": [{"index": 1, "prediction": 60, "weight": [5.0]}]
        }))
        zf.writestr("Metadata/plate_1.png", b"\x89PNG")
    return buf.getvalue()


async def _create_job(client, tmp_path) -> int:
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"):
        (tmp_path / "library").mkdir(exist_ok=True)
        (tmp_path / "filecache").mkdir(exist_ok=True)
        upload = await client.post(
            "/api/v1/files/upload",
            files={"file": ("m.3mf", _make_3mf(), "application/octet-stream")},
        )
    file_id = upload.json()["id"]
    printer = await client.post("/api/v1/printers", json={
        "name": "P", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": [], "current_orca_printer_profile": None,
    })
    printer_id = printer.json()["id"]
    with patch("app.api.routes.jobs.queue_engine"):
        create = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id,
            "plate_number": 1,
            "printer_configs": [
                {"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA"}
            ],
        })
    return create.json()["id"]


async def test_queue_empty(client):
    response = await client.get("/api/v1/queue")
    assert response.status_code == 200
    assert response.json() == []


async def test_queue_shows_active_jobs(client, tmp_path):
    job_id = await _create_job(client, tmp_path)
    response = await client.get("/api/v1/queue")
    assert response.status_code == 200
    ids = [j["id"] for j in response.json()]
    assert job_id in ids


async def test_queue_reorder(client, tmp_path):
    job1 = await _create_job(client, tmp_path)
    job2 = await _create_job(client, tmp_path)
    response = await client.patch("/api/v1/queue/reorder", json={
        "positions": [{"job_id": job1, "queue_position": 5.0}, {"job_id": job2, "queue_position": 3.0}]
    })
    assert response.status_code == 200
    queue = await client.get("/api/v1/queue")
    ordered_ids = [j["id"] for j in queue.json()]
    assert ordered_ids.index(job2) < ordered_ids.index(job1)


async def test_queue_reorder_unknown_job(client):
    response = await client.patch("/api/v1/queue/reorder", json={
        "positions": [{"job_id": 9999, "queue_position": 1.0}]
    })
    assert response.status_code == 404
