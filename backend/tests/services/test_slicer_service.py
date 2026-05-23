# backend/tests/services/test_slicer_service.py
import subprocess
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from app.services.slicer_service import SlicerService, SliceError


def test_slice_calls_orcaslicer(tmp_path):
    gcode_out = tmp_path / "gcode" / "1"
    svc = SlicerService(orca_executable="orcaslicer", data_dir=str(tmp_path))

    def fake_run(cmd, **kwargs):
        # Simulate OrcaSlicer creating a gcode file
        (tmp_path / "gcode" / "1").mkdir(parents=True, exist_ok=True)
        (tmp_path / "gcode" / "1" / "output.gcode").write_text("G28\n")
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("app.services.slicer_service.subprocess.run", side_effect=fake_run) as mock_run:
        path = svc.slice(
            job_id=1,
            file_path="/data/uploads/abc/model.3mf",
            plate_number=1,
            print_profile="0.20mm Standard",
            filament_profile="Bambu PLA Basic",
        )

    assert mock_run.called
    cmd = mock_run.call_args[0][0]
    assert "orcaslicer" in cmd[0]
    assert "--plate" in cmd
    assert "1" in cmd
    assert path.endswith(".gcode")


def test_slice_raises_on_nonzero_exit(tmp_path):
    svc = SlicerService(orca_executable="orcaslicer", data_dir=str(tmp_path))

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 1
        result.stderr = "Profile not found"
        result.stdout = ""
        return result

    with patch("app.services.slicer_service.subprocess.run", side_effect=fake_run):
        with pytest.raises(SliceError, match="Profile not found"):
            svc.slice(
                job_id=1,
                file_path="/data/uploads/abc/model.3mf",
                plate_number=1,
                print_profile="0.20mm Standard",
                filament_profile="Bambu PLA Basic",
            )


def test_slice_raises_when_no_gcode_produced(tmp_path):
    svc = SlicerService(orca_executable="orcaslicer", data_dir=str(tmp_path))

    def fake_run(cmd, **kwargs):
        # Creates the output dir but no .gcode file
        (tmp_path / "gcode" / "1").mkdir(parents=True, exist_ok=True)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("app.services.slicer_service.subprocess.run", side_effect=fake_run):
        with pytest.raises(SliceError, match="no .gcode file"):
            svc.slice(
                job_id=1,
                file_path="/data/uploads/abc/model.3mf",
                plate_number=1,
                print_profile="0.20mm Standard",
                filament_profile="Bambu PLA Basic",
            )
