from unittest.mock import MagicMock, patch
from app.services.snapmaker_client import SnapmakerExtendedClient, SnapmakerState


def _client():
    return SnapmakerExtendedClient(ip_address="192.168.0.119", port=7125)


def test_connection_fields():
    names = [f.name for f in SnapmakerExtendedClient.connection_fields()]
    assert names == ["ip_address", "port", "api_key"]


def test_control_endpoint():
    assert _client().control_endpoint() == ("192.168.0.119", 7125)


def test_is_idle_and_printing_from_print_state():
    c = _client()
    c.state.print_state = "standby"
    assert c.is_idle is True and c.is_printing is False
    c.state.print_state = "printing"
    assert c.is_idle is False and c.is_printing is True
    c.state.print_state = "paused"
    assert c.is_printing is True
    c.state.print_state = "complete"
    assert c.is_idle is True


def test_apply_status_updates_state():
    c = _client()
    c._apply_status({
        "print_stats": {"state": "printing", "filename": "cube.gcode",
                        "print_duration": 120.0, "info": {"current_layer": 5, "total_layer": 100}},
        "display_status": {"progress": 0.25},
        "heater_bed": {"temperature": 60.0, "target": 60.0},
        "extruder": {"temperature": 215.0, "target": 220.0},
        "toolhead": {"extruder": "extruder"},
    })
    assert c.state.print_state == "printing"
    assert c.state.state == "RUNNING"           # normalized
    assert c.state.current_print == "cube.gcode"
    assert c.state.progress == 0.25
    assert c.state.layer_num == 5 and c.state.total_layers == 100
    temps = c.state.temperatures
    assert temps["bed"] == 60.0 and temps["nozzle"] == 215.0
    assert temps["extruders"][0]["temp"] == 215.0


def test_print_complete_fires_once_on_transition():
    c = _client()
    c._fire_print_complete = MagicMock()
    c._apply_status({"print_stats": {"state": "printing"}})
    c._apply_status({"print_stats": {"state": "complete"}})
    c._apply_status({"print_stats": {"state": "complete"}})  # no re-fire
    assert c._fire_print_complete.call_count == 1


def test_http_control_calls():
    c = _client()
    with patch("app.services.snapmaker_client.httpx.post") as post:
        post.return_value = MagicMock(raise_for_status=MagicMock())
        assert c.start_print("cube.gcode") is True
        url, kw = post.call_args[0][0], post.call_args.kwargs
        assert url.endswith("/printer/print/start") and kw["params"]["filename"] == "cube.gcode"

        c.pause_print();  assert post.call_args[0][0].endswith("/printer/print/pause")
        c.resume_print(); assert post.call_args[0][0].endswith("/printer/print/resume")
        c.stop_print();   assert post.call_args[0][0].endswith("/printer/print/cancel")
        c.send_gcode("M104 S200")
        assert post.call_args[0][0].endswith("/printer/gcode/script")
        assert post.call_args.kwargs["params"]["script"] == "M104 S200"


def test_upload_file_posts_multipart():
    c = _client()
    with patch("app.services.snapmaker_client.httpx.post") as post:
        post.return_value = MagicMock(raise_for_status=MagicMock())
        assert c.upload_file(b"G28\n", "cube.gcode") is True
        assert post.call_args[0][0].endswith("/server/files/upload")
        assert "files" in post.call_args.kwargs
