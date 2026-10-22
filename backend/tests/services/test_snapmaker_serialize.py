from app.plugins.snapmaker.client import SnapmakerState, serialize_snapmaker


def test_serialize_snapmaker_shape():
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
    d = serialize_snapmaker(s, 7)
    assert d["printer_type"] == "snapmaker_extended"
    assert d["id"] == 7
    assert d["connected"] is True
    assert d["state"] == "RUNNING"
    assert d["current_print"] == "cube.gcode"
    assert d["progress"] == 50.0  # percent, like the other vendors (Klipper reports 0..1)
    assert d["remaining_time"] == 20
    assert d["temperatures"]["bed"] == 60.0
    assert d["temperatures"]["nozzle"] == 210.0


def test_snapmaker_camera_url_is_the_mjpeg_endpoint_not_the_404_one():
    from app.plugins.snapmaker.client import SnapmakerExtendedClient
    assert SnapmakerExtendedClient(ip_address="192.168.0.119").camera_mjpeg_url == "http://192.168.0.119/webcam/stream.mjpg"
