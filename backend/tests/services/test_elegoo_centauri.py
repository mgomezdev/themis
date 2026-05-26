import json
import time
from unittest.mock import MagicMock
import pytest
from app.services.elegoo_centauri_client import ElegooCentauriClient, ElegooState, _CAMERA_PORT


def _make_client() -> ElegooCentauriClient:
    return ElegooCentauriClient(ip_address="192.168.1.20")


def _make_ack_responder(client: ElegooCentauriClient):
    def side_effect(msg_str: str) -> None:
        data = json.loads(msg_str)
        request_id = data["Data"]["RequestID"]
        client._ack_results[request_id] = 0
        event = client._pending_acks.get(request_id)
        if event:
            event.set()
    return side_effect


def _connected_client() -> ElegooCentauriClient:
    client = _make_client()
    client._ws = MagicMock()
    client._ws.send.side_effect = _make_ack_responder(client)
    with client._lock:
        client.state.connected = True
        client.state.print_state = "standby"
    return client


# ---------------------------------------------------------------------------
# Class-level metadata
# ---------------------------------------------------------------------------

def test_printer_type():
    assert ElegooCentauriClient.printer_type == "elegoo_centauri"


def test_connection_fields_has_ip_and_port():
    fields = {f.name: f for f in ElegooCentauriClient.connection_fields()}
    assert "ip_address" in fields
    assert "port" in fields


def test_connection_fields_no_camera_url():
    # camera URL is derived from IP at the known port — not a user input
    field_names = [f.name for f in ElegooCentauriClient.connection_fields()]
    assert "camera_url" not in field_names


def test_port_default_is_3030():
    fields = {f.name: f for f in ElegooCentauriClient.connection_fields()}
    assert fields["port"].default == 3030
    assert fields["port"].required is False


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

def test_camera_capability_always_true():
    # Centauri Carbon always has a camera at port 3031
    assert _make_client().get_capabilities().camera is True


def test_pause_resume_capability():
    assert _make_client().get_capabilities().pause_resume is True


def test_gcode_not_supported():
    client = _make_client()
    assert client.gcode_supported is False
    assert client.get_capabilities().gcode is False


def test_file_upload_supported():
    assert _make_client().get_capabilities().file_upload is True


def test_chamber_light_supported():
    assert _make_client().get_capabilities().chamber_light is True


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_connected_false_initially():
    assert _make_client().connected is False


def test_initial_print_state_is_standby():
    assert _make_client().state.print_state == "standby"


# ---------------------------------------------------------------------------
# Camera URL
# ---------------------------------------------------------------------------

def test_camera_mjpeg_url_uses_derived_port():
    client = _make_client()
    assert client.camera_mjpeg_url == f"http://192.168.1.20:{_CAMERA_PORT}/video"


def test_camera_mjpeg_url_uses_video_url_when_set():
    client = _make_client()
    with client._lock:
        client.state.video_url = "http://192.168.1.20:3031/video?session=abc"
    assert client.camera_mjpeg_url == "http://192.168.1.20:3031/video?session=abc"


def test_camera_rtsp_url_is_none():
    assert _make_client().camera_rtsp_url is None


# ---------------------------------------------------------------------------
# is_idle / is_printing
# ---------------------------------------------------------------------------

def test_is_idle_when_standby():
    client = _make_client()
    client.state.print_state = "standby"
    assert client.is_idle is True


def test_is_idle_when_complete():
    client = _make_client()
    client.state.print_state = "complete"
    assert client.is_idle is True


def test_is_idle_false_when_printing():
    client = _make_client()
    client.state.print_state = "printing"
    assert client.is_idle is False


def test_is_printing_when_printing():
    client = _make_client()
    client.state.print_state = "printing"
    assert client.is_printing is True


def test_is_printing_when_warming_up():
    client = _make_client()
    client.state.print_state = "warming_up"
    assert client.is_printing is True


def test_is_printing_false_when_paused():
    client = _make_client()
    client.state.print_state = "paused"
    assert client.is_printing is False


# ---------------------------------------------------------------------------
# ElegooState compat shims
# ---------------------------------------------------------------------------

def test_state_shim_maps_print_state_to_norm():
    s = ElegooState()
    s.print_state = "printing"
    assert s.state == "RUNNING"
    s.print_state = "standby"
    assert s.state == "IDLE"
    s.print_state = "complete"
    assert s.state == "FINISH"
    s.print_state = "paused"
    assert s.state == "PAUSE"


def test_state_shim_raw_data_is_none():
    assert ElegooState().raw_data is None


