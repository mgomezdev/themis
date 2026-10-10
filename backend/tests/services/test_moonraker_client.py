"""The generic Moonraker client (BIZ-148) against the virtual Moonraker: connection, telemetry normalisation, upload / start / pause /
resume / cancel, completion, errors, alarms, optional hardware that is absent, camera discovery and the file browser."""
import asyncio
import json
from unittest.mock import MagicMock

import httpx
import pytest

from app.plugins.moonraker.client import MoonrakerClient
from app.services.abstract_printer_client import FileTooLargeError
from tests.virtual_printers import fake_moonraker as fm
from tests.virtual_printers.alarm_payloads import moonraker_status


def client(toolheads=1, api_key=None, **kw) -> MoonrakerClient:
    return MoonrakerClient(ip_address="192.0.2.30", api_key=api_key, toolheads=toolheads, **kw)


@pytest.fixture
def server(monkeypatch):
    s = fm.VirtualMoonraker(files={"cube.gcode": {"data": b"G28\n", "modified": 1_759_000_000.0,
                                                  "meta": {"estimated_time": 600, "filament_weight_total": 3.5, "slicer": "Orca"}}})
    fm.install(monkeypatch, s)
    return s


def ready(c: MoonrakerClient) -> MoonrakerClient:
    c.state.connected = True
    c.state.klippy_ready = True
    return c


# --- connection -----------------------------------------------------------------------------------------------------

def test_on_open_subscribes_to_the_standard_objects_and_one_extruder_per_declared_toolhead_and_asks_for_webcams():
    c, ws = client(toolheads=2), fm.FakeWebSocket()
    c._ws = ws

    c._on_ws_open(ws)

    assert ws.methods() == ["server.info", "printer.objects.subscribe", "printer.objects.query", "server.webcams.list"]
    objs = ws.sent[1]["params"]["objects"]
    assert {"print_stats", "webhooks", "display_status", "heater_bed", "toolhead", "fan", "extruder", "extruder1"} <= set(objs)
    assert "extruder2" not in objs                                       # only what the printer was declared to have
    assert c.state.connected is True and c.connected is False            # the socket is open but Klipper has not said ready


def test_the_client_is_connected_only_once_klippy_reports_ready():
    c = client()
    c._on_ws_open(None)
    c._on_ws_message(None, json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"klippy_state": "startup"}}))
    assert c.connected is False
    c._on_ws_message(None, json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"klippy_state": "ready"}}))
    assert c.connected is True
    c._on_ws_close(None)
    assert c.connected is False


def test_a_status_query_reply_applies_like_a_pushed_update():
    c = ready(client())
    c._on_ws_message(None, json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"status": {"print_stats": {"state": "printing", "filename": "a.gcode"}}}}))
    assert (c.state.state, c.state.current_print) == ("RUNNING", "a.gcode")


# --- telemetry ------------------------------------------------------------------------------------------------------

def test_telemetry_is_normalised_for_a_dual_extruder_printer():
    c = ready(client(toolheads=2))
    c._on_ws_message(None, fm.status_frame(
        print_stats={"state": "printing", "filename": "benchy.gcode", "print_duration": 300.0, "info": {"current_layer": 10, "total_layer": 100}},
        display_status={"progress": 0.5}, heater_bed={"temperature": 59.9, "target": 60.0},
        extruder={"temperature": 200.0, "target": 210.0}, extruder1={"temperature": 150.0, "target": 0.0},
        toolhead={"extruder": "extruder1"}, fan={"speed": 0.4}))

    d = c.serialize_state(7)

    assert (d["printer_type"], d["id"], d["connected"], d["state"]) == ("moonraker", 7, True, "RUNNING")
    assert (d["progress"], d["remaining_time"], d["layer_num"], d["total_layers"]) == (50.0, 5, 10, 100)
    assert d["current_print"] == "benchy.gcode" and d["fan_model"] == 40
    t = d["temperatures"]
    assert (t["nozzle"], t["nozzle_target"], t["bed"], t["bed_target"]) == (150.0, 0.0, 59.9, 60.0)       # the ACTIVE extruder
    assert [e["index"] for e in t["extruders"]] == [0, 1]
    assert c.get_capabilities().multi_nozzle is True


