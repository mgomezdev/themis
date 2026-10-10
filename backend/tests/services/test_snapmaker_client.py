from unittest.mock import MagicMock, patch
from app.plugins.snapmaker.client import SnapmakerExtendedClient, SnapmakerState


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
    with patch("app.plugins.snapmaker.client.httpx.post") as post:
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
    with patch("app.plugins.snapmaker.client.httpx.post") as post:
        post.return_value = MagicMock(raise_for_status=MagicMock())
        assert c.upload_file(b"G28\n", "cube.gcode") is True
        assert post.call_args[0][0].endswith("/server/files/upload")
        assert "files" in post.call_args.kwargs


def test_connected_requires_klippy_ready():
    c = _client()
    c.state.connected = True
    c.state.klippy_ready = False
    assert c.connected is False
    c.state.klippy_ready = True
    assert c.connected is True


def test_error_state_normalized():
    c = _client()
    c._apply_status({"print_stats": {"state": "error"}})
    assert c.state.state == "FAILED"


# Captured from a real U1 (T0 empty, T1-T3 PLA): the per-tool filament setup the printer reports.
_TASK_CONFIG = {
    "filament_vendor": ["NONE", "Generic", "Generic", "Generic"], "filament_type": ["NONE", "PLA", "PLA", "PLA"],
    "filament_sub_type": ["NONE", "", "", ""], "filament_exist": [False, True, True, True],
    "filament_color_rgba": ["FFFFFFFF", "8C9099FF", "519F61FF", "519F61FF"],
}


def test_loaded_filaments_come_from_print_task_config_positionally():
    c = _client()
    c._apply_status({"print_task_config": _TASK_CONFIG})
    trays = c.get_loaded_filaments()
    assert [t["slot"] for t in trays] == [0, 1, 2, 3]                       # list index == tool index
    assert trays[0] == {"slot": 0, "filament_id": None, "name": "", "type": "", "color": "", "empty": True}   # empty tool kept as a placeholder
    assert trays[1] == {"slot": 1, "filament_id": None, "name": "Generic PLA", "type": "PLA", "color": "#8C9099"}
    assert trays[3]["color"] == "#519F61"


def test_task_config_notifications_merge_changed_keys_and_fire_only_on_change():
    import asyncio
    c = _client()
    seen: list = []

    async def on_change(trays):
        seen.append(trays)
    c._on_ams_change = on_change
    loop = asyncio.new_event_loop()
    c._loop = loop
    try:
        c._apply_status({"print_task_config": _TASK_CONFIG})
        c._apply_status({"print_task_config": {"filament_type": ["NONE", "PETG", "PLA", "PLA"]}})   # partial update
        c._apply_status({"print_task_config": {"filament_type": ["NONE", "PETG", "PLA", "PLA"]}})   # no change
        loop.run_until_complete(asyncio.sleep(0.05))
    finally:
        loop.close()
    assert len(seen) == 2
    assert seen[1][1]["type"] == "PETG" and seen[1][1]["color"] == "#8C9099"                         # colour kept from the first report


def test_a_tool_with_no_spool_is_not_loaded_for_the_queue():
    from app.services.queue_engine import _mapped_tools_loaded, _slot_for_config
    from app.plugins.snapmaker.client import _trays_from_task_config
    loaded = _trays_from_task_config(_TASK_CONFIG)
    cfg = MagicMock(tool_index=0, filament_type=None, filament_color=None)
    assert _slot_for_config(cfg, loaded) is None                                                      # T0 is empty
    assert _slot_for_config(MagicMock(tool_index=2), loaded)["type"] == "PLA"
    assert _mapped_tools_loaded([{"tool_index": 0}], loaded) is False
    assert _mapped_tools_loaded([{"tool_index": 1}, {"tool_index": 3}], loaded) is True
    any_ask = MagicMock(tool_index=None, filament_type="any", filament_color="any")
    assert _slot_for_config(any_ask, loaded)["slot"] == 1                                             # "any" skips the empty T0


def test_a_hand_entered_slot_without_a_type_still_counts_as_loaded():
    """Only the U1's explicit empty-tool placeholder is "not loaded"; a manual slot with a blank type (e.g. from the
    scan-spool flow) used to match "any" asks and tool_index, and must keep doing so."""
    from app.services.queue_engine import _mapped_tools_loaded, _slot_for_config
    manual = [{"slot": 0, "type": "", "name": "", "color": "", "filament_profile": "Generic PLA"}]
    assert _slot_for_config(MagicMock(tool_index=0), manual) is manual[0]
    assert _slot_for_config(MagicMock(tool_index=None, filament_type="any", filament_color="any"), manual) is manual[0]
    assert _mapped_tools_loaded([{"tool_index": 0}], manual) is True
