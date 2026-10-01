"""Vendor error decoding → normalised alarms, through the real client message parsers."""
import json

import pytest

from app.services import alarm_codes
from app.services.alarm_codes import decode_hms, hms_alarms, hms_key, klipper_alarms, sdcp_alarms
from tests.virtual_printers.alarm_payloads import bambu_report, elegoo_status, moonraker_status


# ── Bambu HMS ────────────────────────────────────────────────────────────────

def test_hms_key_and_decode_follow_the_documented_layout():
    attr, code = 0x07000100, 0x00020001                      # module 0x07 (AMS), severity 2 (serious)
    assert hms_key(attr, code) == "0700_0100_0002_0001"
    a = decode_hms(attr, code, messages={})
    assert (a.code, a.severity, a.source) == ("HMS_0700_0100_0002_0001", "error", "hms")
    assert a.message == "AMS reported HMS 0700_0100_0002_0001"
    assert a.help_url.endswith("/0700_0100_0002_0001")


@pytest.mark.parametrize("sev_nibble,expected", [(1, "fatal"), (2, "error"), (3, "warning"), (4, "info"), (9, "warning")])
def test_hms_severity_mapping(sev_nibble, expected):
    assert decode_hms(0x0C000000, sev_nibble << 16, messages={}).severity == expected


@pytest.mark.parametrize("module,name", [(0x05, "Mainboard"), (0x07, "AMS"), (0x08, "Toolhead"), (0x0C, "Xcam"), (0x63, "module 0x63")])
def test_hms_modules(module, name):
    assert decode_hms(module << 24, 0x00030001, messages={}).message.startswith(name)


def test_hms_text_comes_from_the_optional_messages_file(tmp_path, monkeypatch):
    (tmp_path / "hms_messages.json").write_text(json.dumps({"0700_0100_0002_0001": "AMS filament has run out"}))
    monkeypatch.setattr("app.config.get_data_dir", lambda: tmp_path)
    alarm_codes.reset_hms_messages_cache()
    try:
        assert decode_hms(0x07000100, 0x00020001).message == "AMS: AMS filament has run out"
        assert decode_hms(0x07000100, 0x00020002).message.startswith("AMS reported HMS")       # unknown code → raw
    finally:
        alarm_codes.reset_hms_messages_cache()


def test_a_missing_or_corrupt_messages_file_degrades_to_raw_codes(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.get_data_dir", lambda: tmp_path)
    alarm_codes.reset_hms_messages_cache()
    assert "reported HMS" in decode_hms(0x05000000, 0x00030001).message
    (tmp_path / "hms_messages.json").write_text("{not json")
    alarm_codes.reset_hms_messages_cache()
    assert "reported HMS" in decode_hms(0x05000000, 0x00030001).message
    alarm_codes.reset_hms_messages_cache()


def test_hms_alarms_skips_malformed_entries_and_dedupes():
    entries = [{"attr": 0x07000100, "code": 0x00020001}, {"attr": 0x07000100, "code": 0x00020001},
               {"attr": "x"}, {"code": 1}, None, "junk", {"attr": None, "code": 1}]
    assert [a.code for a in hms_alarms(entries)] == ["HMS_0700_0100_0002_0001"]
    assert hms_alarms(None) == [] and hms_alarms([]) == []


def test_bambu_client_keeps_the_last_full_hms_list_and_ignores_updates_without_the_key():
    from app.services.bambu_mqtt import BambuMQTTClient
    c = BambuMQTTClient(ip_address="192.0.2.7", serial_number="01P00A000000001", access_code="12345678")
    c._handle_message(bambu_report([(0x07000100, 0x00020001), (0x0C000000, 0x00030002)]))
    assert sorted(a.code for a in c.get_alarms()) == ["HMS_0700_0100_0002_0001", "HMS_0C00_0000_0003_0002"]

    c._handle_message(bambu_report(None, mc_percent=50))                  # partial update without `hms`: unchanged
    assert len(c.get_alarms()) == 2

    c._handle_message(bambu_report([(0x07000100, 0x00020001)]))           # a shorter list replaces it
    assert [a.code for a in c.get_alarms()] == ["HMS_0700_0100_0002_0001"]
    c._handle_message(bambu_report([]))                                   # empty list = everything cleared
    assert c.get_alarms() == []


# ── Elegoo SDCP ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n,msg", [(1, "File MD5 check failed"), (2, "File read failed"), (3, "File resolution mismatch"),
                                   (4, "File format mismatch"), (5, "Machine model mismatch"), (77, "Printer reported error 77")])