def test_state_shim_current_print():
    s = ElegooState()
    s.filename = "cube.gcode"
    assert s.current_print == "cube.gcode"


def test_state_shim_remaining_time():
    s = ElegooState()
    s.total_ticks = 3600.0   # 60 minutes total
    s.current_ticks = 1800.0  # 30 minutes elapsed
    assert s.remaining_time == 30  # 30 minutes left


# ---------------------------------------------------------------------------
# check_staleness
# ---------------------------------------------------------------------------

def test_check_staleness_returns_connected():
    client = _make_client()
    assert client.check_staleness() is False
    with client._lock:
        client.state.connected = True
    assert client.check_staleness() is True


# ---------------------------------------------------------------------------
# Print control commands
# ---------------------------------------------------------------------------

def test_start_print_sends_cmd_128():
    client = _connected_client()
    client.start_print("model.gcode")
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 128
    assert sent["Data"]["Data"]["Filename"] == "model.gcode"


def test_stop_print_sends_cmd_130():
    client = _connected_client()
    client.stop_print()
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 130


def test_pause_print_sends_cmd_129():
    client = _connected_client()
    client.pause_print()
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 129


def test_resume_print_sends_cmd_131():
    client = _connected_client()
    client.resume_print()
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 131


def test_home_sends_cmd_402():
    client = _connected_client()
    client.home()
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 402
    assert sent["Data"]["Data"]["Axis"] == "XYZ"


def test_jog_z_sends_cmd_401():
    client = _connected_client()
    client.jog_z(5.0)
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 401
    assert sent["Data"]["Data"]["Axis"] == "Z"
    assert sent["Data"]["Data"]["Step"] == 5.0


# ---------------------------------------------------------------------------
# Chamber light
# ---------------------------------------------------------------------------

def test_set_chamber_light_sends_cmd_403():
    client = _connected_client()
    client.set_chamber_light(True)
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 403
    assert sent["Data"]["Data"]["LightStatus"]["SecondLight"] is True


def test_set_chamber_light_includes_current_rgb():
    client = _connected_client()
    with client._lock:
        client.state.rgb_light = [255, 128, 0]
    client.set_chamber_light(False)
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Data"]["LightStatus"]["RgbLight"] == [255, 128, 0]


def test_set_chamber_light_updates_state_on_success():
    client = _connected_client()
    with client._lock:
        client.state.chamber_light = False
    client.set_chamber_light(True)
    with client._lock:
        assert client.state.chamber_light is True


# ---------------------------------------------------------------------------
# Status message parsing
# ---------------------------------------------------------------------------

def _status_msg(status_dict: dict) -> dict:
    return {"Status": status_dict, "Topic": "sdcp/status/TESTID"}


def test_parse_status_sets_connected_true():
    client = _make_client()
    client._on_ws_message(None, json.dumps(_status_msg({
        "CurrentStatus": [0],
        "PrintInfo": {},
    })))
    assert client.connected is True


def test_parse_status_idle():
    client = _make_client()
    client._on_ws_message(None, json.dumps(_status_msg({
        "CurrentStatus": [0],
        "PrintInfo": {"Status": 0},
    })))
    assert client.state.print_state == "standby"


def test_parse_status_printing():
    client = _make_client()
    client._on_ws_message(None, json.dumps(_status_msg({
        "CurrentStatus": [1],
        "PrintInfo": {"Status": 13, "Filename": "cube.gcode", "TotalTicks": 3600, "CurrentTicks": 1200},
    })))
    assert client.state.print_state == "printing"
    assert client.state.filename == "cube.gcode"
    assert client.state.progress == pytest.approx(33.33, abs=0.1)


def test_parse_status_complete_from_code_8():
    client = _make_client()
    client._on_ws_message(None, json.dumps(_status_msg({
        "CurrentStatus": [8],
        "PrintInfo": {},
    })))
    assert client.state.print_state == "complete"


def test_parse_status_temperatures():
    client = _make_client()
    client._on_ws_message(None, json.dumps(_status_msg({
        "CurrentStatus": [0],
        "TempOfNozzle": 220.123,
        "TempOfHotbed": 60.456,
        "TempOfBox": 35.789,
        "PrintInfo": {},
    })))
    temps = client.state.temperatures
    assert temps["nozzle"] == pytest.approx(220.1)
    assert temps["bed"] == pytest.approx(60.5)
    assert temps["chamber"] == pytest.approx(35.8)


