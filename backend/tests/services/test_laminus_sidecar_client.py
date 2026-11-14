"""Tests for LaminusSidecarClient using httpx.MockTransport."""
from pathlib import Path

import httpx
import pytest

from app.services.providers.laminus.sidecar_client import LaminusSidecarClient, SidecarError


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


# ── pack_stls: counts / labels / capability discovery ──────────────────────────

import json as _json
import re as _re


def _sidecar(features=("pack.counts",), *, root=None):
    """A sidecar double: `GET /api/v1` advertises `features` (or `root(req)` answers instead); POST /api/v1/pack is recorded."""
    seen = {"gets": [], "posts": []}

    def handler(req):
        if req.method == "GET" and req.url.path == "/api/v1":
            seen["gets"].append(req)
            return root(req) if root else httpx.Response(200, json={"features": list(features)})
        if req.url.path == "/api/v1/pack":
            seen["posts"].append(req)
            return httpx.Response(200, content=b"PACKED")
        return httpx.Response(404)

    return _client(httpx.MockTransport(handler)), seen


def _form(req):
    """(upload filenames in order, {field: value}) of a recorded multipart request."""
    body = req.content.decode("latin-1")
    files = _re.findall(r'name="files"; filename="([^"]*)"', body)
    fields = dict(_re.findall(r'name="([^"]+)"\r\n\r\n([^\r]*)\r\n', body))
    return files, fields


def _stls(tmp_path, *names):
    out = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(f"solid {n}\nendsolid".encode())
        out.append(p)
    return out


def test_counts_with_pack_counts_upload_each_file_once_and_send_the_counts(tmp_path):
    c, seen = _sidecar()
    a, b = _stls(tmp_path, "a.stl", "b.stl")

    assert c.pack_stls([a, b], 220, 220, counts=[3, 2], labels=["a__m0", "b__m1"]) == b"PACKED"

    (post,) = seen["posts"]
    files, fields = _form(post)
    assert files == ["a__m0.stl", "b__m1.stl"]
    assert _json.loads(fields["counts"]) == [3, 2]
    assert (fields["bed_x"], fields["bed_y"]) == ("220", "220")


def test_counts_without_pack_counts_upload_every_copy_and_send_no_counts_field(tmp_path):
    c, seen = _sidecar(features=())
    a, b = _stls(tmp_path, "a.stl", "b.stl")

    c.pack_stls([a, b], 220, 220, counts=[3, 2], labels=["a__m0", "b__m1"])

    files, fields = _form(seen["posts"][0])
    assert files == ["a__m0.stl"] * 3 + ["b__m1.stl"] * 2
    assert "counts" not in fields


@pytest.mark.parametrize("root", [
    lambda req: httpx.Response(404),
    lambda req: httpx.Response(500, json={"features": ["pack.counts"]}),
    lambda req: httpx.Response(200, content=b"<html>not json</html>"),
    lambda req: httpx.Response(200, json=["pack.counts"]),
    lambda req: httpx.Response(200, json={"features": "pack.counts"}),
    lambda req: (_ for _ in ()).throw(httpx.ConnectError("down")),
], ids=["404", "500", "non-json", "json-list", "features-not-a-list", "connect-error"])
def test_an_unusable_capability_endpoint_counts_as_not_advertised(tmp_path, root):
    c, seen = _sidecar(root=root)
    (a,) = _stls(tmp_path, "a.stl")

    c.pack_stls([a], 220, 220, counts=[2])

    files, fields = _form(seen["posts"][0])
    assert files == ["a.stl", "a.stl"] and "counts" not in fields


def test_more_than_fifty_copies_without_pack_counts_is_refused_before_any_post(tmp_path):
    c, seen = _sidecar(features=())
    a, b = _stls(tmp_path, "a.stl", "b.stl")

    with pytest.raises(SidecarError, match="50"):
        c.pack_stls([a, b], 220, 220, counts=[30, 21])

    assert seen["posts"] == []


def test_exactly_fifty_copies_without_pack_counts_still_go_out(tmp_path):
    c, seen = _sidecar(features=())
    (a,) = _stls(tmp_path, "a.stl")

    c.pack_stls([a], 220, 220, counts=[50])

    assert len(_form(seen["posts"][0])[0]) == 50


def test_more_than_fifty_copies_are_fine_when_pack_counts_is_advertised(tmp_path):
    c, seen = _sidecar()
    (a,) = _stls(tmp_path, "a.stl")

    c.pack_stls([a], 220, 220, counts=[200])

    files, fields = _form(seen["posts"][0])
    assert files == ["a.stl"] and _json.loads(fields["counts"]) == [200]


def test_all_counts_of_one_never_ask_for_capabilities(tmp_path):
    c, seen = _sidecar()
    a, b = _stls(tmp_path, "a.stl", "b.stl")

    c.pack_stls([a, b], 220, 220, counts=[1, 1], labels=["x__m0", "y__m1"])
    c.pack_stls([a, b], 220, 220)

    assert seen["gets"] == []
    for post in seen["posts"]:
        assert "counts" not in _form(post)[1]
    assert [_form(p)[0] for p in seen["posts"]] == [["x__m0.stl", "y__m1.stl"], ["a.stl", "b.stl"]]


@pytest.mark.parametrize("counts,labels", [
    ([1], None),                  # fewer counts than models
    ([1, 1, 1], None),            # more counts than models
    ([2, 0], None),               # a zero count
    ([2, -1], None),              # a negative count
    ([1, 1], ["only-one"]),       # labels do not match the models
])
def test_mismatched_or_non_positive_counts_and_labels_are_refused(tmp_path, counts, labels):
    c, seen = _sidecar()
    a, b = _stls(tmp_path, "a.stl", "b.stl")

    with pytest.raises(SidecarError):
        c.pack_stls([a, b], 220, 220, counts=counts, labels=labels)

    assert seen["posts"] == []


def test_uuid_mode_passes_counts_labels_and_presets_too(tmp_path):
    c, seen = _sidecar()
    a, b = _stls(tmp_path, "a.stl", "b.stl")

    c.pack_stls_by_uuid([a, b], "m-1", "p-1", ["f-1"], counts=[4, 1], labels=["a__m0", "b__m1"])

    files, fields = _form(seen["posts"][0])
    assert files == ["a__m0.stl", "b__m1.stl"]
    assert _json.loads(fields["counts"]) == [4, 1]
    assert (fields["machine_uuid"], fields["process_uuid"]) == ("m-1", "p-1")
    assert _json.loads(fields["filament_uuids"]) == ["f-1"]


def test_uuid_mode_without_pack_counts_repeats_the_uploads(tmp_path):
    c, seen = _sidecar(features=())
    (a,) = _stls(tmp_path, "a.stl")

    c.pack_stls_by_uuid([a], "m-1", "p-1", [], counts=[3], labels=["a__m0"])

    files, fields = _form(seen["posts"][0])
    assert files == ["a__m0.stl"] * 3 and "counts" not in fields


def test_without_labels_the_upload_keeps_the_file_name(tmp_path):
    c, seen = _sidecar()
    (a,) = _stls(tmp_path, "part one.stl")

    c.pack_stls([a], 220, 220, counts=[2])

    assert _form(seen["posts"][0])[0] == ["part one.stl"]


def test_features_reads_the_advertised_flags_and_drops_non_strings():
    c, _ = _sidecar(root=lambda req: httpx.Response(200, json={"features": ["pack.counts", 7, None, "x"]}))
    assert c.features() == frozenset({"pack.counts", "x"})


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
