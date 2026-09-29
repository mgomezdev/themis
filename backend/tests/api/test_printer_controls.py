import pytest
import pytest_asyncio
from unittest.mock import MagicMock, patch
from app.services.printer_manager import printer_manager


@pytest_asyncio.fixture
async def printer_id(create_printer) -> int:
    """An Elegoo printer registered with the manager (the conftest stub never connects it)."""
    return await create_printer(name="Test Printer", printer_type="elegoo_centauri",
                                connection_config={"ip_address": "192.168.1.99"})


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


async def test_pause_503_when_not_connected(client, printer_id):
    resp = await client.post(f"/api/v1/printers/{printer_id}/pause")
    assert resp.status_code == 503


async def test_pause_ok(client, printer_id):
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/pause")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.pause_print.assert_called_once()


# ── Resume ───────────────────────────────────────────────────────────────────

async def test_resume_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/resume")
    assert resp.status_code == 404


async def test_resume_503_when_not_connected(client, printer_id):
    resp = await client.post(f"/api/v1/printers/{printer_id}/resume")
    assert resp.status_code == 503


async def test_resume_ok(client, printer_id):
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/resume")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.resume_print.assert_called_once()


# ── Stop ─────────────────────────────────────────────────────────────────────

async def test_stop_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/stop")
    assert resp.status_code == 404


async def test_stop_503_when_not_connected(client, printer_id):
    resp = await client.post(f"/api/v1/printers/{printer_id}/stop")
    assert resp.status_code == 503


async def test_stop_ok(client, printer_id):
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/stop")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.stop_print.assert_called_once()


async def test_stop_reconciles_running_job(client, session_factory, printer_id):
    """Stopping a printer that's running a job marks that job cancelled."""
    from datetime import datetime, timezone
    from app.models import Job, UploadedFile

    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock

    # Seed an uploaded file + a printing job assigned to this printer.
    async with session_factory() as session:
        now = datetime.now(timezone.utc).isoformat()
        uf = UploadedFile(original_filename="m.3mf", stored_path="/x/m.3mf", plates=[], uploaded_at=now)
        session.add(uf)
        await session.flush()
        job = Job(uploaded_file_id=uf.id, plate_number=1, status="printing",
                  assigned_printer_id=printer_id, queue_position=1.0, created_at=now, updated_at=now)
        session.add(job)
        await session.commit()
        job_id = job.id

    resp = await client.post(f"/api/v1/printers/{printer_id}/stop")
    assert resp.status_code == 200
    mock.stop_print.assert_called_once()
    updated = await client.get(f"/api/v1/jobs/{job_id}")
    body = updated.json()
    assert body["status"] == "cancelled"
    assert body["assigned_printer_id"] is None


# ── Light ────────────────────────────────────────────────────────────────────

async def test_light_ok_on(client, printer_id):
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/light", json={"on": True})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.set_chamber_light.assert_called_once_with(True)


async def test_light_ok_off(client, printer_id):
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/light", json={"on": False})
    assert resp.status_code == 200
    mock.set_chamber_light.assert_called_once_with(False)


async def test_light_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/light", json={"on": True})
    assert resp.status_code == 404


async def test_light_503_when_not_connected(client, printer_id):
    resp = await client.post(f"/api/v1/printers/{printer_id}/light", json={"on": True})
    assert resp.status_code == 503


# ── Jog-Z ────────────────────────────────────────────────────────────────────

async def test_jog_z_ok(client, printer_id):
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/jog-z", json={"distance_mm": 10.0})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.jog_z.assert_called_once_with(10.0)


async def test_jog_z_negative(client, printer_id):
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/jog-z", json={"distance_mm": -10.0})
    assert resp.status_code == 200
    mock.jog_z.assert_called_once_with(-10.0)


async def test_jog_z_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/jog-z", json={"distance_mm": 5.0})
    assert resp.status_code == 404


async def test_jog_z_503_when_not_connected(client, printer_id):
    resp = await client.post(f"/api/v1/printers/{printer_id}/jog-z", json={"distance_mm": 5.0})
    assert resp.status_code == 503


# ── Fan ──────────────────────────────────────────────────────────────────────

async def test_fan_ok_changes_model_fan_preserves_others(client, printer_id):
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


async def test_fan_ok_changes_aux_fan(client, printer_id):
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


async def test_fan_ok_changes_box_fan(client, printer_id):
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


async def test_fan_422_on_invalid_fan_name(client, printer_id):
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


async def test_fan_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/fan", json={"fan": "model", "speed_pct": 50})
    assert resp.status_code == 404


async def test_fan_503_when_not_connected(client, printer_id):
    resp = await client.post(f"/api/v1/printers/{printer_id}/fan", json={"fan": "model", "speed_pct": 50})
    assert resp.status_code == 503


# ── Bed temp ─────────────────────────────────────────────────────────────────

async def test_bed_temp_ok(client, printer_id):
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/bed-temp", json={"celsius": 95})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.set_bed_temp.assert_called_once_with(95)


async def test_bed_temp_zero_turns_off(client, printer_id):
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/bed-temp", json={"celsius": 0})
    assert resp.status_code == 200
    mock.set_bed_temp.assert_called_once_with(0)


async def test_bed_temp_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/bed-temp", json={"celsius": 95})
    assert resp.status_code == 404


async def test_bed_temp_503_when_not_connected(client, printer_id):
    resp = await client.post(f"/api/v1/printers/{printer_id}/bed-temp", json={"celsius": 95})
    assert resp.status_code == 503
