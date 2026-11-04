"""BIZ-296: Laminus as the neutral `ProjectConstructor` (`3mf.construct`): packs STLs by the target's bed over a fake sidecar."""
from __future__ import annotations

import httpx
import pytest

from app.services.providers import contract as c
from app.services.providers.laminus.connection import PLUGIN_VERSION, LaminusConnection, LaminusSettings
from app.services.providers.laminus.constructor import LaminusProjectConstructor
from tests.services.test_slicing_provider_contract import URL, sidecar  # noqa: F401  (fixture)

BED = (120.0, 130.0, 140.0)


def constructor() -> LaminusProjectConstructor:
    return LaminusProjectConstructor(URL, version="9.9")


def _stl(tmp_path, name="a.stl"):
    p = tmp_path / name
    p.write_bytes(b"solid x\nendsolid x\n")
    return p


def _req(tmp_path, *inputs, bed=BED):
    return c.ConstructRequest(inputs or (c.ConstructInput(_stl(tmp_path), 2, "7"),), c.TargetPrinter(bed))


def _construct_error(fn) -> c.ErrorCode:
    with pytest.raises(c.ProviderError) as e:
        fn()
    return e.value.code


def test_identity_names_the_plugin():
    ident = constructor().identity
    assert (ident.provider_id, ident.version) == ("laminus", "9.9")


def test_packs_by_the_target_bed_and_writes_one_project(sidecar, tmp_path):  # noqa: F811
    out = tmp_path / "out"
    out.mkdir()
    art = constructor().construct(_req(tmp_path), out)
    assert art.kind is c.ArtifactKind.PROJECT_3MF and art.produced_by.provider_id == "laminus"
    assert art.path.parent == out and art.path.read_bytes() == b"PACKED"
    (req,) = [r for r in sidecar.requests if r.url.path == "/api/v1/pack"]
    body = req.content.decode(errors="ignore")
    for needle in ('name="bed_x"\r\n\r\n120.0', 'name="bed_y"\r\n\r\n130.0', 'name="bed_z"\r\n\r\n140.0',
                   '"file": "7_a.stl", "quantity": 2', 'filename="7_a.stl"'):
        assert needle in body
    assert "machine_uuid" not in body


def test_the_shorter_of_the_call_timeout_and_the_plugin_request_timeout_reaches_the_http_client(sidecar, tmp_path):  # noqa: F811
    constructor().construct(_req(tmp_path), tmp_path, timeout=42.0)
    assert sidecar.client_kwargs[-1]["timeout"] == 42.0
    LaminusProjectConstructor(URL, version="9.9", request_timeout_s=30.0).construct(_req(tmp_path), tmp_path, timeout=42.0)
    assert sidecar.client_kwargs[-1]["timeout"] == 30.0


@pytest.mark.parametrize("bed", [(0.0, 130.0, 140.0), (120.0, -1.0, 140.0), (120.0, 130.0, 0.0)])
def test_a_bed_without_a_positive_size_is_an_unsupported_target_and_nothing_is_sent(sidecar, tmp_path, bed):  # noqa: F811
    assert _construct_error(lambda: constructor().construct(_req(tmp_path, bed=bed), tmp_path)) is c.ErrorCode.UNSUPPORTED_TARGET
    assert sidecar.requests == []


def test_3mf_objects_plates_and_rules_go_to_the_mixed_pack(sidecar, tmp_path):  # noqa: F811
    """BIZ-356: anything but plain split STLs is packed by /pack/mixed, each source uploaded once, the selection in input order."""
    import json as _json
    proj = tmp_path / "p.3mf"
    proj.write_bytes(b"PK")
    stl = _stl(tmp_path)
    req = _req(tmp_path, c.ConstructInput(stl, 2, "1"), c.ConstructInput(proj, 3, "item-5", object_id="8"),
               c.ConstructInput(proj, 1, "item-6", plate=2, rule="together"), c.ConstructInput(stl, 1, "item-7", rule="solo"))
    art = constructor().construct(req, tmp_path)
    assert art.path.read_bytes() == b"MIXED"
    (call,) = [r for r in sidecar.requests if r.url.path == "/api/v1/pack/mixed"]
    body = call.content.decode("latin-1")
    sel = _json.loads(body.split('name="selection"')[1].split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0])
    assert sel == [{"file": "0_a.stl", "qty": 2}, {"file": "1_p.3mf", "qty": 3, "id": 8},
                   {"file": "1_p.3mf", "qty": 1, "plate": 2, "rule": "together"}, {"file": "0_a.stl", "qty": 1, "rule": "solo"}]
    assert body.count('name="files"') == 2
    assert 'name="bed_x"' in body