def test_parse_status_fans():
    client = _make_client()
    client._on_ws_message(None, json.dumps(_status_msg({
        "CurrentStatus": [0],
        "CurrentFanSpeed": {"ModelFan": 75, "AuxiliaryFan": 50},
        "PrintInfo": {},
    })))
    assert client.state.fan_model == 75
    assert client.state.fan_aux == 50


def test_parse_status_lights():
    client = _make_client()
    client._on_ws_message(None, json.dumps(_status_msg({
        "CurrentStatus": [0],
        "LightStatus": {"SecondLight": 1, "RgbLight": [100, 200, 50]},
        "PrintInfo": {},
    })))
    assert client.state.chamber_light is True
    assert client.state.rgb_light == [100, 200, 50]


def test_parse_status_rgb_not_overwritten_when_absent():
    client = _make_client()
    with client._lock:
        client.state.rgb_light = [10, 20, 30]
    # Message with LightStatus but no RgbLight key
    client._on_ws_message(None, json.dumps(_status_msg({
        "CurrentStatus": [0],
        "LightStatus": {"SecondLight": 0},
        "PrintInfo": {},
    })))
    assert client.state.rgb_light == [10, 20, 30]


# ---------------------------------------------------------------------------
# ACK / response parsing  (RequestID with \x01 suffix)
# ---------------------------------------------------------------------------

def test_ack_strips_x01_from_request_id():
    client = _make_client()
    import uuid
    rid = uuid.uuid4().hex
    event = __import__("threading").Event()
    client._pending_acks[rid] = event

    # Simulate printer response appending \x01
    response = {
        "Data": {
            "Cmd": 0,
            "Data": {"Ack": 0},
            "RequestID": rid + "\x01",
            "MainboardID": "TESTBOARD",
            "Result": 0,
        },
        "Topic": "sdcp/response/TESTBOARD",
    }
    client._on_ws_message(None, json.dumps(response))
    assert event.is_set()
    assert client._ack_results.get(rid) == 0


def test_ack_updates_mainboard_id():
    client = _make_client()
    response = {
        "Data": {
            "Cmd": 0,
            "Data": {},
            "RequestID": "someid\x01",
            "MainboardID": "NEWBOARD123",
            "Result": 0,
        },
        "Topic": "sdcp/response/NEWBOARD123",
    }
    client._on_ws_message(None, json.dumps(response))
    with client._lock:
        assert client.state.mainboard_id == "NEWBOARD123"


# ---------------------------------------------------------------------------
# Port coercion (frontend sends strings)
# ---------------------------------------------------------------------------

def test_port_coerced_from_string():
    client = ElegooCentauriClient(ip_address="1.2.3.4", port="3030")
    assert client._port == 3030
    assert isinstance(client._port, int)


# ---------------------------------------------------------------------------
# fan_box field
# ---------------------------------------------------------------------------

def test_elegoo_state_has_fan_box_defaulting_to_zero():
    client = _make_client()
    assert client.state.fan_box == 0


def test_parse_status_msg_reads_box_fan():
    client = _make_client()
    msg = {
        "Status": {
            "CurrentFanSpeed": {"ModelFan": 80, "AuxiliaryFan": 60, "BoxFan": 40},
            "CurrentStatus": [],
            "PrintInfo": {},
        }
    }
    client._parse_status_msg(msg)
    assert client.state.fan_box == 40


def test_parse_status_msg_box_fan_defaults_to_zero_when_absent():
    client = _make_client()
    msg = {
        "Status": {
            "CurrentFanSpeed": {"ModelFan": 80, "AuxiliaryFan": 60},
            "CurrentStatus": [],
            "PrintInfo": {},
        }
    }
    client._parse_status_msg(msg)
    assert client.state.fan_box == 0


# ---------------------------------------------------------------------------
# Serializer — fan fields
# ---------------------------------------------------------------------------

def test_serialize_elegoo_exposes_three_fan_fields():
    from app.services.printer_manager import _serialize_elegoo
    from app.services.elegoo_centauri_client import ElegooState
    state = ElegooState()
    state.connected = True
    state.fan_model = 80
    state.fan_aux = 60
    state.fan_box = 40
    result = _serialize_elegoo(state, 1)
    assert result["fan_model"] == 80
    assert result["fan_aux"] == 60
    assert result["fan_box"] == 40


def test_serialize_elegoo_no_longer_has_fan_speed():
    from app.services.printer_manager import _serialize_elegoo
    from app.services.elegoo_centauri_client import ElegooState
    result = _serialize_elegoo(ElegooState(), 1)
    assert "fan_speed" not in result
