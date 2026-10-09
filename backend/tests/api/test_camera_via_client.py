"""Camera routes delegate to the client's own camera_stream/camera_snapshot (BIZ-251)."""
import pytest
import pytest_asyncio

from app.services.camera_hub import multipart_part
from app.services.printer_manager import printer_manager

JPEG_A = b"\xff\xd8frame-a\xff\xd9"
JPEG_S = b"\xff\xd8snap\xff\xd9"


class _Caps:
    def __init__(self, camera):
        self.camera = camera


class FakeCamClient:
    def __init__(self, *, connected=True, camera=True, mjpeg=None, rtsp=None, reason=None,
                 snapshot=JPEG_S, snapshot_exc=None):
        self.connected = connected
        self._camera = camera
        self.camera_mjpeg_url = mjpeg
        self.camera_rtsp_url = rtsp
        self.reason = reason
        self.snapshot = snapshot
        self.snapshot_exc = snapshot_exc
        self.calls: list[str] = []

    def get_capabilities(self):
        return _Caps(self._camera)

    def start_video_stream(self):
        self.calls.append("start_video_stream")

    def camera_unavailable_reason(self):
        self.calls.append("reason")
        return self.reason

    async def camera_stream(self):
        self.calls.append("camera_stream")
        yield b"noise" + JPEG_A[:5]
        yield JPEG_A[5:]

    async def camera_snapshot(self):
        self.calls.append("camera_snapshot")
        if self.snapshot_exc:
            raise self.snapshot_exc
        return self.snapshot


@pytest_asyncio.fixture
async def printer_id(create_printer) -> int:
    return await create_printer(name="Cam", printer_type="elegoo_centauri",
                                connection_config={"ip_address": "192.168.1.20"})


async def test_camera_streams_the_clients_own_camera_stream(client, printer_id):
    fake = FakeCamClient(rtsp="rtsp://x/live")
    printer_manager._clients[printer_id] = fake
    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("multipart/x-mixed-replace; boundary=")
    assert resp.content == multipart_part(JPEG_A)
    assert "camera_stream" in fake.calls


@pytest.mark.parametrize("fake, status, detail", [
    (FakeCamClient(connected=False, mjpeg="http://c"), 503, "Printer not connected"),
    (FakeCamClient(camera=False, mjpeg="http://c"), 404, "This printer has no camera"),
    (FakeCamClient(), 404, "No camera URL configured"),
    (FakeCamClient(rtsp="rtsp://x", reason="ffmpeg not available for RTSP streaming"), 503,
     "ffmpeg not available for RTSP streaming"),
    (FakeCamClient(mjpeg="http://c", reason="camera is asleep"), 503, "camera is asleep"),
])
async def test_camera_error_responses_and_never_starts_the_stream(client, printer_id, fake, status, detail):
    printer_manager._clients[printer_id] = fake
    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")
    assert (resp.status_code, resp.json()["detail"]) == (status, detail)
    assert "camera_stream" not in fake.calls


async def test_snapshot_returns_the_clients_bytes_uncached(client, printer_id):
    fake = FakeCamClient(rtsp="rtsp://x/live")
    printer_manager._clients[printer_id] = fake
    resp = await client.get(f"/api/v1/printers/{printer_id}/snapshot")
    assert resp.status_code == 200
    assert resp.content == JPEG_S
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "no-store"
    assert "camera_snapshot" in fake.calls


async def test_snapshot_404_when_the_client_has_no_frame(client, printer_id):
    printer_manager._clients[printer_id] = FakeCamClient(snapshot=None)
    resp = await client.get(f"/api/v1/printers/{printer_id}/snapshot")
    assert (resp.status_code, resp.json()["detail"]) == (404, "No camera source available")


async def test_snapshot_503_when_the_client_raises(client, printer_id):
    printer_manager._clients[printer_id] = FakeCamClient(snapshot_exc=RuntimeError("lens fell off"))
    resp = await client.get(f"/api/v1/printers/{printer_id}/snapshot")
    assert resp.status_code == 503
    assert resp.json()["detail"].startswith("Camera unavailable")
