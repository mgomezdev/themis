import pytest
from unittest.mock import MagicMock, patch
from app.services.printer_manager import printer_manager


async def _create_printer(client) -> int:
    resp = await client.post("/api/v1/printers", json={
        "name": "Test Printer",
        "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "192.168.1.99"},
    })
    assert resp.status_code == 201
    return resp.json()["id"]


def _mock_connected_client():
    mock = MagicMock()
    mock.connected = True
    mock.pause_print.return_value = True
    mock.resume_print.return_value = True
    mock.stop_print.return_value = True
    mock.set_chamber_light.return_value = True
    mock.jog_z.return_value = True
    mock.set_fan_speeds.return_value = True
    mock.set_bed_temp.return_value = True
    return mock


# ── Pause ────────────────────────────────────────────────────────────────────

async def test_pause_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/pause")
    assert resp.status_code == 404


async def test_pause_503_when_not_connected(client):
    printer_id = await _create_printer(client)
    resp = await client.post(f"/api/v1/printers/{printer_id}/pause")
    assert resp.status_code == 503
    printer_manager._clients.pop(printer_id, None)


async def test_pause_ok(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/pause")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.pause_print.assert_called_once()
    printer_manager._clients.pop(printer_id)


# ── Resume ───────────────────────────────────────────────────────────────────

async def test_resume_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/resume")
    assert resp.status_code == 404


async def test_resume_503_when_not_connected(client):
    printer_id = await _create_printer(client)
    resp = await client.post(f"/api/v1/printers/{printer_id}/resume")
    assert resp.status_code == 503
    printer_manager._clients.pop(printer_id, None)


async def test_resume_ok(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/resume")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.resume_print.assert_called_once()
    printer_manager._clients.pop(printer_id)


# ── Stop ─────────────────────────────────────────────────────────────────────

async def test_stop_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/stop")
    assert resp.status_code == 404


async def test_stop_503_when_not_connected(client):
    printer_id = await _create_printer(client)
    resp = await client.post(f"/api/v1/printers/{printer_id}/stop")
    assert resp.status_code == 503
    printer_manager._clients.pop(printer_id, None)


async def test_stop_ok(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/stop")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.stop_print.assert_called_once()
    printer_manager._clients.pop(printer_id)


async def test_stop_reconciles_running_job(client):
    """Stopping a printer that's running a job marks that job cancelled."""
    from datetime import datetime, timezone
    from app.models import Job, UploadedFile
    from app.main import app
    from app.database import get_session

    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock

    # Seed an uploaded file + a printing job assigned to this printer.
    agen = app.dependency_overrides[get_session]()
    session = await agen.__anext__()
    now = datetime.now(timezone.utc).isoformat()
    uf = UploadedFile(original_filename="m.3mf", stored_path="/x/m.3mf", plates=[], uploaded_at=now)
    session.add(uf)
    await session.flush()
    job = Job(uploaded_file_id=uf.id, plate_number=1, status="printing",
              assigned_printer_id=printer_id, queue_position=1.0, created_at=now, updated_at=now)
    session.add(job)
    await session.commit()
    job_id = job.id
    await agen.aclose()

    try:
        resp = await client.post(f"/api/v1/printers/{printer_id}/stop")
        assert resp.status_code == 200
        mock.stop_print.assert_called_once()
        updated = await client.get(f"/api/v1/jobs/{job_id}")
        body = updated.json()
        assert body["status"] == "cancelled"
        assert body["assigned_printer_id"] is None
    finally:
        printer_manager._clients.pop(printer_id, None)


# ── Light ────────────────────────────────────────────────────────────────────

async def test_light_ok_on(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/light", json={"on": True})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.set_chamber_light.assert_called_once_with(True)
    printer_manager._clients.pop(printer_id)


async def test_light_ok_off(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/light", json={"on": False})
    assert resp.status_code == 200
    mock.set_chamber_light.assert_called_once_with(False)
    printer_manager._clients.pop(printer_id)


async def test_light_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/light", json={"on": True})
    assert resp.status_code == 404


async def test_light_503_when_not_connected(client):
    printer_id = await _create_printer(client)
    resp = await client.post(f"/api/v1/printers/{printer_id}/light", json={"on": True})
    assert resp.status_code == 503
    printer_manager._clients.pop(printer_id, None)


# ── Jog-Z ────────────────────────────────────────────────────────────────────

async def test_jog_z_ok(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/jog-z", json={"distance_mm": 10.0})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.jog_z.assert_called_once_with(10.0)
    printer_manager._clients.pop(printer_id)


async def test_jog_z_negative(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/jog-z", json={"distance_mm": -10.0})
    assert resp.status_code == 200
    mock.jog_z.assert_called_once_with(-10.0)
    printer_manager._clients.pop(printer_id)


async def test_jog_z_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/jog-z", json={"distance_mm": 5.0})
    assert resp.status_code == 404


async def test_jog_z_503_when_not_connected(client):
    printer_id = await _create_printer(client)
    resp = await client.post(f"/api/v1/printers/{printer_id}/jog-z", json={"distance_mm": 5.0})
    assert resp.status_code == 503
    printer_manager._clients.pop(printer_id, None)


# ── Fan ──────────────────────────────────────────────────────────────────────

async def test_fan_ok_changes_model_fan_preserves_others(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    with patch.object(printer_manager, "get_normalized_state", return_value={
        "fan_model": 50, "fan_aux": 60, "fan_box": 40,
    }):
        resp = await client.post(
            f"/api/v1/printers/{printer_id}/fan",
            json={"fan": "model", "speed_pct": 100},
        )
    assert resp.status_code == 200
    mock.set_fan_speeds.assert_called_once_with(100, 60, 40)
    printer_manager._clients.pop(printer_id)


async def test_fan_ok_changes_aux_fan(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    with patch.object(printer_manager, "get_normalized_state", return_value={
        "fan_model": 80, "fan_aux": 60, "fan_box": 40,
    }):
        resp = await client.post(
            f"/api/v1/printers/{printer_id}/fan",
            json={"fan": "auxiliary", "speed_pct": 0},
        )
    assert resp.status_code == 200
    mock.set_fan_speeds.assert_called_once_with(80, 0, 40)
    printer_manager._clients.pop(printer_id)


async def test_fan_ok_changes_box_fan(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    with patch.object(printer_manager, "get_normalized_state", return_value={
        "fan_model": 80, "fan_aux": 60, "fan_box": 40,
    }):
        resp = await client.post(
            f"/api/v1/printers/{printer_id}/fan",
            json={"fan": "box", "speed_pct": 100},
        )
    assert resp.status_code == 200
    mock.set_fan_speeds.assert_called_once_with(80, 60, 100)
    printer_manager._clients.pop(printer_id)


async def test_fan_422_on_invalid_fan_name(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    with patch.object(printer_manager, "get_normalized_state", return_value={
        "fan_model": 0, "fan_aux": 0, "fan_box": 0,
    }):
        resp = await client.post(
            f"/api/v1/printers/{printer_id}/fan",
            json={"fan": "turbo", "speed_pct": 100},
        )
    assert resp.status_code == 422
    printer_manager._clients.pop(printer_id)


async def test_fan_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/fan", json={"fan": "model", "speed_pct": 50})
    assert resp.status_code == 404


async def test_fan_503_when_not_connected(client):
    printer_id = await _create_printer(client)
    resp = await client.post(f"/api/v1/printers/{printer_id}/fan", json={"fan": "model", "speed_pct": 50})
    assert resp.status_code == 503
    printer_manager._clients.pop(printer_id, None)


# ── Bed temp ─────────────────────────────────────────────────────────────────

async def test_bed_temp_ok(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/bed-temp", json={"celsius": 95})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.set_bed_temp.assert_called_once_with(95)
    printer_manager._clients.pop(printer_id)


async def test_bed_temp_zero_turns_off(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/bed-temp", json={"celsius": 0})
    assert resp.status_code == 200
    mock.set_bed_temp.assert_called_once_with(0)
    printer_manager._clients.pop(printer_id)


async def test_bed_temp_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/bed-temp", json={"celsius": 95})
    assert resp.status_code == 404


async def test_bed_temp_503_when_not_connected(client):
    printer_id = await _create_printer(client)
    resp = await client.post(f"/api/v1/printers/{printer_id}/bed-temp", json={"celsius": 95})
    assert resp.status_code == 503
    printer_manager._clients.pop(printer_id, None)
