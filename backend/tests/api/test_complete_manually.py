import io
import json
import zipfile
from unittest.mock import MagicMock, patch

import pytest


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


async def _create_printer(client, **overrides):
    body = {
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4"],
        "current_orca_printer_profile": "Bambu Lab P1S 0.4",
    }
    body.update(overrides)
    resp = await client.post("/api/v1/printers", json=body)
    return resp.json()["id"]


async def _create_job(client, file_id, printer_id, **config_overrides):
    config = {
        "printer_id": printer_id, "print_profile": "0.20mm",
        "filament_type": "any", "filament_color": "any",
    }
    config.update(config_overrides)
    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [config],
        })
    return resp.json()["id"]


def _fake_gcode_with_estimates(output_dir, grams: float = 12.5, time_str: str = "1h 5m 30s"):
    output_dir.mkdir(parents=True, exist_ok=True)
    gcode_file = output_dir / "out.gcode"
    gcode_file.write_text(
        f"; filament used [g] = {grams}\n"
        f"; estimated printing time (normal mode) = {time_str}\n"
    )
    return str(gcode_file)


async def test_complete_manually_happy_path(client, tmp_path):
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    job_id = await _create_job(client, file_id, printer_id)

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path

    async def fake_run_verify_slice(req, output_dir):
        return _fake_gcode_with_estimates(output_dir)

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe), \
         patch("app.api.routes.jobs._deduct_spool") as mock_deduct:
        resp = await client.post(
            f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "complete"
    assert body["completed_at"] is not None
    assert body["actual_filament_grams"] == 12.5
    assert body["actual_seconds"] == 3930  # 1h5m30s
    assert body["assigned_printer_id"] == printer_id
    mock_deduct.assert_not_called()  # no spool loaded — nothing to deduct

    printer_resp = await client.get(f"/api/v1/printers/{printer_id}")
    printer_data = printer_resp.json()
    assert printer_data["awaiting_plate_clear"] is True

    # lifetime_job_count/lifetime_print_seconds aren't exposed via the printers
    # API - check the DB directly, same pattern as the 409-terminal-status test.
    from app.database import get_session
    from app.main import app
    from app.models import Printer

    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    printer = await session.get(Printer, printer_id)
    assert printer.lifetime_job_count == 1
    assert printer.lifetime_print_seconds == 3930
    await agen.aclose()
