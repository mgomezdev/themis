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
    mock.set_fan_speeds.assert_not_called()  # nothing was sent to the printer


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


# ── Console: jog / home / setpoints / direct upload (BIZ-164) ────────────────

from app.services.abstract_printer_client import PrinterCapabilities


def _console_client(**caps):
    mock = _mock_connected_client()
    mock.is_printing = False
    mock.is_idle = True
    mock.get_capabilities.return_value = PrinterCapabilities(**caps)
    for m in ("jog", "home", "home_axes", "set_nozzle_temp", "set_chamber_temp", "upload_file", "start_print"):
        getattr(mock, m).return_value = True
    return mock


async def test_jog_z_needs_no_capability_but_xy_does(client, printer_id):
    mock = _console_client()
    printer_manager._clients[printer_id] = mock

    assert (await client.post(f"/api/v1/printers/{printer_id}/jog", json={"axis": "Z", "distance_mm": -1})).status_code == 200
    mock.jog.assert_called_once_with("Z", -1)

    resp = await client.post(f"/api/v1/printers/{printer_id}/jog", json={"axis": "X", "distance_mm": 10})
    assert resp.status_code == 409 and "X/Y jog" in resp.json()["detail"]
    assert mock.jog.call_count == 1                                     # the refused move never reached the printer


async def test_xy_jog_with_the_capability_reaches_the_client(client, printer_id):
    mock = _console_client(axis_jog=True)
    printer_manager._clients[printer_id] = mock

    resp = await client.post(f"/api/v1/printers/{printer_id}/jog", json={"axis": "Y", "distance_mm": 5.5})

    assert resp.status_code == 200
    mock.jog.assert_called_once_with("Y", 5.5)


@pytest.mark.parametrize("body", [
    {"axis": "E", "distance_mm": 1}, {"axis": "X", "distance_mm": 500}, {"axis": "X", "distance_mm": "far"}, {"axis": "X"},
])
async def test_jog_validation(client, printer_id, body):
    printer_manager._clients[printer_id] = _console_client(axis_jog=True)
    assert (await client.post(f"/api/v1/printers/{printer_id}/jog", json=body)).status_code == 422


async def test_motion_commands_are_refused_while_printing(client, printer_id):
    mock = _console_client(axis_jog=True, home_axes=True)
    mock.is_printing = True
    printer_manager._clients[printer_id] = mock

    assert (await client.post(f"/api/v1/printers/{printer_id}/jog", json={"axis": "Z", "distance_mm": 1})).status_code == 409
    assert (await client.post(f"/api/v1/printers/{printer_id}/home", json={})).status_code == 409
    mock.jog.assert_not_called()
    mock.home.assert_not_called()


async def test_home_all_always_and_single_axis_only_with_the_capability(client, printer_id):
    mock = _console_client()
    printer_manager._clients[printer_id] = mock
    assert (await client.post(f"/api/v1/printers/{printer_id}/home", json={})).status_code == 200
    mock.home.assert_called_once()

    assert (await client.post(f"/api/v1/printers/{printer_id}/home", json={"axes": "X"})).status_code == 409
    mock.home_axes.assert_not_called()

    mock.get_capabilities.return_value = PrinterCapabilities(home_axes=True)
    assert (await client.post(f"/api/v1/printers/{printer_id}/home", json={"axes": "X"})).status_code == 200
    mock.home_axes.assert_called_once_with("X")
    assert (await client.post(f"/api/v1/printers/{printer_id}/home", json={"axes": "Q"})).status_code == 422


@pytest.mark.parametrize("path,cap,method", [
    ("nozzle-temp", "nozzle_temp", "set_nozzle_temp"),
    ("chamber-temp", "chamber_temp", "set_chamber_temp"),
])
async def test_setpoints_are_capability_gated_and_range_checked(client, printer_id, path, cap, method):
    mock = _console_client()
    printer_manager._clients[printer_id] = mock
    url = f"/api/v1/printers/{printer_id}/{path}"

    assert (await client.post(url, json={"celsius": 60})).status_code == 409
    getattr(mock, method).assert_not_called()

    mock.get_capabilities.return_value = PrinterCapabilities(**{cap: True})
    assert (await client.post(url, json={"celsius": 60})).status_code == 200
    getattr(mock, method).assert_called_once_with(60)
    assert (await client.post(url, json={"celsius": 1000})).status_code == 422
    assert (await client.post(url, json={"celsius": -1})).status_code == 422


async def test_a_printer_that_rejects_the_command_is_a_502_not_a_silent_ok(client, printer_id):
    mock = _console_client(nozzle_temp=True)
    mock.set_nozzle_temp.return_value = False
    printer_manager._clients[printer_id] = mock
    assert (await client.post(f"/api/v1/printers/{printer_id}/nozzle-temp", json={"celsius": 200})).status_code == 502


