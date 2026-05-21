import json
import threading
import uuid
from unittest.mock import MagicMock
import pytest
from app.services.elegoo_centauri_client import ElegooCentauriClient, ElegooState


def _make_client(camera_url: str = "") -> ElegooCentauriClient:
    return ElegooCentauriClient(ip_address="192.168.1.20", camera_url=camera_url)


def _make_ack_responder(client: ElegooCentauriClient):
    def side_effect(msg_str: str) -> None:
        data = json.loads(msg_str)
        request_id = data["Data"]["RequestID"]
        client._ack_results[request_id] = 0
        event = client._pending_acks.get(request_id)
        if event:
            event.set()
    return side_effect


def _connected_client(camera_url: str = "") -> ElegooCentauriClient:
    client = _make_client(camera_url=camera_url)
    client._ws = MagicMock()
    client._ws.send.side_effect = _make_ack_responder(client)
    client.state.connected = True
    client.state.print_state = "IDLE"
    return client


def test_printer_type():
    assert ElegooCentauriClient.printer_type == "elegoo_centauri"


def test_connection_fields_no_camera():
    fields = {f.name: f for f in ElegooCentauriClient.connection_fields()}
    assert "ip_address" in fields
    assert "port" in fields
    assert "camera_url" in fields
    assert fields["camera_url"].required is False


def test_connected_false_initially():
    client = _make_client()
    assert client.connected is False


def test_camera_capability_when_url_set():
    client = _make_client(camera_url="http://192.168.1.20:8080/?action=stream")
    caps = client.get_capabilities()
    assert caps.camera is True


def test_no_camera_capability_without_url():
    client = _make_client(camera_url="")
    caps = client.get_capabilities()
    assert caps.camera is False


def test_is_idle_when_print_state_idle():
    client = _make_client()
    client.state.print_state = "IDLE"
    assert client.is_idle is True


def test_is_printing_when_running():
    client = _make_client()
    client.state.print_state = "RUNNING"
    assert client.is_printing is True


def test_state_compat_shims():
    client = _make_client()
    client.state.print_state = "RUNNING"
    assert client.state.state == "RUNNING"
    assert client.state.raw_data is None


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


def test_stop_print_sends_cmd_130():
    client = _connected_client()
    client.stop_print()
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 130


def test_start_print_sends_cmd_128():
    client = _connected_client()
    client.start_print("model.gcode")
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 128
    assert sent["Data"]["Data"]["Filename"] == "model.gcode"


def test_home_sends_cmd_402():
    client = _connected_client()
    client.home()
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 402


def test_check_staleness_returns_connected():
    client = _make_client()
    client.state.connected = False
    assert client.check_staleness() is False
