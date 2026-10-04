# backend/tests/services/test_slicer_service.py
from tests.catalog_helpers import cached_raw, prime_catalog
import struct
import zipfile as _zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.providers.slicing import SlicingProviderError
from app.services.slicer_service import SlicerService, SliceRequest, SliceError
from tests.fake_providers import FakeSlicingProvider, make_catalog


def _req(tmp_path, export_args=None, **kw):
    src = tmp_path / "model.3mf"
    src.write_bytes(b"dummy")
    return SliceRequest(
        job_id=1, source_3mf=str(src), plate_number=1,
        machine_preset="Elegoo Centauri Carbon", process_preset="0.20mm Standard",
        filament_presets=["Generic PLA"], filament_colours=["#FFFFFF"],
        export_args=export_args or [],
        **kw,
    )


_DEFAULT_CATALOG = {
    "machine": [{"name": "Elegoo Centauri Carbon", "uuid": "m1"}],
    "process": [{"name": "0.20mm Standard", "uuid": "p1"}],
    "filament": [{"name": "Generic PLA", "uuid": "f1"}],
}


def _make_service(tmp_path):
    svc = SlicerService.__new__(SlicerService)
    svc._data_dir = tmp_path
    return svc


# ── error paths ────────────────────────────────────────────────────────────────

def test_raises_when_no_sidecar_url(tmp_path):
    svc = _make_service(tmp_path)
    prime_catalog(_DEFAULT_CATALOG)
    with patch("app.config.get_laminus_sidecar_url", return_value=None):
        with pytest.raises(SliceError, match="LAMINUS_SIDECAR_URL"):
            svc.slice(_req(tmp_path))


def test_raises_when_machine_not_in_catalog(tmp_path):
    svc = _make_service(tmp_path)
    catalog = {
        "machine": [],
        "process": [{"name": "0.20mm Standard", "uuid": "p1"}],
        "filament": [{"name": "Generic PLA", "uuid": "f1"}],
    }
    prime_catalog(catalog)
    with patch("app.config.get_laminus_sidecar_url", return_value="http://laminus:5000"):
        with pytest.raises(SliceError, match="not found in Laminus sidecar catalog"):
            svc.slice(_req(tmp_path))


def test_raises_when_filament_not_in_catalog(tmp_path):
    svc = _make_service(tmp_path)
    catalog = {
        "machine": [{"name": "Elegoo Centauri Carbon", "uuid": "m1"}],
        "process": [{"name": "0.20mm Standard", "uuid": "p1"}],
        "filament": [],
    }
    prime_catalog(catalog)
    with patch("app.config.get_laminus_sidecar_url", return_value="http://laminus:5000"):
        with pytest.raises(SliceError, match="not found in Laminus sidecar catalog"):
            svc.slice(_req(tmp_path))


# ── actionable resolution errors ─────────────────────────────────────────────

def test_error_names_the_specific_unresolved_preset_and_kind(tmp_path):
    """The user must be told WHICH preset failed and its kind, not just that
    something among three presets didn't resolve."""
    svc = _make_service(tmp_path)
    catalog = {
        "machine": [],
        "process": [{"name": "0.20mm Standard", "uuid": "p1"}],
        "filament": [{"name": "Generic PLA", "uuid": "f1"}],
    }
    prime_catalog(catalog)
    with patch("app.config.get_laminus_sidecar_url", return_value="http://laminus:5000"):
        with pytest.raises(SliceError) as exc_info:
            svc.slice(_req(tmp_path))
    msg = str(exc_info.value)
    assert "machine preset" in msg
    assert "Elegoo Centauri Carbon" in msg
    # the other two presets DID resolve — must not be named as failures
    assert "process preset" not in msg
    assert "filament preset" not in msg


def test_error_names_multiple_unresolved_presets(tmp_path):
    svc = _make_service(tmp_path)
    catalog = {"machine": [], "process": [], "filament": []}
    prime_catalog(catalog)
    with patch("app.config.get_laminus_sidecar_url", return_value="http://laminus:5000"):
        with pytest.raises(SliceError) as exc_info:
            svc.slice(_req(tmp_path))
    msg = str(exc_info.value)
    assert "machine preset" in msg and "Elegoo Centauri Carbon" in msg
    assert "process preset" in msg and "0.20mm Standard" in msg
    assert "filament preset" in msg and "Generic PLA" in msg


def test_error_advises_refreshing_the_profile_sync(tmp_path):
    svc = _make_service(tmp_path)
    catalog = {"machine": [], "process": [], "filament": []}
    prime_catalog(catalog)
    with patch("app.config.get_laminus_sidecar_url", return_value="http://laminus:5000"):
        with pytest.raises(SliceError, match="[Rr]efresh the profile sync"):
            svc.slice(_req(tmp_path))


