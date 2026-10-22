import pytest
import pytest_asyncio
from unittest.mock import MagicMock, patch
from app.services.abstract_printer_client import AbstractPrinterClient
from app.services.camera_hub import multipart_part
from app.services.printer_manager import printer_manager

JPEG_A = b"\xff\xd8frame-a\xff\xd9"


@pytest_asyncio.fixture
async def printer_id(create_printer) -> int:
    """An Elegoo printer registered with the manager but never connected (conftest stub)."""
    return await create_printer(name="Test", printer_type="elegoo_centauri",
                                connection_config={"ip_address": "192.168.1.20"})


def _camera_client(*, connected=True, camera=True, mjpeg=None, rtsp=None) -> MagicMock:
    client = MagicMock()
    client.connected = connected
    client.get_capabilities.return_value = MagicMock(camera=camera)
    client.camera_mjpeg_url = mjpeg
    client.camera_rtsp_url = rtsp
    client.camera_configured = AbstractPrinterClient.camera_configured.fget(client)   # the real base-class rule
    client.camera_unavailable_reason.return_value = None
    # the real base-class feed (the default MJPEG proxy), so these tests keep exercising the hub through the actual path
    client.camera_stream = lambda: AbstractPrinterClient.camera_stream(client)
    client.camera_snapshot = lambda: AbstractPrinterClient.camera_snapshot(client)
    return client


async def _empty_stream(url):
    return
    yield  # make it an async generator


async def test_camera_404_on_missing_printer(client):
    resp = await client.get("/api/v1/printers/999/camera")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Printer 999 not found"


async def test_camera_503_when_not_connected(client, printer_id):
    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Printer not connected"


async def test_camera_503_when_camera_capable_printer_is_disconnected_and_no_stream_is_started(client, printer_id):
    fake = _camera_client(connected=False, mjpeg="http://fake/stream")
    printer_manager._clients[printer_id] = fake

    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")

    assert resp.status_code == 503
    fake.start_video_stream.assert_not_called()


async def test_camera_404_when_no_camera_capability_and_no_stream_is_started(client, printer_id):
    fake = _camera_client(camera=False)
    printer_manager._clients[printer_id] = fake

    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "This printer has no camera"
    fake.start_video_stream.assert_not_called()


async def test_camera_404_when_the_printer_has_a_camera_but_no_url_configured(client, printer_id):
    printer_manager._clients[printer_id] = _camera_client()

    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "No camera URL configured"


async def test_camera_503_when_rtsp_needs_ffmpeg_and_it_is_missing(client, printer_id):
    fake = _camera_client(rtsp="rtsp://192.168.1.20/live")
    fake.camera_unavailable_reason.return_value = "ffmpeg not available for RTSP streaming"      # the vendor's own check
    printer_manager._clients[printer_id] = fake

    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")

    assert resp.status_code == 503
    assert resp.json()["detail"] == "ffmpeg not available for RTSP streaming"


async def test_camera_streams_mjpeg_as_multipart_after_activating_the_printer_stream(client, printer_id):
    fake = _camera_client(mjpeg="http://192.168.1.20:3031/video")
    printer_manager._clients[printer_id] = fake
    opened: list[str] = []

    async def stream(url):
        opened.append(url)
        yield b"junk-header\r\n" + JPEG_A[:6]          # a frame split across chunks, with noise before it
        yield JPEG_A[6:] + b"\r\n--frame\r\n"

    with patch("app.services.camera_proxy.stream_mjpeg", stream):
        resp = await client.get(f"/api/v1/printers/{printer_id}/camera")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
    assert resp.content == multipart_part(JPEG_A)          # re-framed whole frame, noise stripped
    assert opened == ["http://192.168.1.20:3031/video"]
    fake.start_video_stream.assert_called_once()


