"""Tests for LaminusSidecarClient using httpx.MockTransport."""
from pathlib import Path

import httpx
import pytest

from app.services.laminus_sidecar_client import LaminusSidecarClient, SidecarError


def _client(transport: httpx.MockTransport) -> LaminusSidecarClient:
    c = LaminusSidecarClient.__new__(LaminusSidecarClient)
    c._client = httpx.Client(transport=transport, base_url="http://laminus:5000")
    return c


# ── health ─────────────────────────────────────────────────────────────────────

def test_health_ok():
    def handler(req):
        return httpx.Response(200, json={"status": "ok", "orcaslicer_version": "2.3.2"})

    c = _client(httpx.MockTransport(handler))
    assert c.health()["status"] == "ok"


def test_health_raises_on_non200():
    def handler(req):
        return httpx.Response(503, json={"detail": "starting"})

    c = _client(httpx.MockTransport(handler))
    with pytest.raises(SidecarError, match="503"):
        c.health()


# ── slice_prepared ─────────────────────────────────────────────────────────────

def test_slice_prepared_returns_job_id(tmp_path):
    prepared = tmp_path / "prepared.3mf"
    prepared.write_bytes(b"PK")

    def handler(req):
        assert req.url.path == "/api/slice/prepared"
        return httpx.Response(200, json={"job_id": "abc-123", "status": "pending"})

    c = _client(httpx.MockTransport(handler))
    assert c.slice_prepared(prepared, plate=1) == "abc-123"


def test_slice_prepared_raises_on_error(tmp_path):
    prepared = tmp_path / "prepared.3mf"
    prepared.write_bytes(b"PK")

    def handler(req):
        return httpx.Response(422, json={"detail": "bad file"})

    c = _client(httpx.MockTransport(handler))
    with pytest.raises(SidecarError, match="422"):
        c.slice_prepared(prepared, plate=1)


def test_slice_prepared_passes_export_3mf_flag(tmp_path):
    prepared = tmp_path / "mymodel.3mf"
    prepared.write_bytes(b"PK")
    captured: list[dict] = []

    def handler(req):
        captured.append({"body": req.content.decode("latin-1")})
        return httpx.Response(200, json={"job_id": "xyz"})

    c = _client(httpx.MockTransport(handler))
    c.slice_prepared(prepared, plate=2, export_3mf=True)
    assert "export_3mf" in captured[0]["body"]


# ── poll_status ────────────────────────────────────────────────────────────────

def test_poll_status_completes_on_second_poll():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(200, json={"status": "slicing", "sliced_file": None, "error": None})
        return httpx.Response(200, json={"status": "completed", "sliced_file": "plate_1.gcode", "error": None})

    c = _client(httpx.MockTransport(handler))
    result = c.poll_status("job1", poll_interval=0.0, timeout=10.0)
    assert result["status"] == "completed"
    assert result["sliced_file"] == "plate_1.gcode"
    assert calls["n"] == 2


def test_poll_status_raises_on_failed():
    def handler(req):
        return httpx.Response(200, json={"status": "failed", "sliced_file": None, "error": "oom"})

    c = _client(httpx.MockTransport(handler))
    with pytest.raises(SidecarError, match="oom"):
        c.poll_status("job1", poll_interval=0.0, timeout=5.0)


def test_poll_status_raises_on_timeout():
    def handler(req):
        return httpx.Response(200, json={"status": "slicing", "sliced_file": None, "error": None})

    c = _client(httpx.MockTransport(handler))
    with pytest.raises(SidecarError, match="timed out"):
        # timeout=0 expires immediately on first pending response
        c.poll_status("job1", poll_interval=0.0, timeout=0.0)


# ── download ───────────────────────────────────────────────────────────────────

def test_download_writes_file(tmp_path):
    gcode_bytes = b"G28\nG1 X0\n"

    def handler(req):
        assert "/api/slice/download/" in req.url.path
        return httpx.Response(200, content=gcode_bytes)

    c = _client(httpx.MockTransport(handler))
    dest = tmp_path / "plate_1.gcode"
    result = c.download("job1", dest)
    assert result == dest
    assert dest.read_bytes() == gcode_bytes


def test_download_raises_on_404():
    def handler(req):
        return httpx.Response(404, json={"detail": "not found"})

    c = _client(httpx.MockTransport(handler))
    with pytest.raises(SidecarError, match="404"):
        c.download("gone", Path("/tmp/out.gcode"))


# ── pack_stls ──────────────────────────────────────────────────────────────────

def test_pack_stls_returns_bytes(tmp_path):
    packed = b"PK\x03\x04 3mf-bytes"
    stl1 = tmp_path / "a.stl"
    stl2 = tmp_path / "b.stl"
    stl1.write_bytes(b"solid a\nendsolid a\n")
    stl2.write_bytes(b"solid b\nendsolid b\n")

    def handler(req):
        assert req.url.path == "/api/pack"
        return httpx.Response(200, content=packed)

    c = _client(httpx.MockTransport(handler))
    result = c.pack_stls([stl1, stl2], bed_x=220.0, bed_y=220.0)
    assert result == packed


def test_pack_stls_raises_on_error(tmp_path):
    stl = tmp_path / "a.stl"
    stl.write_bytes(b"solid a\nendsolid a\n")

    def handler(req):
        return httpx.Response(400, json={"detail": "slicer failed"})

    c = _client(httpx.MockTransport(handler))
    with pytest.raises(SidecarError, match="400"):
        c.pack_stls([stl], bed_x=100.0, bed_y=100.0)
