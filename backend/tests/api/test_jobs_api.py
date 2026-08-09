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


async def test_unblock_clears_slice_failure_and_requeues(client, tmp_path):
    """Unblocking must reset slice_failed so the job actually re-slices; otherwise
    the engine re-blocks it immediately with the stale error."""
    from app.models import Job, JobPrinterConfig
    from sqlalchemy import select

    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    with patch("app.api.routes.jobs.queue_engine"):
        create = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm"}],
        })
    job_id = create.json()["id"]

    # Simulate a prior slice failure that left the job blocked, using the same
    # session factory the API is wired to (conftest's get_session override).
    from app.main import app
    from app.database import get_session
    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    job = await session.get(Job, job_id)
    job.status = "blocked"
    job.block_reason = "slicing failed: boom"
    cfg = (await session.execute(
        select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))).scalar_one()
    cfg.slice_failed = True
    cfg.slice_error = "boom"
    await session.commit()
    await agen.aclose()

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post(f"/api/v1/jobs/{job_id}/unblock")
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"

    # The slice-failure flag must be cleared.
    failures = await client.get(f"/api/v1/jobs/{job_id}/slice-failures")
    assert failures.json() == []


async def test_cancel_running_job_stops_printer(client, tmp_path):
    """Cancelling a job the printer is actively running must also stop the printer."""
    from unittest.mock import MagicMock
    from app.models import Job
    from app.main import app
    from app.database import get_session
    from app.services.printer_manager import printer_manager

    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    with patch("app.api.routes.jobs.queue_engine"):
        create = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm"}],
        })
    job_id = create.json()["id"]

    # Put the job in a printing state assigned to the printer.
    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    job = await session.get(Job, job_id)
    job.status = "printing"
    job.assigned_printer_id = printer_id
    await session.commit()
    await agen.aclose()

    mock_client = MagicMock()
    mock_client.connected = True
    printer_manager._clients[printer_id] = mock_client
    try:
        with patch("app.api.routes.jobs.queue_engine"):
            resp = await client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "cancelled"
        assert body["assigned_printer_id"] is None
        mock_client.stop_print.assert_called_once()
    finally:
        printer_manager._clients.pop(printer_id, None)


async def test_verify_slice_success(client, tmp_path):
    from unittest.mock import MagicMock
    from app.services.slicer_service import SliceError  # noqa: F401

    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    with patch("app.api.routes.jobs.queue_engine"):
        create = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm"}],
        })
    job_id = create.json()["id"]

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path
    expected_output_dir = tmp_path / "gcode_verify" / str(job_id)

    async def fake_run_verify_slice(req, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        gcode_file = output_dir / "out.gcode"
        gcode_file.write_text("G28")
        return str(gcode_file)

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe):
        resp = await client.post(f"/api/v1/jobs/{job_id}/verify-slice",
                                  json={"printer_id": printer_id})

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["error"] is None
    assert not expected_output_dir.exists()  # scratch dir cleaned up after test run


async def test_verify_slice_slice_error(client, tmp_path):
    from unittest.mock import MagicMock
    from app.services.slicer_service import SliceError

    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    with patch("app.api.routes.jobs.queue_engine"):
        create = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm"}],
        })
    job_id = create.json()["id"]

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path

    async def fake_run_verify_slice(req, output_dir):
        raise SliceError("OrcaSlicer exited with code 1\nboom")

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe):
        resp = await client.post(f"/api/v1/jobs/{job_id}/verify-slice",
                                  json={"printer_id": printer_id})

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "OrcaSlicer" in data["error"]


async def test_verify_slice_missing_printer_config(client, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    with patch("app.api.routes.jobs.queue_engine"):
        create = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm"}],
        })
    job_id = create.json()["id"]

    # Use a printer_id that has no config for this job
    resp = await client.post(f"/api/v1/jobs/{job_id}/verify-slice",
                              json={"printer_id": 9999})
    assert resp.status_code == 404