def test_plain_split_stls_still_use_pack(sidecar, tmp_path):  # noqa: F811
    constructor().construct(_req(tmp_path), tmp_path)
    assert [r.url.path for r in sidecar.requests] == ["/api/v1/pack"]


def test_list_objects_reads_the_sidecar_inspect(sidecar, tmp_path):  # noqa: F811
    proj = tmp_path / "p.3mf"
    proj.write_bytes(b"PK")
    assert constructor().list_objects(proj) == [c.ModelObject("3", "Gear", 2), c.ModelObject("8", "Axle", 1)]


def test_no_inputs_is_unsupported(sidecar, tmp_path):  # noqa: F811
    req = c.ConstructRequest((), c.TargetPrinter(BED))
    assert _construct_error(lambda: constructor().construct(req, tmp_path)) is c.ErrorCode.UNSUPPORTED_INPUT


def test_a_cancelled_token_stops_before_anything_is_sent(sidecar, tmp_path):  # noqa: F811
    tok = c.CancelToken()
    tok.cancel()
    assert _construct_error(lambda: constructor().construct(_req(tmp_path), tmp_path, cancel=tok)) is c.ErrorCode.CANCELLED
    assert sidecar.requests == []


def test_cancel_during_the_pack_discards_the_result(sidecar, tmp_path):  # noqa: F811
    tok = c.CancelToken()
    out = tmp_path / "out"
    out.mkdir()

    def cancel_mid_call(request):
        tok.cancel()
        return httpx.Response(200, content=b"PACKED")
    sidecar.handler = cancel_mid_call
    assert _construct_error(lambda: constructor().construct(_req(tmp_path), out, cancel=tok)) is c.ErrorCode.CANCELLED
    assert list(out.iterdir()) == []


