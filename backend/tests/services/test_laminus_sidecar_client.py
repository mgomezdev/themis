"""Tests for LaminusSidecarClient using httpx.MockTransport."""
from pathlib import Path

import httpx
import pytest

from app.services.providers.laminus.sidecar_client import LaminusSidecarClient, SidecarError
from app.services.providers.contract import ConstructInput


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
        assert req.url.path == "/api/v1/slice/prepared"
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
            return httpx.Response(200, json={"status": "slicing", "artifact": {"name": None, "available": False}, "error": None})
        assert req.url.path == "/api/v1/jobs/job1"
        return httpx.Response(200, json={"status": "completed", "artifact": {"name": "plate_1.gcode", "available": True}, "error": None})

    c = _client(httpx.MockTransport(handler))
    result = c.poll_status("job1", poll_interval=0.0, timeout=10.0)
    assert result["status"] == "completed"
    assert result["artifact"]["name"] == "plate_1.gcode"
    assert calls["n"] == 2


def test_poll_status_raises_on_failed():
    def handler(req):
        return httpx.Response(200, json={"status": "failed", "artifact": {"name": None, "available": False}, "error": "oom"})

    c = _client(httpx.MockTransport(handler))
    with pytest.raises(SidecarError, match="oom"):
        c.poll_status("job1", poll_interval=0.0, timeout=5.0)


def test_poll_status_raises_on_timeout():
    def handler(req):
        return httpx.Response(200, json={"status": "slicing", "artifact": {"name": None, "available": False}, "error": None})

    c = _client(httpx.MockTransport(handler))
    with pytest.raises(SidecarError, match="timed out"):
        # timeout=0 expires immediately on first pending response
        c.poll_status("job1", poll_interval=0.0, timeout=0.0)


# ── download ───────────────────────────────────────────────────────────────────

def test_download_writes_file(tmp_path):
    gcode_bytes = b"G28\nG1 X0\n"

    def handler(req):
        assert req.url.path == "/api/v1/jobs/job1/artifact"
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
        assert req.url.path == "/api/v1/pack"
        return httpx.Response(200, content=packed)

    c = _client(httpx.MockTransport(handler))
    result = c.pack_stls([ConstructInput(stl1, 1, "1"), ConstructInput(stl2, 1, "2")], bed_x=220.0, bed_y=220.0)
    assert result == packed


def test_pack_sends_each_stl_once_with_a_quantity_and_a_unique_name(tmp_path):
    import json, re
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    a1, a2 = tmp_path / "x" / "bracket.stl", tmp_path / "y" / "bracket.stl"   # same basename, different library files
    a1.write_bytes(b"solid 1\nendsolid 1\n")
    a2.write_bytes(b"solid 2\nendsolid 2\n")
    seen = {}

    def handler(req):
        body = req.content.decode("latin-1")
        seen["names"] = re.findall(r'name="files"; filename="([^"]+)"', body)
        seen["items"] = json.loads(re.search(r'name="items"\r\n\r\n(.*?)\r\n--', body, re.S).group(1))
        return httpx.Response(200, content=b"PACKED")

    c = _client(httpx.MockTransport(handler))
    assert c.pack_stls([ConstructInput(a1, 5, "7"), ConstructInput(a2, 1, "9")], 220.0, 220.0) == b"PACKED"
    assert seen["names"] == ["7_bracket.stl", "9_bracket.stl"]            # 5 copies, but one upload each; names unique
    assert seen["items"] == [{"file": "7_bracket.stl", "quantity": 5}, {"file": "9_bracket.stl", "quantity": 1}]


