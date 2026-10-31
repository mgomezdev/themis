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


def test_a_3mf_input_is_unsupported_because_pack_cannot_preserve_it(sidecar, tmp_path):  # noqa: F811
    proj = tmp_path / "p.3mf"
    proj.write_bytes(b"PK")
    req = _req(tmp_path, c.ConstructInput(_stl(tmp_path), 1, "1"), c.ConstructInput(proj, 1, "2"))
    assert _construct_error(lambda: constructor().construct(req, tmp_path)) is c.ErrorCode.UNSUPPORTED_INPUT
    assert sidecar.requests == []


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


def test_the_plugin_instance_builds_the_constructor_and_none_while_unconfigured():
    from app.plugins.capabilities.three_mf_construct import ConstructPlugin
    assert LaminusConnection(LaminusSettings(url=None)).project_constructor() is None
    conn = LaminusConnection(LaminusSettings(url=URL, request_timeout_s=77.0))
    assert isinstance(conn, ConstructPlugin)
    built = conn.project_constructor()
    assert isinstance(built, LaminusProjectConstructor) and built.identity.version == PLUGIN_VERSION
    assert built._request_timeout_s == 77.0