@pytest.mark.parametrize("handler, code", [
    (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")), c.ErrorCode.TIMEOUT),
    (lambda r: httpx.Response(408), c.ErrorCode.TIMEOUT),
    (lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")), c.ErrorCode.UNREACHABLE),
    (lambda r: httpx.Response(503), c.ErrorCode.NOT_READY),
    (lambda r: httpx.Response(500, json={"error": {"code": "PACK_FAILED", "message": "boom", "status": 500}}), c.ErrorCode.FAILED),
])
def test_sidecar_failures_map_to_neutral_codes(sidecar, tmp_path, handler, code):  # noqa: F811
    sidecar.handler = handler
    with pytest.raises(c.ProviderError) as e:
        constructor().construct(_req(tmp_path), tmp_path)
    assert e.value.code is code
    if code is c.ErrorCode.FAILED:
        assert "PACK_FAILED: boom" in e.value.message


def test_status_ready_not_ready_and_unreachable(sidecar):  # noqa: F811
    assert constructor().status().state is c.Readiness.READY
    sidecar.handler = lambda r: httpx.Response(503)
    assert constructor().status().state is c.Readiness.NOT_READY

    def boom(request):
        raise httpx.ConnectError("refused")
    sidecar.handler = boom
    st = constructor().status(timeout=1.5)
    assert st.state is c.Readiness.UNREACHABLE and "refused" in st.detail
    assert sidecar.client_kwargs[-1]["timeout"] == 1.5
    constructor().status(timeout=None)                     # never an unbounded wait
    assert sidecar.client_kwargs[-1]["timeout"] == c.DEFAULT_HEALTH_TIMEOUT_S


def test_the_plugin_instance_builds_the_constructor_and_none_while_unconfigured():
    from app.plugins.capabilities.three_mf_construct import ConstructPlugin
    assert LaminusConnection(LaminusSettings(url=None)).project_constructor() is None
    conn = LaminusConnection(LaminusSettings(url=URL, request_timeout_s=77.0))
    assert isinstance(conn, ConstructPlugin)
    built = conn.project_constructor()
    assert isinstance(built, LaminusProjectConstructor) and built.identity.version == PLUGIN_VERSION
    assert built._request_timeout_s == 77.0


# ---- plate manifest (BIZ-359): which request input each instance on each plate came from ---------------------------------

def _pack_with(tmp_path, manifest) -> "Path":
    import io, json, zipfile
    p = tmp_path / "pack.3mf"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/plate_1.png", b"x")
        if manifest is not None:
            zf.writestr("Metadata/laminus_manifest.json", manifest if isinstance(manifest, str) else json.dumps(manifest))
    p.write_bytes(buf.getvalue())
    return p


def test_plate_manifest_counts_instances_per_plate_and_input(tmp_path):
    m = {"version": 1, "items": [{"item": 0, "source": "a.stl", "requested": 3}, {"item": 1, "source": "b.stl", "requested": 1}],
         "plates": [{"plate": 1, "instances": [{"item": 0, "part": "a.stl"}, {"item": 0, "part": "a.stl"}, {"item": 1, "part": "b.stl"}]},
                    {"plate": 2, "instances": [{"item": 0, "part": "a.stl"}]}]}
    got = constructor().plate_manifest(_pack_with(tmp_path, m))
    assert got == c.PlateManifest(plates={1: {0: 2, 1: 1}, 2: {0: 1}}, requested={0: 3, 1: 1})


@pytest.mark.parametrize("manifest", [None, "not json", {"version": 2, "plates": []}, {"version": 1},
                                      {"version": 1, "plates": [{"plate": "x"}]}, {"version": 1, "plates": [{"plate": 1, "instances": [{}]}]}])
def test_plate_manifest_is_none_when_absent_unreadable_or_unknown(tmp_path, manifest):
    assert constructor().plate_manifest(_pack_with(tmp_path, manifest)) is None


def test_plate_manifest_is_none_for_a_non_zip(tmp_path):
    p = tmp_path / "x.3mf"
    p.write_bytes(b"nope")
    assert constructor().plate_manifest(p) is None


def test_the_contract_default_reports_no_manifest(tmp_path):
    from tests.fake_providers import FakeProjectConstructor
    assert FakeProjectConstructor().plate_manifest(_pack_with(tmp_path, None)) is None


class _Catalog:
    def ref_for(self, kind, name):
        return {"machine": {"Bambu P1S 0.4": "m-uuid"}}.get(kind, {}).get(name)


def _next_req(tmp_path, machine="Bambu P1S 0.4"):
    proj = tmp_path / "p.3mf"
    proj.write_bytes(b"PK")
    return c.ConstructRequest((c.ConstructInput(_stl(tmp_path), 4, "item-1"), c.ConstructInput(proj, 2, "item-2", plate=3, rule="together")),
                              c.TargetPrinter(BED, machine_profile=machine))


def _next_handler(sc, response):
    def handler(request):
        sc.requests.append(request)
        return response(request)
    return handler


def test_next_plate_posts_the_machine_uuid_selection_and_cap_and_reads_consumed(sidecar, tmp_path, monkeypatch):  # noqa: F811
    """BIZ-362: /pack/next gets the machine's catalog uuid, the selection in input order and max_parts; X-Consumed maps back
    to input indexes (entries that took nothing are left out)."""
    import json as _json
    monkeypatch.setattr("app.services.catalog_service.cached_catalog", lambda: _Catalog())
    sidecar.handler = lambda r: httpx.Response(200, content=b"NEXT", headers={"X-Consumed": "[0, 2]"})
    art, consumed = constructor().next_plate(_next_req(tmp_path), tmp_path, max_parts=5)
    assert art.path.read_bytes() == b"NEXT" and art.kind is c.ArtifactKind.PROJECT_3MF
    assert consumed == {1: 2}
    (call,) = [r for r in sidecar.requests if r.url.path == "/api/v1/pack/next"]
    body = call.content.decode("latin-1")
    field = lambda n: body.split(f'name="{n}"')[1].split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0]  # noqa: E731
    assert field("machine_uuid") == "m-uuid" and field("max_parts") == "5"
    assert _json.loads(field("selection")) == [{"file": "0_a.stl", "qty": 4},
                                                {"file": "1_p.3mf", "qty": 2, "plate": 3, "rule": "together"}]


def test_next_plate_nothing_fits_is_its_own_error(sidecar, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr("app.services.catalog_service.cached_catalog", lambda: _Catalog())
    sidecar.handler = lambda r: httpx.Response(422, json={"error": {"code": "nothing_fits", "message": "too big"}})
    with pytest.raises(c.NothingFits):
        constructor().next_plate(_next_req(tmp_path), tmp_path)
    sidecar.handler = lambda r: httpx.Response(422, json={"error": {"code": "validation_error", "message": "bad"}})
    with pytest.raises(c.ProviderError) as e:
        constructor().next_plate(_next_req(tmp_path), tmp_path)
    assert not isinstance(e.value, c.NothingFits) and e.value.code is c.ErrorCode.FAILED


def test_next_plate_without_a_machine_uuid_is_unsupported_and_sends_nothing(sidecar, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr("app.services.catalog_service.cached_catalog", lambda: _Catalog())
    assert _construct_error(lambda: constructor().next_plate(_next_req(tmp_path, machine="Unknown"), tmp_path)) \
        is c.ErrorCode.UNSUPPORTED_TARGET
    assert sidecar.requests == []


def test_the_contract_default_cannot_pack_a_next_plate(tmp_path):
    from tests.fake_providers import FakeProjectConstructor
    assert _construct_error(lambda: FakeProjectConstructor().next_plate(_req(tmp_path), tmp_path)) is c.ErrorCode.UNSUPPORTED_INPUT


def test_measure_posts_the_selection_and_reads_one_size_per_entry(sidecar, tmp_path):  # noqa: F811
    """BIZ-363: /projects/measure gets {id} / {plate} / {} entries in order; a missing or malformed size is None."""
    import json as _json
    proj = tmp_path / "p.3mf"
    proj.write_bytes(b"PK")
    def handler(r):
        if b'name="selection"' not in r.content:
            return httpx.Response(200, json={"items": [{"size_mm": [5, 6, 7]}]})        # the whole file
        return httpx.Response(200, json={"items": [{"size_mm": [10, 20.5, 30]}, {"size_mm": [1, 2]}]})
    sidecar.handler = handler
    sizes = constructor().measure(proj, [{"object_id": "8"}, {}, {"plate": 2}, {}])
    assert sizes == [(10.0, 20.5, 30.0), (5.0, 6.0, 7.0), None, (5.0, 6.0, 7.0)]
    calls = [r for r in sidecar.requests if r.url.path == "/api/v1/projects/measure"]
    assert len(calls) == 2                                                        # the selection once, the whole file once
    body = next(r for r in calls if b'name="selection"' in r.content).content.decode("latin-1")
    sel = _json.loads(body.split('name="selection"')[1].split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0])
    assert sel == [{"id": 8}, {"plate": 2}]
    sidecar.requests.clear()
    assert constructor().measure(_stl(tmp_path), [{}]) == [(5.0, 6.0, 7.0)]
    assert [b'name="selection"' in r.content for r in sidecar.requests] == [False]


def test_measure_failures_map_to_neutral_codes(sidecar, tmp_path):  # noqa: F811
    stl = _stl(tmp_path)
    sidecar.handler = lambda r: httpx.Response(500, json={"error": {"code": "error", "message": "boom"}})
    assert _construct_error(lambda: constructor().measure(stl, [{}])) is c.ErrorCode.FAILED
    sidecar.handler = lambda r: httpx.Response(503, json={})
    assert _construct_error(lambda: constructor().measure(stl, [{}])) is c.ErrorCode.NOT_READY


def test_the_contract_default_cannot_measure(tmp_path):
    from tests.fake_providers import FakeProjectConstructor
    assert _construct_error(lambda: FakeProjectConstructor().measure(_stl(tmp_path), [{}])) is c.ErrorCode.UNSUPPORTED_INPUT


def test_the_catalog_carries_a_machine_printable_height():
    from app.services.providers.laminus.adapter import catalog_from_legacy
    cat = catalog_from_legacy({"machine": [{"uuid": "m", "name": "A", "bed_size_z": 256}, {"uuid": "n", "name": "B"},
                                           {"uuid": "o", "name": "C", "bed_size_z": None}, {"uuid": "p", "name": "D", "bed_size_z": 0}]})
    assert [p.height_mm for p in cat.machines] == [256.0, None, None, None]