def test_sdcp_error_numbers(n, msg):
    (a,) = sdcp_alarms(n)
    assert (a.code, a.severity, a.message, a.source) == (f"SDCP_{n}", "error", msg, "sdcp")


@pytest.mark.parametrize("none", [0, None, "", "0", "garbage", []])
def test_sdcp_zero_and_junk_are_no_alarm(none):
    assert sdcp_alarms(none) == []


def _elegoo():
    from app.services.elegoo_centauri_client import ElegooCentauriClient
    c = ElegooCentauriClient(ip_address="192.0.2.5")
    c._loop = None
    return c


def test_elegoo_client_reads_the_error_number_from_every_status_push():
    c = _elegoo()
    c._parse_status_msg(elegoo_status(error_number=3))
    assert [a.code for a in c.get_alarms()] == ["SDCP_3"]
    c._parse_status_msg(elegoo_status(error_number=0))
    assert c.get_alarms() == []
    c._parse_status_msg({"Status": {"CurrentStatus": [0], "PrintInfo": {"Status": 0, "ErrorNumber": "oops"}}})
    assert c.get_alarms() == []


# ── Klipper / Moonraker ──────────────────────────────────────────────────────

def test_klipper_shutdown_and_error_states_and_print_errors():
    (a,) = klipper_alarms({"state": "shutdown", "state_message": "Heater extruder not heating at expected rate\nSee docs"}, None)
    assert (a.code, a.severity, a.source) == ("KLIPPER_SHUTDOWN", "fatal", "klipper")
    assert a.message == "Heater extruder not heating at expected rate"                  # first line only
    (b,) = klipper_alarms({"state": "error", "state_message": ""}, None)
    assert (b.code, b.severity, b.message) == ("KLIPPER_ERROR", "error", "Klipper is in the error state")
    (c,) = klipper_alarms({"state": "ready"}, {"state": "error", "message": "Filament runout"})
    assert (c.code, c.message) == ("KLIPPER_PRINT_ERROR", "Filament runout")
    assert klipper_alarms({"state": "ready"}, {"state": "printing"}) == [] and klipper_alarms(None, None) == []


def _moon():
    from app.services.snapmaker_client import SnapmakerExtendedClient
    return SnapmakerExtendedClient(ip_address="192.0.2.6")


def test_snapmaker_client_tracks_webhooks_and_print_stats_updates():
    c = _moon()
    c._apply_status(moonraker_status({"state": "ready", "state_message": "Printer is ready"})["params"][0])
    assert c.get_alarms() == []
    c._apply_status(moonraker_status({"state": "shutdown", "state_message": "MCU 'mcu' shutdown: Timer too close"})["params"][0])
    assert [(a.code, a.severity) for a in c.get_alarms()] == [("KLIPPER_SHUTDOWN", "fatal")]
    c._apply_status(moonraker_status({"state": "ready", "state_message": "Printer is ready"},
                                     {"state": "error", "message": "Filament runout"})["params"][0])
    assert [a.code for a in c.get_alarms()] == ["KLIPPER_PRINT_ERROR"]


def test_snapmaker_shutdown_notification_raises_the_alarm_before_the_reason_arrives_and_ready_clears_it():
    c = _moon()
    c._on_ws_message(None, json.dumps({"jsonrpc": "2.0", "method": "notify_klippy_shutdown"}))
    assert [a.code for a in c.get_alarms()] == ["KLIPPER_SHUTDOWN"]
    assert c.get_alarms()[0].message == "Klipper is in the shutdown state"          # reason not known yet
    c._on_ws_message(None, json.dumps({"jsonrpc": "2.0", "method": "notify_klippy_ready"}))
    assert c.get_alarms() == []


def test_default_clients_and_the_mock_report_no_alarms():
    from app.services.mock_printer_client import MockPrinterClient
    assert MockPrinterClient.__new__(MockPrinterClient).get_alarms() == []