def test_error_reports_missing_filament_when_none_supplied(tmp_path):
    """process_preset omitted entirely (Part 2's 'no invented default') — the
    filament list ends up empty, which must read as a real, named failure."""
    svc = _make_service(tmp_path)
    catalog = {
        "machine": [{"name": "Elegoo Centauri Carbon", "uuid": "m1"}],
        "process": [{"name": "0.20mm Standard", "uuid": "p1"}],
        "filament": [{"name": "Generic PLA", "uuid": "f1"}],
    }
    prime_catalog(catalog)
    req = _req(tmp_path)
    req.filament_presets = []
    with patch("app.config.get_laminus_sidecar_url", return_value="http://laminus:5000"):
        with pytest.raises(SliceError, match="no filament preset was supplied"):
            svc.slice(req)


def _fake_provider(**kw):
    fake = FakeSlicingProvider(**kw)
    return fake, patch("app.services.slicer_service.get_slicing_provider", return_value=fake)


def _slice_spec(fake):
    return next(c[1] for c in fake.calls if c[0] == "slice")


def test_raises_with_clear_message_when_sidecar_unreachable(tmp_path):
    """Catalog fetch failure surfaces 'unreachable', not a misleading profile-not-found message."""
    svc = _make_service(tmp_path)
    prime_catalog(None)  # force a live sidecar fetch

    fake, use = _fake_provider()
    fake.fail_on["get_catalog"] = SlicingProviderError("Connection refused")
    with use:
        with pytest.raises(SliceError, match="Laminus sidecar unreachable"):
            svc.slice(_req(tmp_path))


def test_a_cold_cache_resolves_presets_from_a_live_catalog_fetch_and_remembers_it(tmp_path):
    svc = _make_service(tmp_path)
    prime_catalog(None)

    fake, use = _fake_provider(catalog=make_catalog(
        machines=(("Elegoo Centauri Carbon", "m1"),), processes=(("0.20mm Standard", "p1"),),
        filaments=(("Generic PLA", "f1"),)))
    with use:
        svc.slice(_req(tmp_path))
        svc.slice(_req(tmp_path))

    assert [c[0] for c in fake.calls].count("get_catalog") == 1   # fetched once, then served from the cache
    assert cached_raw() is not None
    spec = _slice_spec(fake)
    assert (spec.machine_ref, spec.process_ref, spec.filament_refs) == ("m1", "p1", ["f1"])


def test_sidecar_error_converted_to_slice_error(tmp_path):
    svc = _make_service(tmp_path)
    prime_catalog(_DEFAULT_CATALOG)

    fake, use = _fake_provider()
    fake.fail_on["slice"] = SlicingProviderError("timeout")
    with use:
        with pytest.raises(SliceError, match="timeout"):
            svc.slice(_req(tmp_path))


# ── happy paths ────────────────────────────────────────────────────────────────

def test_default_returns_raw_gcode(tmp_path):
    svc = _make_service(tmp_path)
    prime_catalog(_DEFAULT_CATALOG)

    fake, use = _fake_provider()
    fake.artifact_name = "plate_1.gcode"
    with use:
        path = svc.slice(_req(tmp_path))

    assert path == str(tmp_path / "gcode" / "1" / "plate_1.gcode")
    assert (tmp_path / "gcode" / "1" / "plate_1.gcode").exists()
    assert [c[0] for c in fake.calls].count("slice") == 1
    spec = _slice_spec(fake)
    assert spec.export_3mf is False
    assert (spec.machine_ref, spec.process_ref, spec.filament_refs, spec.plate) == ("m1", "p1", ["f1"], 1)
    assert spec.source_file == tmp_path / "model.3mf"


def test_export_3mf_flag_forwarded(tmp_path):
    svc = _make_service(tmp_path)
    prime_catalog(_DEFAULT_CATALOG)

    fake, use = _fake_provider()
    fake.artifact_name = "model.gcode.3mf"
    with use:
        path = svc.slice(_req(tmp_path, export_args=["--export-3mf", "model.gcode.3mf"]))

    assert path.endswith("model.gcode.3mf")
    assert _slice_spec(fake).export_3mf is True


def test_extra_config_passed_to_provider(tmp_path):
    svc = _make_service(tmp_path)
    prime_catalog(_DEFAULT_CATALOG)

    fake, use = _fake_provider()
    overrides = {"curr_bed_type": "textured_plate", "layer_height": "0.15"}
    with use:
        svc.slice(_req(tmp_path, extra_config=overrides))

    assert _slice_spec(fake).extra_config == overrides


def test_stale_artifacts_are_removed_before_slicing(tmp_path):
    svc = _make_service(tmp_path)
    prime_catalog(_DEFAULT_CATALOG)
    out = tmp_path / "gcode" / "1"
    out.mkdir(parents=True)
    (out / "old.gcode").write_text("old")
    (out / "old.gcode.3mf").write_bytes(b"PK")

    fake, use = _fake_provider()
    with use:
        svc.slice(_req(tmp_path))

    assert sorted(p.name for p in out.iterdir()) == ["fake.gcode"]


