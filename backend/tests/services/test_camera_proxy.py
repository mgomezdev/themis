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