async def test_concurrent_viewers_of_one_printer_share_a_single_upstream_connection(client, printer_id):
    import asyncio
    printer_manager._clients[printer_id] = _camera_client(mjpeg="http://192.168.1.20:3031/video")
    opened, release = [], asyncio.Event()

    async def stream(url):
        opened.append(url)
        await release.wait()                       # hold until both viewers are attached
        yield JPEG_A

    async def viewer():
        return await client.get(f"/api/v1/printers/{printer_id}/camera")

    with patch("app.services.camera_proxy.stream_mjpeg", stream):
        t1, t2 = asyncio.create_task(viewer()), asyncio.create_task(viewer())
        from app.services import camera_hub
        for _ in range(200):
            if camera_hub.hub._streams.get(printer_id) and len(camera_hub.hub._streams[printer_id].subscribers) == 2:
                break
            await asyncio.sleep(0.01)
        release.set()
        r1, r2 = await asyncio.gather(t1, t2)

    assert len(opened) == 1
    assert r1.content == r2.content == multipart_part(JPEG_A)


async def test_camera_429_when_the_stream_cap_is_reached_for_another_printer(client, printer_id, monkeypatch):
    from app.services import camera_hub
    monkeypatch.setenv("THEMIS_MAX_CAMERA_STREAMS", "1")
    camera_hub.hub._streams[999] = camera_hub._Stream(999)          # some other printer already holds the only slot
    printer_manager._clients[printer_id] = _camera_client(mjpeg="http://192.168.1.20:3031/video")

    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")

    assert resp.status_code == 429


async def test_camera_pings_an_elegoo_style_stream_through_the_hub(client, printer_id, monkeypatch):
    from app.services import camera_hub
    monkeypatch.setattr(camera_hub, "KEEPALIVE_S", 0.01)
    fake = _camera_client(mjpeg="http://192.168.1.20:3031/video")
    printer_manager._clients[printer_id] = fake
    import asyncio

    async def stream(url):
        await asyncio.sleep(0.1)
        yield JPEG_A

    with patch("app.services.camera_proxy.stream_mjpeg", stream):
        await client.get(f"/api/v1/printers/{printer_id}/camera")

    assert fake.ping_video_stream.call_count >= 1


async def test_camera_stats_report_open_streams_and_snapshot_sharing(client, printer_id):
    from app.services import camera_hub
    fake = _camera_client(mjpeg="http://x/video")
    printer_manager._clients[printer_id] = fake

    async def grab():
        return JPEG_A

    fake.camera_snapshot = grab
    for _ in range(3):
        await client.get(f"/api/v1/printers/{printer_id}/snapshot")
    body = (await client.get("/api/v1/cameras/stats")).json()

    assert (body["snapshot_grabs"], body["snapshot_hits"], body["streams"], body["viewers"]) == (1, 2, [], 0)
    assert body["max_streams"] == camera_hub.max_streams()


async def test_camera_accepts_the_key_as_a_query_parameter_for_img_tags(client, printer_id):
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    raw = client.headers["X-Api-Key"]
    printer_manager._clients[printer_id] = _camera_client(mjpeg="http://x/video")

    async def stream(url):
        yield JPEG_A

    remote = ASGITransport(app=app, client=("203.0.113.7", 50000))
    async with AsyncClient(transport=remote, base_url="http://test") as anon:
        with patch("app.services.camera_proxy.stream_mjpeg", stream):
            no_key = await anon.get(f"/api/v1/printers/{printer_id}/camera")
            with_key = await anon.get(f"/api/v1/printers/{printer_id}/camera", params={"key": raw})

    assert (no_key.status_code, with_key.status_code) == (401, 200)


async def test_a_camera_that_fails_to_wake_is_an_error_response_not_an_empty_stream(client, printer_id):
    fake = _camera_client(mjpeg="http://192.168.1.20:3031/video")
    fake.start_video_stream.side_effect = RuntimeError("camera wake failed")
    printer_manager._clients[printer_id] = fake

    with pytest.raises(RuntimeError, match="camera wake failed"):
        await client.get(f"/api/v1/printers/{printer_id}/camera")        # surfaces as a 500 before any stream starts


async def test_a_second_viewer_does_not_wake_an_already_streaming_camera_again(client, printer_id):
    import asyncio
    from app.services import camera_hub
    fake = _camera_client(mjpeg="http://192.168.1.20:3031/video")
    printer_manager._clients[printer_id] = fake
    camera_hub.hub._streams[printer_id] = camera_hub._Stream(printer_id)     # someone is already watching

    async def stream(url):
        yield JPEG_A

    with patch("app.services.camera_proxy.stream_mjpeg", stream):
        task = asyncio.create_task(client.get(f"/api/v1/printers/{printer_id}/camera"))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    fake.start_video_stream.assert_not_called()