def test_slice_calls_inject_thumbnail_for_3mf_source(tmp_path):
    svc = _make_service(tmp_path)
    prime_catalog(_DEFAULT_CATALOG)

    three_mf = _3mf_with_thumb(tmp_path, plate=1)
    fake, use = _fake_provider()
    fake.artifact_name = "plate_1.gcode"

    req = _req(tmp_path)
    req.source_3mf = str(three_mf)

    with use, patch.object(svc, "_inject_thumbnail") as mock_inject:
        svc.slice(req)

    mock_inject.assert_called_once()
    args = mock_inject.call_args[0]
    assert args[0].endswith(".gcode")
    assert args[1] == str(three_mf)
    assert args[2] == 1


# ── _inject_thumbnail ──────────────────────────────────────────────────────────

def _png(width: int = 64, height: int = 64) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + b"\x00\x00\x00\x00"
    iend = b"\x00\x00\x00\x00IEND\xaeB`\x82"
    return sig + ihdr + iend


def _3mf_with_thumb(tmp_path, *, plate: int | None = 1, name: str | None = None) -> Path:
    path = tmp_path / "model.3mf"
    entry = name or f"plate_{plate}.png"
    with _zipfile.ZipFile(path, "w") as z:
        z.writestr(f"Metadata/{entry}", _png())
    return path


def test_inject_thumbnail_prepends_header_to_gcode(tmp_path):
    svc = _make_service(tmp_path)
    three_mf = _3mf_with_thumb(tmp_path, plate=1)
    gcode = tmp_path / "out.gcode"
    gcode.write_text("G28\nG1 X0\n")

    svc._inject_thumbnail(str(gcode), str(three_mf), plate_number=1)

    content = gcode.read_text()
    assert content.startswith("; thumbnail begin 64x64 ")
    assert "; thumbnail end" in content
    assert "G28" in content


def test_inject_thumbnail_falls_back_to_thumbnail_png(tmp_path):
    svc = _make_service(tmp_path)
    three_mf = _3mf_with_thumb(tmp_path, name="thumbnail.png")
    gcode = tmp_path / "out.gcode"
    gcode.write_text("G28\n")

    svc._inject_thumbnail(str(gcode), str(three_mf), plate_number=1)

    assert "; thumbnail begin" in gcode.read_text()


def test_inject_thumbnail_falls_back_to_preview_png(tmp_path):
    svc = _make_service(tmp_path)
    three_mf = _3mf_with_thumb(tmp_path, name="preview.png")
    gcode = tmp_path / "out.gcode"
    gcode.write_text("G28\n")

    svc._inject_thumbnail(str(gcode), str(three_mf), plate_number=1)

    assert "; thumbnail begin" in gcode.read_text()


def test_inject_thumbnail_noop_when_no_thumbnail_in_zip(tmp_path):
    svc = _make_service(tmp_path)
    path = tmp_path / "model.3mf"
    with _zipfile.ZipFile(path, "w") as z:
        z.writestr("Metadata/model_settings.config", "<config/>")
    gcode = tmp_path / "out.gcode"
    original = "G28\nG1 X0\n"
    gcode.write_text(original)

    svc._inject_thumbnail(str(gcode), str(path), plate_number=1)

    assert gcode.read_text() == original


def test_inject_thumbnail_noop_for_invalid_png_magic(tmp_path):
    svc = _make_service(tmp_path)
    path = tmp_path / "model.3mf"
    with _zipfile.ZipFile(path, "w") as z:
        z.writestr("Metadata/plate_1.png", b"not-a-png-at-all")
    gcode = tmp_path / "out.gcode"
    original = "G28\n"
    gcode.write_text(original)

    svc._inject_thumbnail(str(gcode), str(path), plate_number=1)

    assert gcode.read_text() == original


@pytest.mark.asyncio
async def test_slice_uses_custom_output_dir(tmp_path):
    """When output_dir is provided, the slice method uses it instead of the default."""
    from pathlib import Path
    from unittest.mock import MagicMock, patch
    from app.services.slicer_service import SlicerService, SliceRequest

    custom_dir = tmp_path / "estimates" / "99"

    svc = SlicerService(data_dir=str(tmp_path))

    req = SliceRequest(
        job_id=99, source_3mf="model.3mf", plate_number=1,
        machine_preset="M", process_preset="P", filament_presets=["F"],
    )

    captured_out_dir = None

    def fake_execute(self_inner, req_inner, machine_uuid, process_uuid, filament_uuids,
                     out_dir, sidecar_url):
        nonlocal captured_out_dir
        captured_out_dir = out_dir
        return str(out_dir / "result.gcode")

    with patch("app.config.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch.object(SlicerService, "_resolve_uuids", return_value=("m", "p", ["f"])), \
         patch.object(SlicerService, "_execute_slice_by_ids", fake_execute):
        svc.slice(req, output_dir=custom_dir)

    assert captured_out_dir == custom_dir
    assert custom_dir.exists()
