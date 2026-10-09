"""ffmpeg lives only in the vendor plugin (and config); core camera_proxy is HTTP-MJPEG only (BIZ-251)."""
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
PROXY = APP / "services" / "camera_proxy.py"
REMOVED = ["grab_rtsp_frame", "stream_rtsp_ffmpeg", "grab_snapshot_from_client", "get_ffmpeg_executable"]


def test_camera_proxy_has_no_rtsp_or_ffmpeg_code():
    src = PROXY.read_text(encoding="utf-8")
    assert [n for n in REMOVED if n in src] == []
    assert "ffmpeg" not in src.lower()
    assert "def stream_mjpeg" in src and "def grab_jpeg_frame" in src


def test_no_core_file_mentions_ffmpeg():
    offenders = []
    for p in APP.rglob("*.py"):
        rel = p.relative_to(APP)
        if rel.parts[0] == "plugins" or rel == Path("config.py"):
            continue
        if "ffmpeg" in p.read_text(encoding="utf-8", errors="ignore").lower():
            offenders.append(str(rel))
    assert offenders == []
