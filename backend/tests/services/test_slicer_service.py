# backend/tests/services/test_slicer_service.py
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.slicer_service import SlicerService, SliceRequest, SliceError


def _req(tmp_path, export_args=None):
    src = tmp_path / "model.3mf"
    src.write_bytes(b"dummy")
    return SliceRequest(
        job_id=1, source_3mf=str(src), plate_number=1,
        machine_preset="Elegoo Centauri Carbon", process_preset="0.20mm Standard",
        filament_presets=["Generic PLA"], filament_colours=["#FFFFFF"],
        export_args=export_args or [],
    )


def _make_service(tmp_path):
    svc = SlicerService(orca_executable="orcaslicer", data_dir=str(tmp_path))
    svc._resolver = MagicMock()
    svc._resolver.resolve.return_value = {"name": "x"}
    return svc


def _fake_run(produce: bool):
    """Simulate OrcaSlicer: always writes plate_1.gcode to --outputdir, and the
    --export-3mf archive when requested. ``produce=False`` writes nothing."""
    def run(cmd, **kwargs):
        if produce:
            out = Path(cmd[cmd.index("--outputdir") + 1])
            (out / "plate_1.gcode").write_text("G28\nG1 X0 Y0\n")
            if "--export-3mf" in cmd:
                (out / cmd[cmd.index("--export-3mf") + 1]).write_bytes(b"PK\x03\x04 3mf")
        return MagicMock(returncode=0 if produce else 1, stdout="", stderr="boom")
    return run


@patch("app.services.slicer_service.build_project_config", return_value={})
@patch("app.services.slicer_service.build_sliceable_3mf")
def test_default_returns_raw_gcode_from_outputdir(mock_build, mock_cfg, tmp_path):
    svc = _make_service(tmp_path)
    with patch("app.services.slicer_service.subprocess.run", side_effect=_fake_run(True)) as run:
        path = svc.slice(_req(tmp_path))
    assert path.endswith(".gcode") and Path(path).read_text().startswith("G28")
    cmd = run.call_args[0][0]
    assert "--outputdir" in cmd and "--export-3mf" not in cmd  # default = raw gcode
    assert mock_build.call_args.kwargs.get("geometry_only") is False


@patch("app.services.slicer_service.build_project_config", return_value={})
@patch("app.services.slicer_service.build_sliceable_3mf")
def test_export_3mf_args_return_the_archive(mock_build, mock_cfg, tmp_path):
    svc = _make_service(tmp_path)
    req = _req(tmp_path, export_args=["--export-3mf", "mymodel.gcode.3mf"])
    with patch("app.services.slicer_service.subprocess.run", side_effect=_fake_run(True)) as run:
        path = svc.slice(req)
    assert path.endswith("mymodel.gcode.3mf")          # printer's named archive
    cmd = run.call_args[0][0]
    assert "--export-3mf" in cmd and "mymodel.gcode.3mf" in cmd


@patch("app.services.slicer_service.build_project_config", return_value={})
@patch("app.services.slicer_service.build_sliceable_3mf")
def test_recovers_geometry_only_on_failure(mock_build, mock_cfg, tmp_path):
    svc = _make_service(tmp_path)
    calls = {"n": 0}

    def fail_then_ok(cmd, **kwargs):
        calls["n"] += 1
        return _fake_run(calls["n"] == 2)(cmd, **kwargs)

    with patch("app.services.slicer_service.subprocess.run", side_effect=fail_then_ok):
        path = svc.slice(_req(tmp_path))
    assert Path(path).exists()
    assert [c.kwargs.get("geometry_only") for c in mock_build.call_args_list] == [False, True]


@patch("app.services.slicer_service.build_project_config", return_value={})
@patch("app.services.slicer_service.build_sliceable_3mf")
def test_raises_when_both_attempts_fail(mock_build, mock_cfg, tmp_path):
    svc = _make_service(tmp_path)
    with patch("app.services.slicer_service.subprocess.run", side_effect=_fake_run(False)):
        with pytest.raises(SliceError):
            svc.slice(_req(tmp_path))


@patch("app.services.slicer_service.build_project_config", return_value={})
@patch("app.services.slicer_service.stl_to_3mf")
@patch("app.services.slicer_service.build_sliceable_3mf")
def test_stl_source_uses_stl_wrapper(mock_build, mock_stl, mock_cfg, tmp_path):
    svc = _make_service(tmp_path)
    req = _req(tmp_path)
    req.source_3mf = str(tmp_path / "model.stl")
    Path(req.source_3mf).write_bytes(b"stl")
    with patch("app.services.slicer_service.subprocess.run", side_effect=_fake_run(True)):
        svc.slice(req)
    mock_stl.assert_called_once()
    mock_build.assert_not_called()


def test_raises_on_unresolvable_preset(tmp_path):
    from app.services.preset_resolver import PresetNotFoundError
    svc = SlicerService(orca_executable="orcaslicer", data_dir=str(tmp_path))
    svc._resolver = MagicMock()
    svc._resolver.resolve.side_effect = PresetNotFoundError("nope")
    with pytest.raises(SliceError, match="preset resolution failed"):
        svc.slice(_req(tmp_path))


# ── _inject_thumbnail ──────────────────────────────────────────────────────────

import struct
import zipfile as _zipfile


def _png(width: int = 64, height: int = 64) -> bytes:
    """Minimal valid PNG: signature + IHDR (with real dimensions) + IEND."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + b"\x00\x00\x00\x00"
    iend = b"\x00\x00\x00\x00IEND\xaeB`\x82"
    return sig + ihdr + iend


def _3mf_with_thumb(tmp_path, *, plate: int | None = 1, name: str | None = None) -> Path:
    """3MF ZIP containing a thumbnail at Metadata/<name> or Metadata/plate_<plate>.png."""
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
    assert "G28" in content  # original gcode preserved after the header


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


@patch("app.services.slicer_service.build_project_config", return_value={})
@patch("app.services.slicer_service.build_sliceable_3mf")
def test_slice_calls_inject_thumbnail_for_3mf_source(mock_build, mock_cfg, tmp_path):
    svc = _make_service(tmp_path)
    three_mf = _3mf_with_thumb(tmp_path, plate=1)
    req = _req(tmp_path)
    req.source_3mf = str(three_mf)

    with patch("app.services.slicer_service.subprocess.run", side_effect=_fake_run(True)):
        with patch.object(svc, "_inject_thumbnail") as mock_inject:
            svc.slice(req)

    mock_inject.assert_called_once()
    args = mock_inject.call_args[0]
    assert args[0].endswith(".gcode")
    assert args[1] == str(three_mf)
    assert args[2] == 1  # plate_number


