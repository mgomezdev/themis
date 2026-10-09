"""AbstractPrinterClient camera defaults (BIZ-251)."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.abstract_printer_client import AbstractPrinterClient
from tests.services.fake_printer_plugin import FakeClient


def _make(mjpeg):
    class C(FakeClient):
        @property
        def camera_mjpeg_url(self):
            return mjpeg
    return C("192.0.2.1")


async def test_camera_stream_yields_the_mjpeg_proxy_chunks_for_the_url():
    seen = []

    async def fake(url):
        seen.append(url)
        yield b"x"
        yield b"y"

    with patch("app.services.camera_proxy.stream_mjpeg", fake):
        chunks = [c async for c in _make("http://cam/v").camera_stream()]
    assert chunks == [b"x", b"y"]
    assert seen == ["http://cam/v"]


async def test_camera_stream_without_url_raises_camera_unavailable():
    from app.services.abstract_printer_client import CameraUnavailable
    with pytest.raises(CameraUnavailable, match="No camera URL configured"):
        async for _ in _make(None).camera_stream():
            pass


def test_camera_unavailable_is_an_exception_carrying_its_message():
    from app.services.abstract_printer_client import CameraUnavailable
    assert issubclass(CameraUnavailable, Exception)
    assert str(CameraUnavailable("boom")) == "boom"


async def test_camera_snapshot_grabs_a_jpeg_frame_from_the_url():
    with patch("app.services.camera_proxy.grab_jpeg_frame", new=AsyncMock(return_value=b"jpg")) as grab:
        assert await _make("http://cam/v").camera_snapshot() == b"jpg"
    grab.assert_awaited_once_with("http://cam/v")


async def test_camera_snapshot_without_url_is_none_and_grabs_nothing():
    with patch("app.services.camera_proxy.grab_jpeg_frame", new=AsyncMock()) as grab:
        assert await _make(None).camera_snapshot() is None
    grab.assert_not_awaited()


@pytest.mark.parametrize("mjpeg", ["http://cam/v", None])
def test_camera_unavailable_reason_defaults_to_none(mjpeg):
    assert _make(mjpeg).camera_unavailable_reason() is None
    assert isinstance(_make(mjpeg), AbstractPrinterClient)
