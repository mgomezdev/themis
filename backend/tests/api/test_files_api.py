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


def _patch_dirs(tmp_path):
    (tmp_path / "library").mkdir(exist_ok=True)
    (tmp_path / "filecache").mkdir(exist_ok=True)
    return (
        patch("app.config.get_library_dir", return_value=tmp_path / "library"),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
    )


async def test_upload_three_mf(client, tmp_path):
    lib, cache = _patch_dirs(tmp_path)
    with lib, cache:
        response = await client.post(
            "/api/v1/files/upload",
            files={"file": ("model.3mf", _make_three_mf_bytes(), "application/octet-stream")},
        )
    assert response.status_code == 201
    data = response.json()
    assert data["id"] is not None
    assert data["original_filename"] == "model.3mf"
    assert data["folder"] == "/Job Uploads"
    assert data["plate_count"] == 2
    assert (tmp_path / "library" / "Job Uploads" / "model.3mf").is_file()


async def test_upload_rejects_unsupported_type(client, tmp_path):
    lib, cache = _patch_dirs(tmp_path)
    with lib, cache:
        response = await client.post(
            "/api/v1/files/upload",
            files={"file": ("model.obj", b"# obj file", "application/octet-stream")},
        )
    assert response.status_code == 422


async def test_upload_stl_returns_single_plate(client, tmp_path):
    lib, cache = _patch_dirs(tmp_path)
    with lib, cache:
        response = await client.post(
            "/api/v1/files/upload",
            files={"file": ("model.stl", b"solid model\nendsolid", "application/octet-stream")},
        )
    assert response.status_code == 201
    data = response.json()
    assert data["original_filename"] == "model.stl"
    assert data["plate_count"] == 1


async def test_get_plates(client, tmp_path):
    lib, cache = _patch_dirs(tmp_path)
    with lib, cache:
        upload = await client.post(
            "/api/v1/files/upload",
            files={"file": ("model.3mf", _make_three_mf_bytes(), "application/octet-stream")},
        )
        file_id = upload.json()["id"]
        response = await client.get(f"/api/v1/files/{file_id}/plates")
    assert response.status_code == 200
    plates = response.json()
    assert len(plates) == 2
    assert plates[0]["plate_number"] == 1
    assert plates[0]["estimated_time"] == 3600


async def test_get_plates_not_found(client):
    response = await client.get("/api/v1/files/9999/plates")
    assert response.status_code == 404


async def test_thumbnail_served(client, tmp_path):
    lib, cache = _patch_dirs(tmp_path)
    with lib, cache:
        upload = await client.post(
            "/api/v1/files/upload",
            files={"file": ("model.3mf", _make_three_mf_bytes(), "application/octet-stream")},
        )
        file_id = upload.json()["id"]
        response = await client.get(f"/api/v1/files/{file_id}/thumbnails/plate_1.png")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")


import hashlib


def _make_stl_bytes(seed: bytes = b"stl") -> bytes:
    """Minimal binary STL — 84 bytes (header + triangle count = 0)."""
    return seed.ljust(80, b" ") + b"\x00\x00\x00\x00"


async def test_upload_dedup_same_folder_returns_existing(client, tmp_path):
    """Uploading the same bytes to the same folder twice returns the existing record."""
    lib, cache = _patch_dirs(tmp_path)
    stl = _make_stl_bytes()
    with lib, cache:
        r1 = await client.post(
            "/api/v1/files/upload",
            data={"folder": "/Gridfinity/layout-a"},
            files={"file": ("bin_2x3.stl", stl, "application/octet-stream")},
        )
        r2 = await client.post(
            "/api/v1/files/upload",
            data={"folder": "/Gridfinity/layout-a"},
            files={"file": ("bin_2x3.stl", stl, "application/octet-stream")},
        )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    # Only one file written to disk
    stl_files = list((tmp_path / "library" / "Gridfinity" / "layout-a").glob("*.stl"))
    assert len(stl_files) == 1


async def test_upload_dedup_different_folder_creates_separate_record(client, tmp_path):
    """Same bytes in a different folder get a new record — no cross-folder dedup."""
    lib, cache = _patch_dirs(tmp_path)
    stl = _make_stl_bytes()
    with lib, cache:
        r1 = await client.post(
            "/api/v1/files/upload",
            data={"folder": "/Gridfinity/layout-a"},
            files={"file": ("bin_2x3.stl", stl, "application/octet-stream")},
        )
        r2 = await client.post(
            "/api/v1/files/upload",
            data={"folder": "/Gridfinity/layout-b"},
            files={"file": ("bin_2x3.stl", stl, "application/octet-stream")},
        )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]