def test_pack_upload_names_drop_characters_the_sidecar_cannot_match(tmp_path):
    import json, re
    from app.services.providers.laminus.sidecar_client import _UNSAFE_UPLOAD_CHARS
    # quotes can't be Windows filenames, so the sanitizer is checked directly with one
    assert _UNSAFE_UPLOAD_CHARS.sub("_", "a" + chr(34) + "b;c" + chr(10) + "d" + chr(13) + "e" + chr(0) + "f" + chr(9) + "g" + chr(127) + "h") == "a_b_c_d_e_f_g_h"
    stl = tmp_path / "my; part.stl"
    stl.write_bytes(b"solid x\nendsolid x\n")
    seen = {}

    def handler(req):
        body = req.content.decode("latin-1")
        seen["names"] = re.findall(r'name="files"; filename="([^"]+)"', body)
        seen["items"] = json.loads(re.search(r'name="items"\r\n\r\n(.*?)\r\n--', body, re.S).group(1))
        return httpx.Response(200, content=b"PACKED")

    c = _client(httpx.MockTransport(handler))
    assert c.pack_stls([ConstructInput(stl, 2, "4")], bed_x=100.0, bed_y=100.0) == b"PACKED"
    assert seen["names"] == ["4_my_ part.stl"]
    assert seen["items"] == [{"file": "4_my_ part.stl", "quantity": 2}]   # same sanitized name on the wire and in items


def test_pack_stls_raises_on_error(tmp_path):
    stl = tmp_path / "a.stl"
    stl.write_bytes(b"solid a\nendsolid a\n")

    def handler(req):
        return httpx.Response(400, json={"detail": "slicer failed"})

    c = _client(httpx.MockTransport(handler))
    with pytest.raises(SidecarError, match="400"):
        c.pack_stls([ConstructInput(stl, 1, "1")], bed_x=100.0, bed_y=100.0)


# ── v1 contract ────────────────────────────────────────────────────────────────

def test_slice_start_posts_a_job_and_returns_its_id(tmp_path):
    model = tmp_path / "m.3mf"
    model.write_bytes(b"PK")

    def handler(req):
        assert (req.method, req.url.path) == ("POST", "/api/v1/jobs")
        return httpx.Response(202, json={"id": "job-9", "status": "pending"})

    c = _client(httpx.MockTransport(handler))
    assert c.slice_start(model, "m", "p", ["f"], plate=1) == "job-9"


def test_completed_job_without_an_artifact_is_an_error():
    def handler(req):
        return httpx.Response(200, json={"status": "completed", "artifact": {"name": None, "available": False}})

    c = _client(httpx.MockTransport(handler))
    with pytest.raises(SidecarError, match="without an artifact"):
        c.poll_status("job1", poll_interval=0.0, timeout=5.0)


def test_get_catalog_walks_every_page_and_groups_by_type():
    seen: list[str | None] = []

    def handler(req):
        assert req.url.path == "/api/v1/profiles"
        cursor = req.url.params.get("cursor")
        seen.append(cursor)
        assert req.url.params["limit"] == "200"
        if cursor is None:
            return httpx.Response(200, json={"data": [{"type": "machine", "uuid": "m1", "name": "M"},
                                                      {"type": "process", "uuid": "p1", "name": "P"}],
                                             "page": {"limit": 200, "next_cursor": "c2"}})
        return httpx.Response(200, json={"data": [{"type": "filament", "uuid": "f1", "name": "F"}],
                                         "page": {"limit": 200, "next_cursor": None}})

    cat = _client(httpx.MockTransport(handler)).get_catalog()
    assert seen == [None, "c2"]
    assert [e["uuid"] for e in cat["machine"]] == ["m1"]
    assert [e["uuid"] for e in cat["process"]] == ["p1"]
    assert [e["uuid"] for e in cat["filament"]] == ["f1"]


def test_errors_surface_the_v1_envelope_code_and_message():
    def handler(req):
        return httpx.Response(503, json={"error": {"code": "catalog_not_ready", "message": "Retry shortly.",
                                                   "status": 503}, "request_id": "r"})

    with pytest.raises(SidecarError, match="catalog_not_ready: Retry shortly."):
        _client(httpx.MockTransport(handler)).get_catalog()
