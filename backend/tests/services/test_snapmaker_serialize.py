from app.services.printer_manager import _STATUS_SERIALIZERS
from app.services.snapmaker_client import SnapmakerState


def test_serialize_snapmaker_shape():
    assert "snapmaker_extended" in _STATUS_SERIALIZERS
    s = SnapmakerState()
    s.connected = True
    s.klippy_ready = True
    s.print_state = "printing"
    s.filename = "cube.gcode"
    s.progress = 0.5
    s.print_duration = 1200.0  # 20 minutes elapsed
    s.extruder_temps = [210.0, 0.0, 0.0, 0.0]
    s.bed_temp = 60.0
    assert s.remaining_time == 20  # 1200s * 0.5 / 0.5 = 1200s = 20m remaining
    d = _STATUS_SERIALIZERS["snapmaker_extended"](s, 7)
    assert d["printer_type"] == "snapmaker_extended"
    assert d["id"] == 7
    assert d["connected"] is True
    assert d["state"] == "RUNNING"
    assert d["current_print"] == "cube.gcode"
    assert d["progress"] == 0.5
    assert d["remaining_time"] == 20
    assert d["temperatures"]["bed"] == 60.0
    assert d["temperatures"]["nozzle"] == 210.0
