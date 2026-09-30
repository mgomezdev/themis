import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_stream_mjpeg_yields_chunks():
    from app.services.camera_proxy import stream_mjpeg

    async def fake_aiter_bytes(chunk_size=None):
        yield b"chunk1"
        yield b"chunk2"

    mock_response = MagicMock()
    mock_response.aiter_bytes = fake_aiter_bytes
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream.return_value.__aenter__ = AsyncMock(return_value=mock_response)
    mock_client.stream.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.camera_proxy.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        chunks = []
        async for chunk in stream_mjpeg("http://fake/stream"):
            chunks.append(chunk)

    assert chunks == [b"chunk1", b"chunk2"]


@pytest.mark.asyncio
async def test_stream_rtsp_ffmpeg_yields_stdout():
    from app.services.camera_proxy import stream_rtsp_ffmpeg

    call_count = 0

    async def fake_read(n):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return b"ffmpeg_data"
        return b""

    mock_proc = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stdout.read = fake_read
    mock_proc.returncode = None
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("app.services.camera_proxy.asyncio.create_subprocess_exec",
               new_callable=AsyncMock, return_value=mock_proc):
        with patch("app.services.camera_proxy.get_ffmpeg_executable", return_value="ffmpeg"):
            chunks = []
            async for chunk in stream_rtsp_ffmpeg("rtsps://fake/stream"):
                chunks.append(chunk)

    assert b"ffmpeg_data" in chunks


@pytest.mark.asyncio
async def test_stream_rtsp_ffmpeg_kills_process_on_completion():
    from app.services.camera_proxy import stream_rtsp_ffmpeg

    async def fake_read(n):
        return b""

    mock_proc = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stdout.read = fake_read
    mock_proc.returncode = None
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("app.services.camera_proxy.asyncio.create_subprocess_exec",
               new_callable=AsyncMock, return_value=mock_proc):
        with patch("app.services.camera_proxy.get_ffmpeg_executable", return_value="ffmpeg"):
            async for _ in stream_rtsp_ffmpeg("rtsps://fake/stream"):
                pass

    mock_proc.kill.assert_called_once()
    mock_proc.wait.assert_called_once()


# ---------------------------------------------------------------------------
# grab_jpeg_frame — first complete JPEG out of an MJPEG stream
# ---------------------------------------------------------------------------

import httpx

_SOI, _EOI = b"\xff\xd8", b"\xff\xd9"


class _Chunks(httpx.AsyncByteStream):
    """A response body that arrives in exactly these chunks."""
    def __init__(self, chunks):
        self._chunks = chunks

    async def __aiter__(self):
        for c in self._chunks:
            yield c


@pytest.fixture
def mjpeg_upstream():
    """Route camera_proxy's httpx client to a canned chunked body; `up.kwargs` records how the client was built."""
    from types import SimpleNamespace
    real_client = httpx.AsyncClient
    up = SimpleNamespace(chunks=[], kwargs=None, requested=[])

    def handler(request: httpx.Request) -> httpx.Response:
        up.requested.append(str(request.url))
        return httpx.Response(200, stream=_Chunks(up.chunks))

    def factory(*args, **kwargs):
        up.kwargs = kwargs
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    with patch("app.services.camera_proxy.httpx.AsyncClient", factory):
        yield up


async def test_grab_jpeg_frame_returns_exactly_the_first_frame_even_when_split_across_chunks(mjpeg_upstream):
    from app.services.camera_proxy import grab_jpeg_frame
    frame = _SOI + b"jpeg-body" + _EOI
    mjpeg_upstream.chunks = [b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame[:4], frame[4:] + b"\r\n--frame\r\n" + frame]

    assert await grab_jpeg_frame("http://cam/stream") == frame  # no multipart framing, no second frame
    assert mjpeg_upstream.requested == ["http://cam/stream"]


async def test_grab_jpeg_frame_ignores_an_end_marker_that_precedes_the_start_marker(mjpeg_upstream):
    from app.services.camera_proxy import grab_jpeg_frame
    frame = _SOI + b"real" + _EOI
    mjpeg_upstream.chunks = [b"junk" + _EOI + b"more-junk" + frame]

    assert await grab_jpeg_frame("http://cam/stream") == frame


async def test_grab_jpeg_frame_fails_when_the_stream_ends_without_a_complete_frame(mjpeg_upstream):
    from app.services.camera_proxy import grab_jpeg_frame
    mjpeg_upstream.chunks = [_SOI + b"truncated"]

    with pytest.raises(ValueError, match="No complete JPEG frame found in stream"):
        await grab_jpeg_frame("http://cam/stream")