async def test_verify_slice_does_not_touch_production_gcode_dir(client, tmp_path):
    """Test-slicing a job must not delete its already-produced production gcode.
    slice() unlinks *.gcode/*.gcode.3mf in whatever output dir it's given before
    writing, and the production dir defaults to <data_dir>/gcode/<job_id> — the
    exact directory a parked job's real artifact lives in. verify-slice must pass
    its own scratch dir instead."""
    from unittest.mock import MagicMock

    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    with patch("app.api.routes.jobs.queue_engine"):
        create = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm"}],
        })
    job_id = create.json()["id"]

    # Simulate the already-produced production artifact for this job.
    prod_dir = tmp_path / "gcode" / str(job_id)
    prod_dir.mkdir(parents=True)
    prod_gcode = prod_dir / "model.gcode"
    prod_gcode.write_text("G28 ; production")

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path
    expected_output_dir = tmp_path / "gcode_verify" / str(job_id)

    async def fake_run_verify_slice(req, output_dir):
        assert output_dir == expected_output_dir, "verify-slice must not slice into the production gcode dir"
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / "test.gcode"
        out.write_text("G28 ; test")
        return str(out)

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe):
        resp = await client.post(f"/api/v1/jobs/{job_id}/verify-slice",
                                  json={"printer_id": printer_id})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert prod_gcode.exists()  # production artifact survives the test-slice
    assert not expected_output_dir.exists()  # scratch dir cleaned up


async def test_cancel_queued_job_does_not_stop_printer(client, tmp_path):
    """A queued (not yet printing) job cancel must NOT send stop to any printer."""
    from unittest.mock import MagicMock
    from app.services.printer_manager import printer_manager

    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    with patch("app.api.routes.jobs.queue_engine"):
        create = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm"}],
        })
    job_id = create.json()["id"]

    mock_client = MagicMock()
    mock_client.connected = True
    printer_manager._clients[printer_id] = mock_client
    try:
        with patch("app.api.routes.jobs.queue_engine"):
            resp = await client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert resp.json()["status"] == "cancelled"
        mock_client.stop_print.assert_not_called()
    finally:
        printer_manager._clients.pop(printer_id, None)


async def test_job_response_includes_estimate_fields(client, tmp_path):
    """POST /jobs response includes all new estimate and actual fields."""
    from unittest.mock import MagicMock
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    payload = {
        "uploaded_file_id": file_id,
        "plate_number": 1,
        "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA"}],
    }
    with patch("app.api.routes.jobs.queue_engine") as mock_qe:
        mock_qe.spawn_estimate = MagicMock()
        mock_qe.wake = MagicMock()
        resp = await client.post("/api/v1/jobs", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    for field in ["estimate_status", "estimate_filament_grams", "estimate_seconds",
                  "estimate_filament_breakdown", "estimate_preset_label",
                  "actual_filament_grams", "actual_seconds", "deduction_skipped"]:
        assert field in data, f"missing: {field}"


async def test_cancel_job_clears_estimate_status(client, tmp_path):
    """POST /jobs/{id}/cancel clears estimate_status when it is 'pending'."""
    from unittest.mock import MagicMock
    from app.models import Job
    from app.database import get_session

    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)

    with patch("app.api.routes.jobs.queue_engine") as mock_qe:
        mock_qe.spawn_estimate = MagicMock()
        mock_qe.wake = MagicMock()
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA"}]
        })
    job_id = resp.json()["id"]

    # Set estimate_status to pending via the test DB session
    from app.main import app
    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    job = await session.get(Job, job_id)
    job.estimate_status = "pending"
    await session.commit()
    await agen.aclose()

    with patch("app.api.routes.jobs.queue_engine") as mock_qe:
        mock_qe.wake = MagicMock()
        cancel_resp = await client.post(f"/api/v1/jobs/{job_id}/cancel")

    assert cancel_resp.status_code == 200
    data = cancel_resp.json()
    assert data["estimate_status"] is None


async def test_list_jobs_includes_materials_and_printers(client):
    from app.models import UploadedFile, Job, JobPrinterConfig, Printer
    from app.main import app
    from app.database import get_session

    # Build fixtures directly via the test session
    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()

    p1 = Printer(name="X1C", printer_type="bambu", connection_config={})
    session.add(p1)
    f = UploadedFile(original_filename="x.3mf", stored_path="/tmp/x.3mf",
                     plates=[], uploaded_at="2026-01-01T00:00:00")
    session.add(f)
    await session.flush()

    j = Job(uploaded_file_id=f.id, plate_number=1, status="queued",
            queue_position=1.0, created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00")
    session.add(j)
    await session.flush()

    cfg = JobPrinterConfig(job_id=j.id, printer_id=p1.id,
                           print_profile="0.20mm", filament_profile="PLA Basic",
                           filament_type="PLA")
    session.add(cfg)
    await session.commit()
    await agen.aclose()

    resp = await client.get("/api/v1/jobs")
    assert resp.status_code == 200
    jobs = resp.json()
    job = next(jj for jj in jobs if jj["id"] == j.id)
    assert job["materials"] == ["PLA"]
    assert any(ep["id"] == p1.id and ep["name"] == "X1C" for ep in job["eligible_printers"])


async def test_job_details_returns_live_fields(client, tmp_path):
    """GET /jobs/{id}/details returns filament_grams_live and estimated_seconds_live."""
    from unittest.mock import MagicMock
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)

    with patch("app.api.routes.jobs.queue_engine") as mock_qe:
        mock_qe.spawn_estimate = MagicMock()
        mock_qe.wake = MagicMock()
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA"}]
        })
    job_id = resp.json()["id"]

    detail_resp = await client.get(f"/api/v1/jobs/{job_id}/details")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert "filament_grams_live" in detail
    assert "estimated_seconds_live" in detail
    assert "filament_grams" not in detail
    assert "estimated_seconds" not in detail