@pytest.mark.parametrize("path,body", [
    ("jog", {"axis": "Z", "distance_mm": 1}), ("home", {}), ("nozzle-temp", {"celsius": 1}),
])
async def test_console_commands_404_and_503(client, printer_id, path, body):
    assert (await client.post(f"/api/v1/printers/999/{path}", json=body)).status_code == 404
    assert (await client.post(f"/api/v1/printers/{printer_id}/{path}", json=body)).status_code == 503


def _upload(client, printer_id, name="part.gcode", data=b"G28\n", start=None):
    form = {"start": "true"} if start else {}
    return client.post(f"/api/v1/printers/{printer_id}/upload", files={"file": (name, data)}, data=form)


async def test_upload_only_sends_the_file_and_starts_nothing(client, printer_id):
    mock = _console_client(direct_upload=True)
    printer_manager._clients[printer_id] = mock

    resp = await _upload(client, printer_id)

    assert resp.status_code == 200 and resp.json() == {"ok": True, "filename": "part.gcode", "started": False}
    mock.upload_file.assert_called_once_with(b"G28\n", "part.gcode")
    mock.start_print.assert_not_called()


async def test_upload_and_print_starts_the_print_and_marks_the_plate_not_ready(client, printer_id):
    mock = _console_client(direct_upload=True)
    printer_manager._clients[printer_id] = mock

    resp = await _upload(client, printer_id, name="C:\\tmp\\Benchy.gcode.3mf", start=True)

    assert resp.json()["filename"] == "Benchy.gcode.3mf"                    # client path stripped
    mock.start_print.assert_called_once()
    assert mock.start_print.call_args.args[0] == "Benchy.gcode.3mf"
    assert (await client.get(f"/api/v1/printers/{printer_id}")).json()["awaiting_plate_clear"] is True
    assert printer_manager.is_awaiting_plate_clear(printer_id)


async def test_upload_and_print_refuses_a_busy_printer_without_uploading(client, printer_id, session_factory):
    from app.models import Job, UploadedFile
    mock = _console_client(direct_upload=True)
    printer_manager._clients[printer_id] = mock

    mock.is_idle = False
    assert (await _upload(client, printer_id, start=True)).status_code == 409

    mock.is_idle = True
    async with session_factory() as s:                                         # a queue job holds the printer
        f = UploadedFile(original_filename="a.3mf", stored_path="/x", plates=[], uploaded_at="t")
        s.add(f)
        await s.flush()
        s.add(Job(uploaded_file_id=f.id, plate_number=1, status="slicing", assigned_printer_id=printer_id,
                  queue_position=1.0, created_at="t", updated_at="t"))
        await s.commit()
    resp = await _upload(client, printer_id, start=True)
    assert resp.status_code == 409 and "queued job" in resp.json()["detail"]
    mock.upload_file.assert_not_called()
    assert (await _upload(client, printer_id)).status_code == 200              # plain upload is still fine


@pytest.mark.parametrize("name", ["notes.txt", "evil.exe", "", "noext"])
async def test_upload_rejects_other_file_types(client, printer_id, name):
    mock = _console_client(direct_upload=True)
    printer_manager._clients[printer_id] = mock
    assert (await _upload(client, printer_id, name=name or "x")).status_code in (422,)
    mock.upload_file.assert_not_called()


async def test_upload_failure_modes(client, printer_id):
    mock = _console_client(direct_upload=True)
    printer_manager._clients[printer_id] = mock
    mock.upload_file.return_value = False
    assert (await _upload(client, printer_id)).status_code == 502
    mock.upload_file.return_value = True
    mock.start_print.return_value = False
    resp = await _upload(client, printer_id, start=True)
    assert resp.status_code == 502 and "would not start" in resp.json()["detail"]
    assert (await client.get(f"/api/v1/printers/{printer_id}")).json()["awaiting_plate_clear"] is False   # not marked: nothing printing


async def test_upload_needs_the_capability(client, printer_id):
    mock = _console_client()
    printer_manager._clients[printer_id] = mock
    assert (await _upload(client, printer_id)).status_code == 409


def test_default_client_commands_are_plain_gcode():
    from app.services.mock_printer_client import MockPrinterClient
    c = MockPrinterClient.__new__(MockPrinterClient)
    sent: list[str] = []
    c.send_gcode = lambda g: sent.append(g) or True          # type: ignore[method-assign]
    c.jog("x", -2.5)
    c.home_axes("xy")
    c.set_nozzle_temp(210)
    assert sent == ["G91", "G1 X-2.5", "G90", "G28 X Y", "M104 S210"]