async def test_grab_jpeg_frame_gives_up_on_a_frame_larger_than_two_megabytes(mjpeg_upstream):
    from app.services.camera_proxy import grab_jpeg_frame, _MAX_SNAPSHOT_BYTES
    mjpeg_upstream.chunks = [_SOI] + [b"x" * 500_000] * (_MAX_SNAPSHOT_BYTES // 500_000 + 1)

    with pytest.raises(ValueError, match="exceeds size limit"):
        await grab_jpeg_frame("http://cam/stream")


async def test_grab_jpeg_frame_uses_the_default_or_given_timeout(mjpeg_upstream):
    from app.services.camera_proxy import grab_jpeg_frame
    mjpeg_upstream.chunks = [_SOI + _EOI]

    await grab_jpeg_frame("http://cam/stream")
    assert mjpeg_upstream.kwargs["timeout"] == 8.0
    await grab_jpeg_frame("http://cam/stream", timeout=2.5)
    assert mjpeg_upstream.kwargs["timeout"] == 2.5


# ---------------------------------------------------------------------------
# grab_rtsp_frame — one frame via ffmpeg
# ---------------------------------------------------------------------------

def _proc(stdout=b"", returncode=0, communicate=None):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = communicate or AsyncMock(return_value=(stdout, b""))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


async def test_grab_rtsp_frame_runs_ffmpeg_for_one_mjpeg_frame_and_returns_its_stdout():
    from app.services.camera_proxy import grab_rtsp_frame
    proc = _proc(stdout=_SOI + b"frame" + _EOI)

    with patch("app.services.camera_proxy.asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as spawn, \
         patch("app.services.camera_proxy.get_ffmpeg_executable", return_value="/opt/ffmpeg"):
        frame = await grab_rtsp_frame("rtsps://cam/live")

    assert frame == _SOI + b"frame" + _EOI
    assert spawn.call_args.args == ("/opt/ffmpeg", "-rtsp_transport", "tcp", "-i", "rtsps://cam/live",
                                    "-vframes", "1", "-f", "image2", "-vcodec", "mjpeg", "pipe:1")


@pytest.mark.parametrize("stdout, returncode", [(b"partial", 1), (b"", 0)])
async def test_grab_rtsp_frame_fails_on_a_nonzero_exit_or_empty_output(stdout, returncode):
    from app.services.camera_proxy import grab_rtsp_frame

    with patch("app.services.camera_proxy.asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=_proc(stdout=stdout, returncode=returncode))):
        with pytest.raises(ValueError, match=rf"ffmpeg RTSP grab failed \(exit {returncode}\)"):
            await grab_rtsp_frame("rtsp://cam/live")


async def test_grab_rtsp_frame_kills_a_hung_ffmpeg_and_reports_the_timeout():
    from app.services.camera_proxy import grab_rtsp_frame

    async def hang():
        await asyncio.sleep(30)

    proc = _proc(returncode=None, communicate=hang)
    with patch("app.services.camera_proxy.asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        with pytest.raises(ValueError, match="RTSP frame grab timed out"):
            await grab_rtsp_frame("rtsp://cam/live", timeout=0.01)

    proc.kill.assert_called_once()
    proc.wait.assert_awaited()


async def test_grab_rtsp_frame_propagates_a_missing_ffmpeg_binary():
    from app.services.camera_proxy import grab_rtsp_frame

    with patch("app.services.camera_proxy.asyncio.create_subprocess_exec",
               new=AsyncMock(side_effect=FileNotFoundError("ffmpeg"))):
        with pytest.raises(FileNotFoundError):
            await grab_rtsp_frame("rtsp://cam/live")


# ---------------------------------------------------------------------------
# grab_snapshot_from_client — source selection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mjpeg, rtsp, expected", [
    ("http://cam/mjpeg", "rtsp://cam/live", "mjpeg-frame"),   # MJPEG wins when both exist
    (None, "rtsp://cam/live", "rtsp-frame"),
    ("http://cam/mjpeg", None, "mjpeg-frame"),
    (None, None, None),
])
async def test_grab_snapshot_from_client_picks_the_source(mjpeg, rtsp, expected):
    from app.services.camera_proxy import grab_snapshot_from_client
    client = MagicMock(camera_mjpeg_url=mjpeg, camera_rtsp_url=rtsp)

    with patch("app.services.camera_proxy.grab_jpeg_frame", new=AsyncMock(return_value="mjpeg-frame")) as m, \
         patch("app.services.camera_proxy.grab_rtsp_frame", new=AsyncMock(return_value="rtsp-frame")) as r:
        result = await grab_snapshot_from_client(client)

    assert result == expected
    if mjpeg:
        m.assert_awaited_once_with(mjpeg)
        r.assert_not_called()
    elif rtsp:
        r.assert_awaited_once_with(rtsp)
        m.assert_not_called()
    else:
        m.assert_not_called()
        r.assert_not_called()
