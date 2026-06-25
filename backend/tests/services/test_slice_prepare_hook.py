import pytest
from unittest.mock import patch, MagicMock
from app.services.slicer_service import SlicerService, SliceRequest, SliceError


def _req(**kw):
    base = dict(job_id=1, source_3mf="x.stl", plate_number=1, machine_preset="M",
                process_preset="P", filament_presets=["F"])
    base.update(kw)
    return SliceRequest(**base)


def test_prepare_hook_raises_slice_error(tmp_path):
    """prepare_hook is not supported in sidecar-only mode — must raise SliceError."""
    svc = SlicerService.__new__(SlicerService)
    svc._data_dir = tmp_path
    svc._catalog_cache = None
    svc._catalog_ts = 0.0

    with patch("app.config.get_orca_sidecar_url", return_value="http://orca:5000"):
        with pytest.raises(SliceError, match="prepare_hook"):
            svc.slice(_req(prepare_hook=MagicMock()))


def test_no_hook_with_sidecar_succeeds(tmp_path):
    """A request without a prepare_hook routes to sidecar successfully."""
    svc = SlicerService.__new__(SlicerService)
    svc._data_dir = tmp_path
    svc._catalog_cache = {
        "machine": [{"name": "M", "uuid": "m1"}],
        "process": [{"name": "P", "uuid": "p1"}],
        "filament": [{"name": "F", "uuid": "f1"}],
    }
    svc._catalog_ts = float("inf")

    with patch("app.config.get_orca_sidecar_url", return_value="http://orca:5000"), \
         patch.object(SlicerService, "_execute_slice_by_ids", return_value="out.gcode"):
        assert svc.slice(_req()) == "out.gcode"
