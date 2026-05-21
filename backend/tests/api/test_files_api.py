import io
import json
import zipfile
import pytest
from pathlib import Path
from unittest.mock import patch
from app.services.three_mf_parser import PlateInfo


def _make_three_mf_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        slice_info = json.dumps({"plate": [
            {"index": 1, "prediction": 3600, "weight": [42.1]},
            {"index": 2, "prediction": 1800, "weight": [21.5]},
        ]})
        zf.writestr("Metadata/slice_info.config", slice_info)
        zf.writestr("Metadata/plate_1.png", b"\x89PNG\r\n\x1a\n")
        zf.writestr("Metadata/plate_2.png", b"\x89PNG\r\n\x1a\n")
    return buf.getvalue()


async def test_upload_three_mf(client, tmp_path):
    with patch("app.api.routes.files.get_data_dir", return_value=tmp_path):
        response = await client.post(
            "/api/v1/files/upload",
            files={"file": ("model.3mf", _make_three_mf_bytes(), "application/octet-stream")},
        )
    assert response.status_code == 201
    data = response.json()
    assert data["id"] is not None
    assert data["original_filename"] == "model.3mf"
    assert len(data["plates"]) == 2
    assert data["plates"][0]["plate_number"] == 1
    assert data["plates"][0]["estimated_time"] == 3600


async def test_upload_rejects_non_3mf(client, tmp_path):
    with patch("app.api.routes.files.get_data_dir", return_value=tmp_path):
        response = await client.post(
            "/api/v1/files/upload",
            files={"file": ("model.stl", b"solid model\nendsolid", "application/octet-stream")},
        )
    assert response.status_code == 422


async def test_get_plates(client, tmp_path):
    with patch("app.api.routes.files.get_data_dir", return_value=tmp_path):
        upload = await client.post(
            "/api/v1/files/upload",
            files={"file": ("model.3mf", _make_three_mf_bytes(), "application/octet-stream")},
        )
    file_id = upload.json()["id"]
    with patch("app.api.routes.files.get_data_dir", return_value=tmp_path):
        response = await client.get(f"/api/v1/files/{file_id}/plates")
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_get_plates_not_found(client):
    response = await client.get("/api/v1/files/9999/plates")
    assert response.status_code == 404


async def test_thumbnail_served(client, tmp_path):
    with patch("app.api.routes.files.get_data_dir", return_value=tmp_path):
        upload = await client.post(
            "/api/v1/files/upload",
            files={"file": ("model.3mf", _make_three_mf_bytes(), "application/octet-stream")},
        )
    file_id = upload.json()["id"]
    with patch("app.api.routes.files.get_data_dir", return_value=tmp_path):
        response = await client.get(f"/api/v1/files/{file_id}/thumbnails/plate_1.png")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
