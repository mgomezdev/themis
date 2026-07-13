import io
import zipfile
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def _make_stl_bytes() -> bytes:
    return b"solid part\nendsolid"


def _make_3mf_bytes(plate_count: int = 1) -> bytes:
    """Minimal .3mf (zip) with plate thumbnails so _parse_plate_nums returns correct IDs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(1, plate_count + 1):
            zf.writestr(f"Metadata/plate_{i}.png", b"\x89PNG\r\n\x1a\n")
    return buf.getvalue()


async def _setup_project_with_stl(client, tmp_path) -> tuple[int, int]:
    """Create a project, upload an STL, add it as a project item. Returns (project_id, file_id)."""
    lib = tmp_path / "library"
    lib.mkdir(exist_ok=True)
    (tmp_path / "filecache").mkdir(exist_ok=True)

    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
    ):
        proj_resp = await client.post("/api/v1/projects", json={"name": "Test Pack"})
        assert proj_resp.status_code == 201
        project_id = proj_resp.json()["id"]

        upload_resp = await client.post(
            "/api/v1/files/upload",
            files={"file": ("part.stl", _make_stl_bytes(), "application/octet-stream")},
        )
        assert upload_resp.status_code == 201
        file_id = upload_resp.json()["id"]

        item_resp = await client.post(
            f"/api/v1/projects/{project_id}/items",
            json={"file_id": file_id, "quantity": 1},
        )
        assert item_resp.status_code == 201

    return project_id, file_id


async def test_create_project_without_machine_process(client):
    """Projects can be created without machine_uuid / process_uuid for external importers."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Ordinus Import"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Ordinus Import"
    assert data["source_app"] is None