def test_missing_optional_objects_never_fail_telemetry_and_capabilities_degrade():
    c = ready(client())
    c._on_ws_message(None, fm.status_frame(print_stats={"state": "standby"}, extruder={"temperature": 24.0, "target": 0.0}))   # no bed, fan, display

    d = c.serialize_state(1)
    caps = c.get_capabilities()

    assert d["state"] == "IDLE" and d["fan_model"] == 0 and d["temperatures"]["bed"] == 0.0
    assert (caps.fan_control, caps.multi_nozzle, caps.camera) == (False, False, False)
    assert c.set_fan_speeds(50, 0, 0) is False                           # refuses instead of sending a command nothing honours


def test_an_update_for_an_extruder_the_printer_was_not_declared_with_is_ignored_not_an_error():
    c = ready(client(toolheads=1))
    c._apply_status({"extruder": {"temperature": 10.0}, "extruder3": {"temperature": 99.0}, "toolhead": {"extruder": "extruder3"}})
    assert c.state.temperatures["nozzle"] == 10.0 and len(c.state.extruder_temps) == 1


def test_a_garbled_or_unknown_frame_is_ignored():
    c = ready(client())
    for frame in ("not json", json.dumps({"method": "notify_something_new", "params": []}), json.dumps({"result": "ok"})):
        c._on_ws_message(None, frame)
    assert c.connected is True


@pytest.mark.parametrize("bad", ["abc", None, 0, 99])
def test_a_bad_toolhead_setting_falls_back_to_a_sane_count(bad):
    n = len(client(toolheads=bad).state.extruder_temps)
    assert 1 <= n <= 8


# --- completion + state -----------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_completion_fires_the_callback_exactly_once_per_print():
    done = asyncio.Event()
    calls = []

    async def on_complete(state):
        calls.append(state.print_state)
        done.set()

    c = ready(client(on_print_complete=on_complete))
    c._loop = asyncio.get_running_loop()
    c._apply_status({"print_stats": {"state": "printing"}})
    c._apply_status({"print_stats": {"state": "complete"}})
    c._apply_status({"print_stats": {"state": "complete"}})              # a repeat of the same state is not a second completion
    await asyncio.wait_for(done.wait(), 1)
    await asyncio.sleep(0)

    assert calls == ["complete"]
    assert c.is_idle is True and c.is_printing is False


def test_idle_and_printing_follow_the_klipper_print_state():
    c = ready(client())
    for raw, idle, printing in (("standby", True, False), ("printing", False, True), ("paused", False, True),
                                ("cancelled", True, False), ("error", False, False)):
        c._apply_status({"print_stats": {"state": raw}})
        assert (c.is_idle, c.is_printing) == (idle, printing), raw
    assert c.state.state == "FAILED"


# --- upload / start / pause / resume / cancel -------------------------------------------------------------------------------

def test_upload_start_pause_resume_cancel_drive_the_printer_end_to_end(server):
    c = ready(client())

    assert c.upload_file(b"G28\nG1 X10\n", "part.gcode") is True
    assert server.files["part.gcode"]["data"] == b"G28\nG1 X10\n"
    assert c.start_print("part.gcode") is True and (server.started, server.print_state) == (["part.gcode"], "printing")
    assert c.pause_print() is True and server.print_state == "paused"
    assert c.resume_print() is True and server.print_state == "printing"
    assert c.stop_print() is True and server.print_state == "cancelled"
    assert c.send_gcode("M104 S200") and server.gcode_scripts == ["M104 S200"]
    assert c.set_bed_temp(60) and server.gcode_scripts[-1] == "M140 S60"
    assert c.home() and server.gcode_scripts[-1] == "G28"


def test_a_rejected_control_command_is_a_false_not_an_exception(server):
    c = ready(client())
    assert c.pause_print() is False                                      # nothing is printing: Klipper answers 400
    server.fail_paths["/printer/print/start"] = 500
    assert c.start_print("x.gcode") is False
    server.fail_paths["/server/files/upload"] = 503
    assert c.upload_file(b"G28", "x.gcode") is False


def test_the_api_key_is_sent_and_a_wrong_or_missing_one_is_refused(server):
    server.api_key = "k3y"
    assert client(api_key="k3y").upload_file(b"G28", "a.gcode") is True
    assert client(api_key="nope").upload_file(b"G28", "b.gcode") is False
    assert client().start_print("a.gcode") is False
    assert "b.gcode" not in server.files


# --- alarms -----------------------------------------------------------------------------------------------------------