async def test_reorder_front_gives_lowest_position(client):
    """POST /jobs/{id}/reorder with action='front' moves job ahead of all others."""
    from app.models import UploadedFile, Job
    from app.main import app
    from app.database import get_session

    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    f = UploadedFile(original_filename="x.3mf", stored_path="/t/x.3mf",
                     plates=[], uploaded_at="2026-01-01T00:00:00")
    session.add(f)
    await session.flush()
    j1 = Job(uploaded_file_id=f.id, plate_number=1, status="queued",
             queue_position=1.0, created_at="2026-01-01T00:00:00",
             updated_at="2026-01-01T00:00:00")
    j2 = Job(uploaded_file_id=f.id, plate_number=1, status="queued",
             queue_position=2.0, created_at="2026-01-01T00:00:00",
             updated_at="2026-01-01T00:00:00")
    session.add_all([j1, j2])
    await session.commit()
    j1_id, j2_id = j1.id, j2.id
    j1_pos = j1.queue_position
    await agen.aclose()

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post(f"/api/v1/jobs/{j2_id}/reorder", json={"action": "front"})
    assert resp.status_code == 200

    agen2 = app.dependency_overrides[get_session]()
    session2 = await agen2.__anext__()
    j2_refreshed = await session2.get(Job, j2_id)
    assert j2_refreshed.queue_position < j1_pos
    await agen2.aclose()


async def test_reorder_promote_moves_ahead_of_previous(client):
    """POST /jobs/{id}/reorder with action='promote' moves job one step up."""
    from app.models import UploadedFile, Job
    from app.main import app
    from app.database import get_session

    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    f = UploadedFile(original_filename="x.3mf", stored_path="/t/x.3mf",
                     plates=[], uploaded_at="2026-01-01T00:00:00")
    session.add(f)
    await session.flush()
    j1 = Job(uploaded_file_id=f.id, plate_number=1, status="queued",
             queue_position=1.0, created_at="2026-01-01T00:00:00",
             updated_at="2026-01-01T00:00:00")
    j2 = Job(uploaded_file_id=f.id, plate_number=1, status="queued",
             queue_position=2.0, created_at="2026-01-01T00:00:00",
             updated_at="2026-01-01T00:00:00")
    session.add_all([j1, j2])
    await session.commit()
    j1_id, j2_id = j1.id, j2.id
    j1_pos = j1.queue_position
    await agen.aclose()

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post(f"/api/v1/jobs/{j2_id}/reorder", json={"action": "promote"})
    assert resp.status_code == 200

    agen2 = app.dependency_overrides[get_session]()
    session2 = await agen2.__anext__()
    j2_refreshed = await session2.get(Job, j2_id)
    assert j2_refreshed.queue_position < j1_pos
    await agen2.aclose()


async def test_reorder_rejects_non_queued_job(client):
    """POST /jobs/{id}/reorder returns 422 for jobs not in queued/blocked status."""
    from app.models import UploadedFile, Job
    from app.main import app
    from app.database import get_session

    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    f = UploadedFile(original_filename="x.3mf", stored_path="/t/x.3mf",
                     plates=[], uploaded_at="2026-01-01T00:00:00")
    session.add(f)
    await session.flush()
    j = Job(uploaded_file_id=f.id, plate_number=1, status="printing",
            queue_position=1.0, created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00")
    session.add(j)
    await session.commit()
    j_id = j.id
    await agen.aclose()

    resp = await client.post(f"/api/v1/jobs/{j_id}/reorder", json={"action": "promote"})
    assert resp.status_code == 422
