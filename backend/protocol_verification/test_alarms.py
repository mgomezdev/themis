"""Printer error reporting: assumptions behind the plugins' alarm decoders (`app/plugins/*/alarms.py`) and the clients' `get_alarms()`.

These are mostly *observational*: a healthy printer has no errors, so the checks assert the SHAPE of whatever the
printer reports (and print it with -s) rather than forcing a fault. To exercise the decoders for real, trigger a
harmless fault first (e.g. pull the filament out mid-idle on an AMS, or open the door on an enclosed printer) and
re-run — the report below then includes it. Read-only."""
import json
import time

import pytest

from app.plugins.bambu import alarms as alarm_codes   # Bambu HMS decoding (HMS_MODULES, hms_alarms, ...)
from app.services.abstract_printer_client import SEVERITIES


# ── Bambu: print.hms over MQTT ───────────────────────────────────────────────

@pytest.fixture
def bambu_report(bambu_cfg):
    """The first full status report the printer sends after a `pushall` (needs THEMIS_VERIFY_BAMBU_SERIAL too)."""
    import os
    serial = os.environ.get("THEMIS_VERIFY_BAMBU_SERIAL")
    if not serial:
        pytest.skip("set THEMIS_VERIFY_BAMBU_SERIAL (the printer's serial) for the MQTT checks")
    from app.plugins.bambu.client import BambuMQTTClient
    seen: list[dict] = []
    c = BambuMQTTClient(ip_address=bambu_cfg["host"], serial_number=serial, access_code=bambu_cfg["access_code"])
    original = c._handle_message
    c._handle_message = lambda data: (seen.append(data), original(data))[1]        # type: ignore[method-assign]
    c.connect()
    deadline = time.time() + 20
    while time.time() < deadline and not any("print" in m and "hms" in m["print"] for m in seen):
        time.sleep(0.5)
        if c.connected:
            c.request_status_update()
    yield c, seen
    c.disconnect()


def test_bambu_reports_hms_as_a_list_of_attr_code_ints(bambu_report):
    c, seen = bambu_report
    reports = [m["print"]["hms"] for m in seen if "print" in m and "hms" in m["print"]]
    assert reports, "no `print.hms` key in any report within 20 s — the parser assumes the key carries the full list"
    print("raw hms:", reports[-1])
    for e in reports[-1]:
        assert isinstance(e.get("attr"), int) and isinstance(e.get("code"), int), f"unexpected HMS entry shape: {e}"


def test_bambu_hms_entries_decode_to_known_modules_and_severities(bambu_report):
    c, _ = bambu_report
    alarms = c.get_alarms()
    for a in alarms:
        print("decoded:", a)
        assert a.severity in SEVERITIES and a.code.startswith("HMS_") and a.help_url
        assert "module 0x" not in a.message, f"unknown module in {a.code}: add it to app/plugins/bambu/alarms.py HMS_MODULES"
    if not alarms:
        print("printer currently reports no HMS errors (trigger a harmless fault to exercise the decoder)")


# ── Elegoo: Status.PrintInfo.ErrorNumber over SDCP ───────────────────────────

@pytest.fixture
def elegoo_client(elegoo_cfg):
    """A connected client that has received at least one status push."""
    from app.plugins.elegoo_centauri.client import ElegooCentauriClient
    c = ElegooCentauriClient(ip_address=elegoo_cfg["host"], port=elegoo_cfg["port"])
    c.connect()
    deadline = time.time() + 15
    while time.time() < deadline and not c.state.raw:
        time.sleep(0.25)
    yield c
    c.disconnect()


def test_elegoo_status_carries_an_integer_error_number(elegoo_client):
    c = elegoo_client
    info = (c.state.raw.get("Status") or c.state.raw).get("PrintInfo", {})
    print("PrintInfo:", json.dumps(info)[:400])
    # A healthy Centauri Carbon (V1.4.49) omits ErrorNumber entirely (hardware-verified); the client treats absent
    # as 0. Only a real fault shows the key, so when present it must be an int.
    if "ErrorNumber" in info:
        assert isinstance(info["ErrorNumber"], int)
    else:
        assert c.get_alarms() == [], "no ErrorNumber must mean no alarms"
    print("alarms:", c.get_alarms())


# ── Moonraker: webhooks + print_stats ────────────────────────────────────────

def test_moonraker_exposes_webhooks_state_and_print_stats_message(moonraker_cfg):
    import httpx
    headers = {"X-Api-Key": moonraker_cfg["api_key"]} if moonraker_cfg["api_key"] else {}
    r = httpx.get(f"{moonraker_cfg['url']}/printer/objects/query", params={"webhooks": "", "print_stats": ""},
                  headers=headers, timeout=10)
    r.raise_for_status()
    status = r.json()["result"]["status"]
    print("webhooks:", status.get("webhooks"), "| print_stats:", {k: status.get("print_stats", {}).get(k) for k in ("state", "message")})
    assert status["webhooks"]["state"] in ("startup", "ready", "shutdown", "error")
    assert "state_message" in status["webhooks"]
    assert status["print_stats"]["state"] in ("standby", "printing", "paused", "complete", "cancelled", "error")
    assert "message" in status["print_stats"]


def test_moonraker_client_alarms_match_the_raw_state(moonraker_cfg):
    from urllib.parse import urlparse
    import httpx
    from app.plugins.snapmaker.client import SnapmakerExtendedClient
    u = urlparse(moonraker_cfg["url"])
    c = SnapmakerExtendedClient(ip_address=u.hostname, port=u.port or 7125, api_key=moonraker_cfg["api_key"])
    headers = {"X-Api-Key": moonraker_cfg["api_key"]} if moonraker_cfg["api_key"] else {}
    status = httpx.get(f"{moonraker_cfg['url']}/printer/objects/query", params={"webhooks": "", "print_stats": ""},
                       headers=headers, timeout=10).json()["result"]["status"]
    c._apply_status(status)
    expect_fault = status["webhooks"]["state"] in ("shutdown", "error") or status["print_stats"]["state"] == "error"
    print("client alarms:", c.get_alarms())
    assert bool(c.get_alarms()) is expect_fault
