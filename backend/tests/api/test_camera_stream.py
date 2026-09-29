import pytest
from unittest.mock import MagicMock, patch
from app.services.printer_manager import printer_manager


async def _create_printer(client) -> int:
    resp = await client.post("/api/v1/printers", json={
        "name": "Test", "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "192.168.1.20"},
    })
    return resp.json()["id"]


def _camera_client(*, connected=True, camera=True, mjpeg=None, rtsp=None) -> MagicMock:
    client = MagicMock()
    client.connected = connected
    client.get_capabilities.return_value = MagicMock(camera=camera)
    client.camera_mjpeg_url = mjpeg
    client.camera_rtsp_url = rtsp
    return client


async def _empty_stream(url):
    return
    yield  # make it an async generator


async def test_camera_404_on_missing_printer(client):
    resp = await client.get("/api/v1/printers/999/camera")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Printer 999 not found"


async def test_camera_503_when_not_connected(client):
    printer_id = await _create_printer(client)  # registered with the manager but never connected
    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Printer not connected"


async def test_camera_503_when_camera_capable_printer_is_disconnected_and_no_stream_is_started(client):
    printer_id = await _create_printer(client)
    fake = _camera_client(connected=False, mjpeg="http://fake/stream")
    printer_manager._clients[printer_id] = fake

    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")

    assert resp.status_code == 503
    fake.start_video_stream.assert_not_called()


async def test_camera_404_when_no_camera_capability_and_no_stream_is_started(client):
    printer_id = await _create_printer(client)
    fake = _camera_client(camera=False)
    printer_manager._clients[printer_id] = fake

    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "This printer has no camera"
    fake.start_video_stream.assert_not_called()


async def test_camera_404_when_the_printer_has_a_camera_but_no_url_configured(client):
    printer_id = await _create_printer(client)
    printer_manager._clients[printer_id] = _camera_client()

    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "No camera URL configured"


async def test_camera_503_when_rtsp_needs_ffmpeg_and_it_is_missing(client):
    printer_id = await _create_printer(client)
    printer_manager._clients[printer_id] = _camera_client(rtsp="rtsp://192.168.1.20/live")

    with patch("app.api.routes.printers.shutil.which", return_value=None):
        resp = await client.get(f"/api/v1/printers/{printer_id}/camera")

    assert resp.status_code == 503
    assert resp.json()["detail"] == "ffmpeg not available for RTSP streaming"


async def test_camera_streams_mjpeg_as_multipart_after_activating_the_printer_stream(client):
    printer_id = await _create_printer(client)
    fake = _camera_client(mjpeg="http://192.168.1.20:3031/video")
    printer_manager._clients[printer_id] = fake
    opened: list[str] = []

    async def stream(url):
        opened.append(url)
        yield b"--frame\r\nfirst-frame"

    with patch("app.api.routes.printers.stream_mjpeg", stream):
        resp = await client.get(f"/api/v1/printers/{printer_id}/camera")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
    assert resp.content == b"--frame\r\nfirst-frame"
    assert opened == ["http://192.168.1.20:3031/video"]
    fake.start_video_stream.assert_called_once()
