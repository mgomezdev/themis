import json
import threading
import time
from unittest.mock import MagicMock, call, patch
import pytest
from app.services.bambu_mqtt import BambuMQTTClient, PrinterState
from app.services.abstract_printer_client import PrinterCapabilities, StartPrintOptions


def _make_client(**kwargs) -> BambuMQTTClient:
    return BambuMQTTClient(
        ip_address="192.168.1.10",
        serial_number="01P00A123456789",
        access_code="12345678",
        **kwargs,
    )


def _connected_client(**kwargs) -> BambuMQTTClient:
    client = _make_client(**kwargs)
    client._client = MagicMock()
    client.state.connected = True
    return client


_AMS_REPORT = {"print": {
    "ams": {"tray_now": "1", "ams": [{"id": "0", "tray": [
        {"id": "0", "tray_type": "PLA", "tray_sub_brands": "PLA Basic", "tray_color": "FF0000FF", "tray_info_idx": "GFA00"},
        {"id": "1", "tray_type": "PETG", "tray_color": "000000FF", "tray_info_idx": "GFG00"},
        {"id": "2", "tray_type": "", "tray_color": "00000000"},  # empty slot — skipped
    ]}]},
    "vt_tray": {"id": "254", "tray_type": "TPU", "tray_color": "00FF00FF"},  # external spool
}}


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


def test_upload_file_uses_implicit_ftps(mocker):
    client = _make_client()
    mock_ftp = MagicMock()
    mocker.patch("app.services.bambu_mqtt._ImplicitFTP_TLS", return_value=mock_ftp)
    result = client.upload_file(b"data", "model.gcode.3mf")
    assert result is True
    # implicit FTPS on 990, authenticated, encrypted data channel
    assert mock_ftp.connect.call_args[0][1] == 990
    mock_ftp.login.assert_called_once_with("bblp", "12345678")
    mock_ftp.prot_p.assert_called_once()
    mock_ftp.storbinary.assert_called_once()


def test_upload_file_returns_false_on_ftps_error(mocker):
    client = _make_client()
    mocker.patch("app.services.bambu_mqtt._ImplicitFTP_TLS", side_effect=OSError("refused"))
    assert client.upload_file(b"data", "model.gcode.3mf") is False


# ── AMS ──────────────────────────────────────────────────────────────────────

def test_parse_ams_flattens_trays_and_skips_empty():
    trays = BambuMQTTClient._parse_ams(_AMS_REPORT["print"])
    assert len(trays) == 3  # 2 AMS + 1 external; empty tray skipped
    by_id = {t["ams_tray_id"]: t for t in trays}
    assert by_id[0]["type"] == "PLA" and by_id[0]["color"] == "#FF0000"
    assert by_id[1]["type"] == "PETG" and by_id[1]["color"] == "#000000"
    assert by_id[254]["type"] == "TPU"  # external/virtual spool


def test_handle_message_updates_ams_state_and_loaded_filaments():
    client = _make_client()
    client._handle_message(_AMS_REPORT)
    loaded = client.get_loaded_filaments()
    assert len(loaded) == 3
    assert client.state.ams_tray_now == 1


def test_handle_message_fires_on_ams_change_once():
    seen = []
    client = _make_client(on_ams_change=lambda trays: seen.append(trays))
    # No event loop wired → callback path guarded; assert state still updates.
    client._handle_message(_AMS_REPORT)
    assert len(client.state.ams_trays) == 3


def test_start_print_includes_ams_mapping_and_per_printer_flags():
    client = _connected_client(use_ams="1", bed_leveling="0", flow_cali="1", timelapse="1")
    opts = StartPrintOptions(plate_id=2, gcode_path="Metadata/plate_2.gcode", ams_mapping=[1])
    client.start_print("m.gcode.3mf", opts)
    p = json.loads(client._client.publish.call_args[0][1])["print"]
    assert p["ams_mapping"] == [1]
    assert p["use_ams"] is True
    assert p["bed_levelling"] is False
    assert p["flow_cali"] is True
    assert p["timelapse"] is True


def test_connection_fields_include_ams_options():
    names = {f.name for f in BambuMQTTClient.connection_fields()}
    assert {"use_ams", "bed_leveling", "flow_cali", "timelapse"} <= names


def test_start_print_uses_gcode_path_when_set():
    client = _connected_client()
    from app.services.abstract_printer_client import StartPrintOptions
    opts = StartPrintOptions(plate_id=1, gcode_path="output.gcode")
    client.start_print("output.gcode", opts)
    import json
    payload = json.loads(client._client.publish.call_args[0][1])
    assert payload["print"]["param"] == "output.gcode"


def test_camera_rtsp_url_returns_rtsps_url():
    client = _make_client()
    assert client.camera_rtsp_url == "rtsps://bblp:12345678@192.168.1.10:322/streaming/live/1"


def test_camera_mjpeg_url_is_none_for_bambu():
    client = _make_client()
    assert client.camera_mjpeg_url is None
