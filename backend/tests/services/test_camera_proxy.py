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
