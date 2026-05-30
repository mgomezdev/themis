# backend/tests/services/test_slicer_service.py
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.slicer_service import SlicerService, SliceRequest, SliceError


def _req(tmp_path):
    src = tmp_path / "model.3mf"
    src.write_bytes(b"dummy")
    return SliceRequest(
        job_id=1,
        source_3mf=str(src),
        plate_number=1,
        machine_preset="Elegoo Centauri Carbon",
        process_preset="0.20mm Standard",
        filament_presets=["Generic PLA"],
        filament_colours=["#FFFFFF"],
    )


def _make_service(tmp_path):
    svc = SlicerService(orca_executable="orcaslicer", data_dir=str(tmp_path))
    # Isolate orchestration from real OrcaSlicer config + 3MF assembly.
    svc._resolver = MagicMock()
    svc._resolver.resolve.return_value = {"name": "x"}
    return svc


def _fake_run_writing_gcode(export_has_gcode: bool):
    """Build a subprocess.run fake that simulates OrcaSlicer writing (or not) a
    .gcode.3mf at the --export-3mf path."""
    def fake_run(cmd, **kwargs):
        export = Path(cmd[cmd.index("--export-3mf") + 1])
        if export_has_gcode:
            with zipfile.ZipFile(export, "w") as z:
                z.writestr("Metadata/plate_1.gcode", "G28\nG1 X0 Y0\n")
        result = MagicMock(returncode=0 if export_has_gcode else 1, stdout="", stderr="boom")
        return result
    return fake_run


@patch("app.services.slicer_service.build_project_config", return_value={})
@patch("app.services.slicer_service.build_sliceable_3mf")
def test_slice_success_extracts_gcode(mock_build, mock_cfg, tmp_path):
    svc = _make_service(tmp_path)
    with patch("app.services.slicer_service.subprocess.run", side_effect=_fake_run_writing_gcode(True)) as run:
        path = svc.slice(_req(tmp_path))

    assert Path(path).read_text().startswith("G28")
    cmd = run.call_args[0][0]
    assert "--slice" in cmd and "--export-3mf" in cmd
    assert "--outputdir" not in cmd  # path-doubling bug guard
    # Primary attempt preserves model_settings (geometry_only False)
    assert mock_build.call_args.kwargs.get("geometry_only") is False


@patch("app.services.slicer_service.build_project_config", return_value={})
@patch("app.services.slicer_service.build_sliceable_3mf")
def test_slice_recovers_geometry_only_on_failure(mock_build, mock_cfg, tmp_path):
    svc = _make_service(tmp_path)
    calls = {"n": 0}

    def run_fail_then_succeed(cmd, **kwargs):
        calls["n"] += 1
        return _fake_run_writing_gcode(calls["n"] == 2)(cmd, **kwargs)

    with patch("app.services.slicer_service.subprocess.run", side_effect=run_fail_then_succeed):
        path = svc.slice(_req(tmp_path))

    assert Path(path).exists()
    # Two build calls: primary (preserve) then recovery (geometry_only).
    flags = [c.kwargs.get("geometry_only") for c in mock_build.call_args_list]
    assert flags == [False, True]


@patch("app.services.slicer_service.build_project_config", return_value={})
@patch("app.services.slicer_service.build_sliceable_3mf")
def test_slice_raises_when_both_attempts_fail(mock_build, mock_cfg, tmp_path):
    svc = _make_service(tmp_path)
    with patch("app.services.slicer_service.subprocess.run", side_effect=_fake_run_writing_gcode(False)):
        with pytest.raises(SliceError):
            svc.slice(_req(tmp_path))


def test_slice_raises_on_unresolvable_preset(tmp_path):
    from app.services.preset_resolver import PresetNotFoundError
    svc = SlicerService(orca_executable="orcaslicer", data_dir=str(tmp_path))
    svc._resolver = MagicMock()
    svc._resolver.resolve.side_effect = PresetNotFoundError("nope")
    with pytest.raises(SliceError, match="preset resolution failed"):
        svc.slice(_req(tmp_path))
