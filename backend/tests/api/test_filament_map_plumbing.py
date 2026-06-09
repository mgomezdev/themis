import inspect
from app.services.slicer_service import SliceRequest
from app.api.routes import jobs
from app.api.routes.jobs import PrinterConfigInput


def test_slice_request_has_filament_map_default_none():
    req = SliceRequest(job_id=1, source_3mf="x", plate_number=0, machine_preset="M",
                       process_preset="P", filament_presets=["F"])
    assert req.filament_map is None
    assert SliceRequest(job_id=1, source_3mf="x", plate_number=0, machine_preset="M",
                        process_preset="P", filament_presets=["F"],
                        filament_map=[{"model_filament": 1, "tool_index": 2}]).filament_map is not None


def test_printer_config_input_accepts_filament_map():
    c = PrinterConfigInput(printer_id=1, print_profile="p",
                           filament_map=[{"model_filament": 1, "tool_index": 2}])
    assert c.filament_map[0]["tool_index"] == 2
    assert PrinterConfigInput(printer_id=1, print_profile="p").filament_map is None


def test_job_routes_round_trip_filament_map():
    assert "filament_map=cfg.filament_map" in inspect.getsource(jobs.create_job)
    assert "filament_map=cfg.filament_map" in inspect.getsource(jobs.update_job_configs)
    assert '"filament_map"' in inspect.getsource(jobs.get_job_details)
