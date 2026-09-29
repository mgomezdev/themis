"""GET /printers/{id}/snapshot — the real route (test_auth only exercises a stand-in)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.printer_manager import printer_manager

JPEG = b"\xff\xd8snapshot\xff\xd9"


@pytest_asyncio.fixture
async def printer_id(create_printer) -> int:
    return await create_printer(name="Cam", printer_type="elegoo_centauri",
                                connection_config={"ip_address": "192.168.1.20"})


def _cam(*, connected=True, camera=True, mjpeg="http://192.168.1.20:3031/video", rtsp=None) -> MagicMock:
    cam = MagicMock()
    cam.connected = connected
    cam.get_capabilities.return_value = MagicMock(camera=camera)
    cam.camera_mjpeg_url = mjpeg
    cam.camera_rtsp_url = rtsp
    return cam


async def test_snapshot_404_for_a_missing_printer(client):
    resp = await client.get("/api/v1/printers/999/snapshot")
    assert (resp.status_code, resp.json()["detail"]) == (404, "Printer 999 not found")


@pytest.mark.parametrize("clients, detail", [
    ({}, "Printer not connected"),                                 # no client registered at all
    ({"cam": lambda: _cam(connected=False)}, "Printer not connected"),
])
async def test_snapshot_503_until_the_printer_is_connected(client, printer_id, clients, detail):
    fake = None
    if clients:
        fake = clients["cam"]()
        printer_manager._clients[printer_id] = fake

    resp = await client.get(f"/api/v1/printers/{printer_id}/snapshot")

    assert (resp.status_code, resp.json()["detail"]) == (503, detail)
    if fake:
        fake.start_video_stream.assert_not_called()  # never wakes a disconnected camera


async def test_snapshot_404_when_the_printer_has_no_camera_and_nothing_is_started(client, printer_id):
    fake = _cam(camera=False)
    printer_manager._clients[printer_id] = fake

    resp = await client.get(f"/api/v1/printers/{printer_id}/snapshot")

    assert (resp.status_code, resp.json()["detail"]) == (404, "This printer has no camera")
    fake.start_video_stream.assert_not_called()


async def test_snapshot_returns_the_frame_as_an_uncached_jpeg_after_waking_the_camera(client, printer_id):
    fake = _cam()
    printer_manager._clients[printer_id] = fake
    order = []
    fake.start_video_stream.side_effect = lambda: order.append("activate")

    async def grab(c):
        order.append("grab")
        assert c is fake
        return JPEG

    with patch("app.api.routes.printers.grab_snapshot_from_client", grab):
        resp = await client.get(f"/api/v1/printers/{printer_id}/snapshot")

    assert resp.status_code == 200
    assert resp.content == JPEG
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "no-store"
    assert order == ["activate", "grab"]


async def test_snapshot_grabs_from_the_clients_mjpeg_url_end_to_end(client, printer_id):
    printer_manager._clients[printer_id] = _cam(mjpeg="http://192.168.1.20:3031/video")

    with patch("app.services.camera_proxy.grab_jpeg_frame", new=AsyncMock(return_value=JPEG)) as grab:
        resp = await client.get(f"/api/v1/printers/{printer_id}/snapshot")

    assert (resp.status_code, resp.content) == (200, JPEG)
    grab.assert_awaited_once_with("http://192.168.1.20:3031/video")


async def test_snapshot_404_when_the_camera_capable_printer_has_no_source(client, printer_id):
    printer_manager._clients[printer_id] = _cam(mjpeg=None, rtsp=None)

    resp = await client.get(f"/api/v1/printers/{printer_id}/snapshot")

    assert (resp.status_code, resp.json()["detail"]) == (404, "No camera source available")


@pytest.mark.parametrize("error", [ValueError("No complete JPEG frame found in stream"),
                                   TimeoutError("timed out"), FileNotFoundError("ffmpeg")])
async def test_snapshot_turns_grab_failures_into_503_with_the_reason(client, printer_id, error):
    printer_manager._clients[printer_id] = _cam()

    with patch("app.api.routes.printers.grab_snapshot_from_client", new=AsyncMock(side_effect=error)):
        resp = await client.get(f"/api/v1/printers/{printer_id}/snapshot")

    assert resp.status_code == 503
    assert resp.json()["detail"] == f"Camera unavailable: {error}"


async def test_snapshot_accepts_the_key_as_a_query_parameter_but_other_routes_do_not(client, printer_id):
    """<img src> cannot send headers, so /snapshot (only) honours ?key= — exercised on the real route."""
    raw = client.headers["X-Api-Key"]
    printer_manager._clients[printer_id] = _cam()
    remote = ASGITransport(app=app, client=("203.0.113.7", 50000))  # off the local network: no keyless admin

    async with AsyncClient(transport=remote, base_url="http://test") as anon:
        with patch("app.api.routes.printers.grab_snapshot_from_client", new=AsyncMock(return_value=JPEG)):
            no_key = await anon.get(f"/api/v1/printers/{printer_id}/snapshot")
            with_key = await anon.get(f"/api/v1/printers/{printer_id}/snapshot", params={"key": raw})
            bad_key = await anon.get(f"/api/v1/printers/{printer_id}/snapshot", params={"key": "thm_wrong"})
        other_route = await anon.get(f"/api/v1/printers/{printer_id}", params={"key": raw})

    assert (no_key.status_code, bad_key.status_code, other_route.status_code) == (401, 401, 401)
    assert (with_key.status_code, with_key.content) == (200, JPEG)
