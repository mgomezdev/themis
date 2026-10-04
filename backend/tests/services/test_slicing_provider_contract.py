"""Contract suite for SlicingProvider, run against the in-memory fake and the Laminus adapter
(backed by an in-process fake sidecar behind an httpx mock transport)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from app.services.providers.laminus import LaminusSlicingProvider
from app.services.providers.slicing import SliceSpec, SlicingProviderError, get_slicing_provider
from tests.fake_providers import FakeSlicingProvider, make_catalog

URL = "http://laminus.test"

_RAW_CATALOG = {
    "machine": [{"name": "Printer A", "uuid": "m-1"}],
    "process": [{"name": "0.20mm Standard", "uuid": "p-1", "compatible_printers": ["Printer A"]}],
    "filament": [{"name": "PLA @A", "uuid": "f-1", "compatible_printers": ["Printer A"]},
                 {"name": "PETG @A", "uuid": "f-2", "compatible_printers": []}],
}


@pytest.fixture
def sidecar():
    """Patch every httpx.Client the adapter's sidecar client builds onto a fake Laminus.
    `sc.handler` overrides; `sc.requests` records; `sc.client_kwargs` records constructor kwargs."""
    sc = SimpleNamespace(handler=None, requests=[], client_kwargs=[], status="completed", gcode=b"G1 X0\n")
    real_client = httpx.Client

    def default(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok", "orcaslicer_version": "2.3.2"})
        if path == "/api/profiles":
            return httpx.Response(200, json=_RAW_CATALOG)
        if path == "/api/profiles/merged-config":
            body = json.loads(request.content)
            return httpx.Response(200, json={"machine_uuid": body["machine_uuid"], "layer_height": "0.2"})
        if path in ("/api/slice/start", "/api/slice/prepared"):
            return httpx.Response(200, json={"job_id": "job-1"})
        if path == "/api/slice/status/job-1":
            if sc.status == "failed":
                return httpx.Response(200, json={"status": "failed", "error": "bad geometry"})
            return httpx.Response(200, json={"status": "completed", "sliced_file": "out.gcode"})
        if path == "/api/slice/download/job-1":
            return httpx.Response(200, content=sc.gcode)
        if path == "/api/arrange":
            return httpx.Response(200, content=b"ARRANGED")
        if path == "/api/pack":
            return httpx.Response(200, content=b"PACKED")
        return httpx.Response(404)

    def record(request):
        sc.requests.append(request)

    def factory(*args, **kwargs):
        sc.client_kwargs.append(dict(kwargs))
        kwargs.pop("transport", None)
        transport = httpx.MockTransport(sc.handler or default)
        hooks = {"request": [record]}
        return real_client(*args, transport=transport, event_hooks=hooks, **kwargs)

    with patch("app.services.laminus_sidecar_client.httpx.Client", factory):
        yield sc


@pytest.fixture(params=["fake", "laminus"])
def provider(request, sidecar):
    if request.param == "fake":
        return FakeSlicingProvider(make_catalog(
            machines=(("Printer A", "m-1"),), processes=(("0.20mm Standard", "p-1"),),
            filaments=(("PLA @A", "f-1"), ("PETG @A", "f-2", []))))
    return LaminusSlicingProvider(URL)


def _spec(tmp_path, **kw) -> SliceSpec:
    src = tmp_path / "model.3mf"
    src.write_bytes(b"PK")
    return SliceSpec(source_file=src, plate=1, machine_ref="m-1", process_ref="p-1", filament_refs=["f-1"], **kw)


def test_capabilities_declared(provider):
    assert provider.ARRANGE and provider.PACK_MODELS and provider.PREPARED_PROJECT
    assert isinstance(provider.identity, str) and provider.identity


def test_health_returns_dict(provider):
    assert provider.health()["status"] == "ok"


def test_catalog_maps_presets_and_name_to_ref_lookup(provider):
    cat = provider.get_catalog()
    assert [(p.name, p.ref) for p in cat.machines] == [("Printer A", "m-1")]
    assert cat.ref_for("process", "0.20mm Standard") == "p-1"
    assert cat.ref_for("filament", "PETG @A") == "f-2"
    assert cat.ref_for("filament", "nope") is None
    assert cat.names("filament") == {"PLA @A", "PETG @A"}
    assert cat.refs("machine") == {"m-1"}
    pla = next(p for p in cat.filaments if p.ref == "f-1")
    assert pla.compatible_printers == ["Printer A"]
    assert next(p for p in cat.filaments if p.ref == "f-2").compatible_printers == []


def test_catalog_raw_is_the_legacy_shape(provider):
    raw = provider.get_catalog().raw
    assert set(raw) >= {"machine", "process", "filament"}
    assert raw["machine"][0]["uuid"] == "m-1"


def test_merged_config_returns_dict(provider):
    assert provider.merged_config("m-1", "p-1", ["f-1"]) is not None


def test_slice_returns_artifact_path_with_bytes(provider, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    path = provider.slice(_spec(tmp_path), out)
    assert path.startswith(str(out))
    assert (out / path.rsplit("/", 1)[-1]).read_bytes()


def test_arrange_and_pack_return_bytes(provider, tmp_path):
    proj = tmp_path / "p.3mf"
    proj.write_bytes(b"PK")
    stl = tmp_path / "a.stl"
    stl.write_bytes(b"solid")
    assert provider.arrange(proj) == b"ARRANGED"
    assert provider.pack_models([stl], machine_ref="m-1", process_ref="p-1", filament_refs=["f-1"]) == b"PACKED"
    assert provider.pack_models([stl], bed=(220.0, 220.0, 250.0)) == b"PACKED"


# ---- Laminus-adapter specifics ----

def test_laminus_failed_poll_raises_neutral_error(sidecar, tmp_path):
    sidecar.status = "failed"
    with pytest.raises(SlicingProviderError, match="bad geometry"):
        LaminusSlicingProvider(URL).slice(_spec(tmp_path), tmp_path)


def test_laminus_slice_artifact_is_downloaded_to_the_status_filename(sidecar, tmp_path):
    sidecar.gcode = b"G28\nG1 X1\n"
    path = LaminusSlicingProvider(URL).slice(_spec(tmp_path), tmp_path)
    assert path == str(tmp_path / "out.gcode")
    assert (tmp_path / "out.gcode").read_bytes() == b"G28\nG1 X1\n"


def test_laminus_slice_sends_resolved_refs_plate_and_extra_config(sidecar, tmp_path):
    LaminusSlicingProvider(URL).slice(_spec(tmp_path, extra_config={"bed_type": "Textured PEI"}), tmp_path)
    start = next(r for r in sidecar.requests if r.url.path == "/api/slice/start")
    body = start.content.decode(errors="ignore")
    for needle in ('name="machine_uuid"', "m-1", "p-1", '["f-1"]', 'name="plate"', "Textured PEI", "geometry_only_retry"):
        assert needle in body


def test_laminus_omits_empty_extra_config_and_names_the_3mf_export(sidecar, tmp_path):
    LaminusSlicingProvider(URL).slice(_spec(tmp_path, export_3mf=True), tmp_path)
    body = next(r for r in sidecar.requests if r.url.path == "/api/slice/start").content.decode(errors="ignore")
    assert "extra_config" not in body
    assert "model.gcode.3mf" in body


def test_laminus_prepared_projects_use_the_prepared_endpoint(sidecar, tmp_path):
    LaminusSlicingProvider(URL).slice(_spec(tmp_path, prepared=True), tmp_path)
    paths = [r.url.path for r in sidecar.requests]
    assert "/api/slice/prepared" in paths and "/api/slice/start" not in paths


def test_laminus_timeouts_unchanged(sidecar, tmp_path):
    p = LaminusSlicingProvider(URL)
    p.slice(_spec(tmp_path), tmp_path)
    assert sidecar.client_kwargs[-1]["timeout"] == 630           # slice client: just over sidecar's 600s
    p.health(timeout=2)
    assert sidecar.client_kwargs[-1]["timeout"] == 2             # explicit short health timeout honoured
    p.merged_config("m-1", "p-1", ["f-1"], timeout=10)
    assert sidecar.client_kwargs[-1]["timeout"] == 10
    import inspect
    from app.services.laminus_sidecar_client import LaminusSidecarClient
    assert inspect.signature(LaminusSidecarClient.poll_status).parameters["timeout"].default == 620.0


def test_laminus_http_errors_map_to_neutral_error(sidecar):
    sidecar.handler = lambda request: httpx.Response(503, text="starting")
    with pytest.raises(SlicingProviderError, match="503"):
        LaminusSlicingProvider(URL).health()
    with pytest.raises(SlicingProviderError):
        LaminusSlicingProvider(URL).get_catalog()


def test_laminus_pack_without_presets_or_bed_is_rejected(sidecar, tmp_path):
    with pytest.raises(SlicingProviderError):
        LaminusSlicingProvider(URL).pack_models([tmp_path / "a.stl"])


# ---- accessor ----

def test_accessor_none_when_url_not_configured(monkeypatch):
    monkeypatch.delenv("LAMINUS_SIDECAR_URL", raising=False)
    assert get_slicing_provider() is None


def test_accessor_returns_laminus_adapter_when_configured(monkeypatch):
    monkeypatch.setenv("LAMINUS_SIDECAR_URL", URL)
    p = get_slicing_provider()
    assert isinstance(p, LaminusSlicingProvider) and p.identity == URL


# ---- Laminus catalog readiness / rebuild ----

def test_laminus_catalog_health_returns_body_on_200_and_building_marker_on_503(sidecar):
    p = LaminusSlicingProvider(URL)
    assert p.catalog_health()["status"] == "ok"
    sidecar.handler = lambda request: httpx.Response(503, text="building")
    assert p.catalog_health() == {"catalog_loaded": False, "catalog_building": True}
    sidecar.handler = lambda request: httpx.Response(500)
    with pytest.raises(SlicingProviderError, match="500"):
        p.catalog_health()


def test_laminus_catalog_health_uses_the_given_timeout_and_maps_transport_errors(sidecar):
    p = LaminusSlicingProvider(URL)
    p.catalog_health(timeout=5.0)
    assert sidecar.requests[-1].extensions["timeout"]["read"] == 5.0

    def boom(request):
        raise httpx.ConnectError("refused")
    sidecar.handler = boom
    with pytest.raises(SlicingProviderError, match="refused"):
        p.catalog_health()


@pytest.mark.parametrize("status, ok", [(200, True), (503, True), (500, False)])
def test_laminus_rebuild_request_accepts_200_and_503_only(sidecar, status, ok):
    sidecar.handler = lambda request: httpx.Response(status)
    p = LaminusSlicingProvider(URL)
    if ok:
        p.request_catalog_rebuild()
        req = sidecar.requests[-1]
        assert req.url.path == "/api/profiles" and req.url.params["refresh"] == "true"
        assert req.extensions["timeout"]["read"] == 10.0
    else:
        with pytest.raises(SlicingProviderError, match="Laminus rescan trigger returned 500"):
            p.request_catalog_rebuild()


def test_laminus_rebuild_request_maps_transport_errors(sidecar):
    def boom(request):
        raise httpx.ConnectError("refused")
    sidecar.handler = boom
    with pytest.raises(SlicingProviderError, match="Could not reach Laminus sidecar: refused"):
        LaminusSlicingProvider(URL).request_catalog_rebuild()


# ---- slicer-specific file-format methods ----

def test_format_methods_are_provided_without_any_server(monkeypatch):
    """get_format_provider never returns None and its format methods do no I/O to a server."""
    from app.services.providers.slicing import get_format_provider
    monkeypatch.delenv("LAMINUS_SIDECAR_URL", raising=False)
    fmt = get_format_provider()
    assert get_slicing_provider() is None
    assert "enable_support" in fmt.curated_override_keys()


def test_parse_estimates_reads_the_orca_summary_lines(provider, tmp_path):
    gcode = tmp_path / "m.gcode"
    gcode.write_text("; filament used [g] = 1.25, 2.75\n; estimated printing time (normal mode) = 1h 2m 3s\n")
    grams, secs, per_extruder = provider.parse_estimates(str(gcode))
    assert isinstance(grams, float) and isinstance(secs, int) and per_extruder
    if isinstance(provider, LaminusSlicingProvider):
        assert (grams, secs, per_extruder) == (4.0, 3723, [1.25, 2.75])


def test_inspect_overrides_returns_the_documented_shape(provider, tmp_path):
    import zipfile
    proj = tmp_path / "p.3mf"
    with zipfile.ZipFile(proj, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps({"enable_support": "1"}))
    result = provider.inspect_overrides(str(proj), {"enable_support": "0"}, 1)
    assert set(result) >= {"has_findings", "setting_changes", "slot_warning"}
    if isinstance(provider, LaminusSlicingProvider):
        assert result["has_findings"] is True
        assert [c["key"] for c in result["setting_changes"]] == ["enable_support"]


def test_compatible_presets_filters_a_catalog_by_the_machines_compatible_printers_list(provider):
    cat = provider.get_catalog()
    assert [p.name for p in provider.compatible_presets(cat, "Printer A", "filament")] == ["PLA @A"]
    assert [p.name for p in provider.compatible_presets(cat, "Printer A", "process")] == ["0.20mm Standard"]
    assert provider.compatible_presets(cat, "Some Other Printer", "filament") == []
