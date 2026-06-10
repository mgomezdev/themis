from unittest.mock import patch, MagicMock
from app.services.slicer_service import SlicerService, SliceRequest


def _req(**kw):
    base = dict(job_id=1, source_3mf="x.stl", plate_number=0, machine_preset="M",
                process_preset="P", filament_presets=["F"])
    base.update(kw); return SliceRequest(**base)


def test_prepare_hook_invoked_before_run(tmp_path):
    svc = SlicerService.__new__(SlicerService); svc._data_dir = tmp_path
    hook = MagicMock()
    with patch.object(SlicerService, "_build_config", return_value={"k": "v"}), \
         patch("app.services.slicer_service.stl_to_3mf"), \
         patch.object(SlicerService, "_run", return_value="out.gcode"):
        svc.slice(_req(source_3mf="x.stl", prepare_hook=hook))
        assert hook.called


def test_no_hook_is_fine(tmp_path):
    svc = SlicerService.__new__(SlicerService); svc._data_dir = tmp_path
    with patch.object(SlicerService, "_build_config", return_value={"k": "v"}), \
         patch("app.services.slicer_service.stl_to_3mf"), \
         patch.object(SlicerService, "_run", return_value="out.gcode"):
        assert svc.slice(_req(source_3mf="x.stl")) == "out.gcode"
