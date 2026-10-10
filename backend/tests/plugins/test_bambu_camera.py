"""Bambu plugin owns its RTSP/ffmpeg camera feed (BIZ-251)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.plugins.bambu.client import BambuMQTTClient

_SOI, _EOI = b"\xff\xd8", b"\xff\xd9"
SPAWN = "app.plugins.bambu.camera.asyncio.create_subprocess_exec"
EXE = "app.plugins.bambu.camera.get_ffmpeg_executable"
RTSP = "rtsps://cam/live"


def _proc(stdout=b"", returncode=0, communicate=None):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = communicate or AsyncMock(return_value=(stdout, b""))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


def _stream_proc(chunks):
    it = iter(chunks)

    async def read(n):
        return next(it, b"")

    proc = MagicMock()
    proc.stdout.read = read
    proc.returncode = None
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


async def test_grab_rtsp_frame_runs_ffmpeg_for_one_mjpeg_frame_and_returns_its_stdout():
    from app.plugins.bambu.camera import grab_rtsp_frame
    proc = _proc(stdout=_SOI + b"frame" + _EOI)
    with patch(SPAWN, new=AsyncMock(return_value=proc)) as spawn, patch(EXE, return_value="/opt/ffmpeg"):
        frame = await grab_rtsp_frame(RTSP)
    assert frame == _SOI + b"frame" + _EOI
    assert spawn.call_args.args == ("/opt/ffmpeg", "-rtsp_transport", "tcp", "-i", RTSP,
                                    "-vframes", "1", "-f", "image2", "-vcodec", "mjpeg", "pipe:1")


@pytest.mark.parametrize("stdout, returncode", [(b"partial", 1), (b"", 0)])
async def test_grab_rtsp_frame_fails_on_a_nonzero_exit_or_empty_output(stdout, returncode):
    from app.plugins.bambu.camera import grab_rtsp_frame
    with patch(SPAWN, new=AsyncMock(return_value=_proc(stdout=stdout, returncode=returncode))):
        with pytest.raises(ValueError, match=rf"ffmpeg RTSP grab failed \(exit {returncode}\)"):
            await grab_rtsp_frame(RTSP)


async def test_grab_rtsp_frame_kills_a_hung_ffmpeg_and_reports_the_timeout():
    from app.plugins.bambu.camera import grab_rtsp_frame

    async def hang():
        await asyncio.sleep(30)

    proc = _proc(returncode=None, communicate=hang)
    with patch(SPAWN, new=AsyncMock(return_value=proc)):
        with pytest.raises(ValueError, match="RTSP frame grab timed out"):
            await grab_rtsp_frame(RTSP, timeout=0.01)
    proc.kill.assert_called_once()
    proc.wait.assert_awaited()


async def test_grab_rtsp_frame_propagates_a_missing_ffmpeg_binary():
    from app.plugins.bambu.camera import grab_rtsp_frame
    with patch(SPAWN, new=AsyncMock(side_effect=FileNotFoundError("ffmpeg"))):
        with pytest.raises(FileNotFoundError):
            await grab_rtsp_frame(RTSP)


async def test_stream_rtsp_ffmpeg_yields_stdout_then_kills_the_process():
    from app.plugins.bambu.camera import stream_rtsp_ffmpeg
    proc = _stream_proc([b"ffmpeg_data"])
    with patch(SPAWN, new=AsyncMock(return_value=proc)), patch(EXE, return_value="ffmpeg"):
        chunks = [c async for c in stream_rtsp_ffmpeg(RTSP)]
    assert chunks == [b"ffmpeg_data"]
    proc.kill.assert_called_once()
    proc.wait.assert_called_once()


@pytest.mark.parametrize("found, expected", [("/usr/bin/ffmpeg", True), (None, False)])
def test_ffmpeg_available_reflects_which(found, expected):
    from app.plugins.bambu import camera
    with patch(EXE, return_value="ffmpeg"), patch("app.plugins.bambu.camera.shutil.which", return_value=found) as which:
        assert camera.ffmpeg_available() is expected
    which.assert_called_once_with("ffmpeg")


# --- Bambu client overrides -------------------------------------------------
# The real Bambu client always exposes an rtsp url (built from ip + access code).

def _bambu():
    return BambuMQTTClient(ip_address="192.0.2.7", serial_number="S", access_code="1")


class _NoRtspBambu(BambuMQTTClient):
    @property
    def camera_rtsp_url(self):
        return None


def _no_rtsp():
    return _NoRtspBambu(ip_address="192.0.2.7", serial_number="S", access_code="1")


async def test_bambu_camera_stream_yields_the_rtsp_ffmpeg_chunks():
    c = _bambu()
    seen = []

    async def fake_stream(url):
        seen.append(url)
        yield b"a"
        yield b"b"

    with patch("app.plugins.bambu.camera.stream_rtsp_ffmpeg", fake_stream):
        chunks = [x async for x in c.camera_stream()]
    assert chunks == [b"a", b"b"]
    assert seen == [c.camera_rtsp_url]


async def test_bambu_camera_snapshot_grabs_an_rtsp_frame():
    c = _bambu()
    with patch("app.plugins.bambu.camera.grab_rtsp_frame", new=AsyncMock(return_value=b"jpg")) as grab:
        assert await c.camera_snapshot() == b"jpg"
    grab.assert_awaited_once_with(c.camera_rtsp_url)


@pytest.mark.parametrize("available, reason", [(False, "ffmpeg not available for RTSP streaming"), (True, None)])
def test_bambu_unavailable_reason_follows_ffmpeg_presence(available, reason):
    with patch("app.plugins.bambu.camera.ffmpeg_available", return_value=available):
        assert _bambu().camera_unavailable_reason() == reason


async def test_bambu_without_rtsp_url_behaves_like_the_base_class():
    from app.services.abstract_printer_client import CameraUnavailable
    c = _no_rtsp()
    with patch("app.plugins.bambu.camera.ffmpeg_available", return_value=False):
        assert c.camera_unavailable_reason() is None
    assert await c.camera_snapshot() is None
    with pytest.raises(CameraUnavailable, match="No camera URL configured"):
        async for _ in c.camera_stream():
            pass
