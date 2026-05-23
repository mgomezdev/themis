import json
import threading
import time
from unittest.mock import MagicMock, call, patch
import pytest
from app.services.bambu_mqtt import BambuMQTTClient, PrinterState
from app.services.abstract_printer_client import PrinterCapabilities, StartPrintOptions


def _make_client() -> BambuMQTTClient:
    return BambuMQTTClient(
        ip_address="192.168.1.10",
        serial_number="01P00A123456789",
        access_code="12345678",
    )


def _connected_client() -> BambuMQTTClient:
    client = _make_client()
    client._client = MagicMock()
    client.state.connected = True
    return client


def test_printer_type():
    assert BambuMQTTClient.printer_type == "bambu"


def test_connection_fields():
    fields = {f.name: f for f in BambuMQTTClient.connection_fields()}
    assert "ip_address" in fields
    assert "serial_number" in fields
    assert "access_code" in fields
    assert fields["access_code"].field_type == "password"


def test_connected_false_initially():
    client = _make_client()
    assert client.connected is False


def test_is_idle_when_state_idle():
    client = _make_client()
    client.state.state = "IDLE"
    assert client.is_idle is True


def test_is_idle_when_finish_and_stg_255():
    client = _make_client()
    client.state.state = "FINISH"
    client.state.stg_cur = 255
    assert client.is_idle is True


def test_is_idle_false_when_running():
    client = _make_client()
    client.state.state = "RUNNING"
    assert client.is_idle is False


def test_is_printing_when_running():
    client = _make_client()
    client.state.state = "RUNNING"
    assert client.is_printing is True


def test_is_printing_false_when_idle():
    client = _make_client()
    client.state.state = "IDLE"
    assert client.is_printing is False


def test_get_capabilities():
    caps = _make_client().get_capabilities()
    assert caps.pause_resume is True
    assert caps.chamber_light is True
    assert caps.ams is True
    assert caps.camera is True
    assert caps.gcode is True


def test_pause_print_publishes(mocker):
    client = _connected_client()
    result = client.pause_print()
    assert result is True
    assert client._client.publish.called
    topic, payload = client._client.publish.call_args[0]
    data = json.loads(payload)
    assert data["print"]["command"] == "pause"
    assert topic == f"device/{client._serial_number}/request"


def test_resume_print_publishes(mocker):
    client = _connected_client()
    result = client.resume_print()
    assert result is True
    payload = json.loads(client._client.publish.call_args[0][1])
    assert payload["print"]["command"] == "resume"


def test_stop_print_publishes():
    client = _connected_client()
    result = client.stop_print()
    assert result is True
    payload = json.loads(client._client.publish.call_args[0][1])
    assert payload["print"]["command"] == "stop"


def test_set_chamber_light_publishes_twice():
    client = _connected_client()
    client.set_chamber_light(True)
    assert client._client.publish.call_count == 2


def test_send_gcode_publishes():
    client = _connected_client()
    client.send_gcode("G28")
    payload = json.loads(client._client.publish.call_args[0][1])
    assert payload["print"]["command"] == "gcode_line"
    assert "G28" in payload["print"]["param"]


def test_start_print_publishes():
    client = _connected_client()
    opts = StartPrintOptions(plate_id=1)
    result = client.start_print("model.3mf", opts)
    assert result is True
    assert client._client.publish.called


def test_check_staleness_returns_connected_state():
    client = _make_client()
    client.state.connected = False
    assert client.check_staleness() is False


def test_upload_file_uses_ftp(mocker):
    client = _make_client()
    mock_ftp_instance = MagicMock()
    mocker.patch("ftplib.FTP", return_value=mock_ftp_instance)
    result = client.upload_file(b"G28\n", "output.gcode")
    assert result is True
    mock_ftp_instance.connect.assert_called_once()
    mock_ftp_instance.storbinary.assert_called_once()


def test_upload_file_returns_false_on_ftp_error(mocker):
    client = _make_client()
    mocker.patch("ftplib.FTP", side_effect=OSError("refused"))
    result = client.upload_file(b"G28\n", "output.gcode")
    assert result is False


def test_start_print_uses_gcode_path_when_set():
    client = _connected_client()
    from app.services.abstract_printer_client import StartPrintOptions
    opts = StartPrintOptions(plate_id=1, gcode_path="output.gcode")
    client.start_print("output.gcode", opts)
    import json
    payload = json.loads(client._client.publish.call_args[0][1])
    assert payload["print"]["param"] == "output.gcode"
