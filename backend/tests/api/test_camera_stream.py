import pytest
from unittest.mock import MagicMock, patch
from app.services.printer_manager import printer_manager


async def _create_printer(client) -> int:
    resp = await client.post("/api/v1/printers", json={
        "name": "Test", "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "192.168.1.20"},
    })
    return resp.json()["id"]


async def test_camera_404_on_missing_printer(client):
    resp = await client.get("/api/v1/printers/999/camera")
    assert resp.status_code == 404


async def test_camera_503_when_not_connected(client):
    printer_id = await _create_printer(client)
    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")
    assert resp.status_code == 503


async def test_camera_404_when_no_camera_capability(client):
    printer_id = await _create_printer(client)
    mock = MagicMock()
    mock.connected = True
    mock.get_capabilities.return_value = MagicMock(camera=False)
    printer_manager._clients[printer_id] = mock
    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")
    assert resp.status_code == 404
    printer_manager._clients.pop(printer_id)


async def test_camera_calls_start_video_stream_when_available(client):
    printer_id = await _create_printer(client)
    mock = MagicMock()
    mock.connected = True
    mock.get_capabilities.return_value = MagicMock(camera=True)
    mock.camera_mjpeg_url = "http://192.168.1.20:3031/video"
    mock.camera_rtsp_url = None

    async def _empty_stream(url):
        return
        yield  # make it an async generator

    printer_manager._clients[printer_id] = mock
    with patch("app.api.routes.printers.stream_mjpeg", _empty_stream):
        resp = await client.get(f"/api/v1/printers/{printer_id}/camera")

    mock.start_video_stream.assert_called_once()
    printer_manager._clients.pop(printer_id)