def test_klipper_problems_are_normalised_to_alarms():
    c = ready(client())
    assert c.get_alarms() == []
    c._apply_status(moonraker_status({"state": "shutdown", "state_message": "MCU 'mcu' shutdown: Timer too close"})["params"][0])
    (alarm,) = c.get_alarms()
    assert (alarm.code, alarm.severity, alarm.source) == ("KLIPPER_SHUTDOWN", "fatal", "klipper") and "Timer too close" in alarm.message

    c._apply_status(moonraker_status({"state": "ready", "state_message": "Printer is ready"}, {"state": "error", "message": "Heater extruder not heating"})["params"][0])
    (alarm,) = c.get_alarms()
    assert (alarm.code, alarm.severity) == ("KLIPPER_PRINT_ERROR", "error") and "not heating" in alarm.message


def test_a_klippy_shutdown_notice_marks_the_printer_not_ready_and_asks_why():
    c, ws = ready(client()), fm.FakeWebSocket()
    c._ws = ws

    c._on_ws_message(None, json.dumps({"jsonrpc": "2.0", "method": "notify_klippy_shutdown"}))

    assert c.connected is False and ws.methods() == ["printer.info"]
    assert c.get_alarms()[0].code == "KLIPPER_SHUTDOWN"


# --- camera discovery -------------------------------------------------------------------------------------------------------

def _webcams(c, cams):
    c._on_ws_message(None, json.dumps({"jsonrpc": "2.0", "id": 4, "result": {"webcams": cams}}))


def test_the_first_enabled_mjpeg_webcam_becomes_the_camera_with_an_absolute_url():
    c = client()
    assert c.camera_mjpeg_url is None and c.get_capabilities().camera is False
    _webcams(c, [{"name": "off", "enabled": False, "service": "mjpegstreamer", "stream_url": "/webcam0/?action=stream"},
                 {"name": "main", "enabled": True, "service": "mjpegstreamer", "stream_url": "/webcam/?action=stream"}])
    assert c.camera_mjpeg_url == "http://192.0.2.30/webcam/?action=stream" and c.get_capabilities().camera is True


@pytest.mark.parametrize("cams", [[], [{"enabled": True, "service": "webrtc-camerastreamer", "stream_url": "/webcam/webrtc"}],
                                  [{"enabled": True, "service": "mjpegstreamer"}], ["junk"]])
def test_no_usable_webcam_leaves_the_camera_unsupported_rather_than_guessing(cams):
    c = client()
    _webcams(c, cams)
    assert c.camera_mjpeg_url is None and c.camera_configured is False


# --- file browser -------------------------------------------------------------------------------------------------------------

def test_the_file_browser_lists_downloads_and_deletes(server):
    c = ready(client())

    (f,) = c.list_files()
    assert (f.id, f.size, f.metadata) == ("cube.gcode", 4, {"estimated_seconds": 600, "filament_grams": 3.5, "slicer": "Orca"})
    assert c.download_file("cube.gcode") == b"G28\n"
    with pytest.raises(FileTooLargeError):
        c.download_file("cube.gcode", max_bytes=1)
    assert c.delete_file("cube.gcode") is True and c.list_files() == []


def test_path_traversal_in_file_ids_is_refused(server):
    c = ready(client())
    with pytest.raises(ValueError):
        c.list_files("../etc")
    assert c.delete_file("../secret.gcode") is False


def test_a_listing_failure_raises_so_empty_is_distinguishable_from_unreachable(server):
    server.fail_paths["/server/files/directory"] = 500
    with pytest.raises(httpx.HTTPStatusError):
        ready(client()).list_files()


# --- the form + discovery ------------------------------------------------------------------------------------------------------

def test_connection_fields_carry_the_declared_toolhead_count_as_the_default():
    class Model:
        toolheads = 2
    fields = {f.name: f for f in MoonrakerClient.connection_fields_for(Model())}
    assert set(fields) == {"ip_address", "port", "api_key", "toolheads"}
    assert fields["toolheads"].default == 2 and fields["api_key"].field_type == "password"
    assert MoonrakerClient.connection_fields_for(None)[-1].default == 1


@pytest.mark.asyncio
async def test_discovery_finds_a_moonraker_and_flags_one_that_needs_a_key():
    from tests.virtual_printers.virtual_network import VirtualNetwork
    net = VirtualNetwork()
    net.add_moonraker("192.168.7.30", needs_key=True)
    net.add_moonraker("192.168.7.31")

    locked = await MoonrakerClient.discover_host(net, "192.168.7.30")
    open_ = await MoonrakerClient.discover_host(net, "192.168.7.31")
    nothing = await MoonrakerClient.discover_host(net, "192.168.7.99")

    assert (locked.printer_type, locked.note) == ("moonraker", "Requires an API key")
    assert (open_.printer_type, open_.connection_config) == ("moonraker", {"ip_address": "192.168.7.31", "port": 7125})
    assert nothing is None