async def test_create_project_with_source_fields(client):
    """Source fields are stored and returned on the project."""
    resp = await client.post(
        "/api/v1/projects",
        json={
            "name": "Ordinus Import",
            "source_app": "ordinus",
            "source_user": "alice",
            "source_layout_id": 42,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["source_app"] == "ordinus"
    assert data["source_user"] == "alice"
    assert data["source_layout_id"] == 42


async def test_create_project_source_fields_default_null(client):
    """Source fields are null when not provided."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Regular Project", "machine_uuid": "abc", "process_uuid": "def"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["source_app"] is None
    assert data["source_user"] is None
    assert data["source_layout_id"] is None


async def test_generate_stores_3mf_in_job_pack_folder(client, tmp_path):
    """generate stores the 3MF under Job Pack 3MFs/<job_id>/ in the library."""
    project_id, _ = await _setup_project_with_stl(client, tmp_path)

    lib = tmp_path / "library"
    fake_3mf = _make_3mf_bytes(plate_count=1)

    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_library_dir", return_value=lib),
        patch("app.api.routes.projects.get_laminus_sidecar_url", return_value="http://fake-sidecar"),
        patch("app.api.routes.projects.LaminusSidecarClient") as mock_cls,
        patch("app.api.routes.projects.regen_file_thumbnails", new_callable=AsyncMock),
    ):
        mock_cls.return_value.pack_stls.return_value = fake_3mf

        resp = await client.post(
            f"/api/v1/projects/{project_id}/generate",
            json={"eligible_printer_ids": []},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["files"]) == 1
    assert len(data["jobs"]) == 1

    job_id = data["jobs"][0]["id"]
    file_info = data["files"][0]

    assert file_info["folder"] == f"/Job Pack 3MFs/{job_id}"

    job_pack_dir = lib / "Job Pack 3MFs" / str(job_id)
    assert job_pack_dir.is_dir()
    packed_files = list(job_pack_dir.iterdir())
    assert len(packed_files) == 1
    assert packed_files[0].suffix == ".3mf"


async def test_generate_3mf_folder_field_in_db(client, tmp_path):
    """UploadedFile.folder is set to /Job Pack 3MFs/<job_id> in the database."""
    project_id, _ = await _setup_project_with_stl(client, tmp_path)

    lib = tmp_path / "library"
    fake_3mf = _make_3mf_bytes(plate_count=1)

    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_library_dir", return_value=lib),
        patch("app.api.routes.projects.get_laminus_sidecar_url", return_value="http://fake-sidecar"),
        patch("app.api.routes.projects.LaminusSidecarClient") as mock_cls,
        patch("app.api.routes.projects.regen_file_thumbnails", new_callable=AsyncMock),
    ):
        mock_cls.return_value.pack_stls.return_value = fake_3mf

        resp = await client.post(
            f"/api/v1/projects/{project_id}/generate",
            json={"eligible_printer_ids": []},
        )

    assert resp.status_code == 200
    data = resp.json()
    job_id = data["jobs"][0]["id"]
    expected_folder = f"/Job Pack 3MFs/{job_id}"

    # Verify DB record via the list endpoint filtered by the expected folder.
    list_resp = await client.get(f"/api/v1/files", params={"folder": expected_folder})
    assert list_resp.status_code == 200
    files_in_folder = list_resp.json()
    assert len(files_in_folder) == 1
    assert files_in_folder[0]["folder"] == expected_folder


async def test_generate_multi_plate_uses_id_range_subfolder(client, tmp_path):
    """A multi-plate 3MF creates jobs whose IDs form the subfolder (first_id-last_id)."""
    project_id, _ = await _setup_project_with_stl(client, tmp_path)

    lib = tmp_path / "library"
    fake_3mf = _make_3mf_bytes(plate_count=2)

    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_library_dir", return_value=lib),
        patch("app.api.routes.projects.get_laminus_sidecar_url", return_value="http://fake-sidecar"),
        patch("app.api.routes.projects.LaminusSidecarClient") as mock_cls,
        patch("app.api.routes.projects.regen_file_thumbnails", new_callable=AsyncMock),
    ):
        mock_cls.return_value.pack_stls.return_value = fake_3mf

        resp = await client.post(
            f"/api/v1/projects/{project_id}/generate",
            json={"eligible_printer_ids": []},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["jobs"]) == 2

    first_id = data["jobs"][0]["id"]
    last_id = data["jobs"][-1]["id"]
    expected_label = f"{first_id}-{last_id}"

    assert data["files"][0]["folder"] == f"/Job Pack 3MFs/{expected_label}"

    job_pack_dir = lib / "Job Pack 3MFs" / expected_label
    assert job_pack_dir.is_dir()


async def test_project_estimate_rollup_keys(client):
    """GET /projects/{id} response includes new rollup keys and excludes old ones."""
    resp = await client.post("/api/v1/projects", json={
        "name": "Test", "customer": "", "order_type": "internal",
        "on_hold": False, "due_date": None, "notes": None,
    })
    assert resp.status_code == 201
    proj_id = resp.json()["id"]

    get_resp = await client.get(f"/api/v1/projects/{proj_id}")
    data = get_resp.json()
    for key in ["estimate_filament_grams_total", "estimate_seconds_total",
                "estimate_filament_grams_remaining", "estimate_seconds_remaining",
                "actual_filament_grams", "actual_seconds"]:
        assert key in data, f"missing: {key}"
    assert "filament_grams" not in data
    assert "estimated_seconds" not in data


async def test_project_estimate_remaining_excludes_terminal_jobs(client, tmp_path):
    """estimate_filament_grams_remaining excludes completed/cancelled/failed jobs."""
    from app.main import app
    from app.database import get_session
    from app.models import Job

    resp = await client.post("/api/v1/projects", json={
        "name": "P", "customer": "", "order_type": "internal",
        "on_hold": False, "due_date": None, "notes": None,
    })
    proj_id = resp.json()["id"]

    # Upload a file and create a printer so we can create a job
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"):
        (tmp_path / "library").mkdir(exist_ok=True)
        (tmp_path / "filecache").mkdir(exist_ok=True)
        import io as _io, zipfile as _zf
        buf = _io.BytesIO()
        with _zf.ZipFile(buf, "w") as zf:
            zf.writestr("Metadata/plate_1.png", b"\x89PNG")
        file_resp = await client.post(
            "/api/v1/files/upload",
            files={"file": ("m.3mf", buf.getvalue(), "application/octet-stream")},
        )
    file_id = file_resp.json()["id"]

    printer_resp = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4",
    })
    printer_id = printer_resp.json()["id"]

    with patch("app.api.routes.jobs.queue_engine") as mock_qe:
        mock_qe.spawn_estimate = MagicMock()
        mock_qe.wake = MagicMock()
        j1 = (await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA"}]
        })).json()

    # Set project_id and estimates on job via DB override
    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    job = await session.get(Job, j1["id"])
    job.project_id = proj_id
    job.estimate_filament_grams = 10.0
    job.status = "complete"  # terminal — excluded from remaining
    await session.commit()
    await agen.aclose()

    detail = (await client.get(f"/api/v1/projects/{proj_id}")).json()
    assert detail["estimate_filament_grams_total"] == pytest.approx(10.0)
    assert detail["estimate_filament_grams_remaining"] is None  # all jobs terminal
    assert detail["actual_filament_grams"] is None
