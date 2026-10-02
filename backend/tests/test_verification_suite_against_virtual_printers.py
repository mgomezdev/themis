"""The manual real-protocol checks (backend/protocol_verification) must themselves be correct, and our virtual
printers must agree with them. So run those very checks against the virtual printers: if a virtual printer drifts
from what the verification asserts about real hardware, this fails in CI long before anyone plugs in a printer.
(Elegoo's SDCP websocket has no virtual device yet, so its checks only run against real hardware.)"""
import importlib
import inspect

import pytest

from tests.virtual_printers import fake_bambu_ftps as bambu_fake
from tests.virtual_printers import fake_moonraker as moon_fake

SACRIFICIAL = "themis-verify-1.gcode"


def _run_all(module_name: str, **fixtures) -> list[str]:
    """Call every `test_*` in a verification module with the given fixture values; returns the names run."""
    mod = importlib.import_module(module_name)
    ran = []
    for name, fn in sorted(vars(mod).items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        params = inspect.signature(fn).parameters
        try:
            fn(**{p: fixtures[p] for p in params})
        except KeyError as e:
            raise AssertionError(f"{module_name}.{name} needs an unknown fixture {e}") from e
        ran.append(name)
    return ran


def test_bambu_verification_checks_pass_against_the_virtual_printer(monkeypatch):
    storage = bambu_fake.VirtualBambuStorage({
        "Benchy.gcode.3mf": b"PK\x05\x06" + b"\x00" * 18,                    # an (empty) zip: is_zipfile → True
        "cache/Cached.gcode.3mf": b"PK\x05\x06" + b"\x00" * 18,
    })
    bambu_fake.install(monkeypatch, storage)

    ran = _run_all("protocol_verification.test_bambu_files",
                   client=bambu_fake.make_client(), require_write=None, sacrificial_name=SACRIFICIAL)

    assert len(ran) == 6 and not storage.files.keys() & {SACRIFICIAL}         # the write round trip cleaned up after itself


def test_moonraker_verification_checks_pass_against_the_virtual_printer(monkeypatch):
    server = moon_fake.VirtualMoonraker({
        "part.gcode": {"data": b"G28\n", "modified": 1_759_000_000.0,
                       "meta": {"estimated_time": 3600, "filament_total": 1200.5, "filament_weight_total": 3.5,
                                "slicer": "OrcaSlicer"}},
        "sub/inner.gcode": {"data": b"G0\n", "modified": 1_759_000_200.0},
    })
    moon_fake.install(monkeypatch, server)

    ran = _run_all("protocol_verification.test_moonraker_files",
                   client=moon_fake.make_client(), require_write=None, sacrificial_name=SACRIFICIAL)

    assert len(ran) == 6 and SACRIFICIAL not in server.files


def test_discovery_verification_checks_pass_against_the_virtual_lan(monkeypatch):
    from tests.virtual_printers.virtual_network import VirtualNetwork
    lan = VirtualNetwork()
    lan.add_bambu("192.168.7.20")
    lan.add_elegoo("192.168.7.40")
    lan.add_moonraker("192.168.7.30")
    from tests.virtual_printers.virtual_network import bambu_ssdp_reply
    lan.announcements = [("192.168.7.20", bambu_ssdp_reply("192.168.7.20", "01P00A000000001", "C12", "3DP"))]
    monkeypatch.setenv("THEMIS_VERIFY_BAMBU_HOST", "192.168.7.20")
    monkeypatch.setenv("THEMIS_VERIFY_ELEGOO_HOST", "192.168.7.40")
    monkeypatch.setenv("THEMIS_VERIFY_MOONRAKER_URL", "http://192.168.7.30:7125")

    ran = _run_all("protocol_verification.test_discovery", net=lan,
                   bambu_cfg={"host": "192.168.7.20"}, elegoo_cfg={"host": "192.168.7.40"},
                   moonraker_cfg={"url": "http://192.168.7.30:7125"}, discovery_cfg={"range": "192.168.7.0/24"})

    assert len(ran) == 9


def test_alarm_verification_checks_pass_against_virtual_error_reports(monkeypatch):
    from app.services.bambu_mqtt import BambuMQTTClient
    from app.services.elegoo_centauri_client import ElegooCentauriClient
    from app.services.snapmaker_client import SnapmakerExtendedClient
    from tests.virtual_printers.alarm_payloads import bambu_report, elegoo_status, moonraker_status

    bambu = BambuMQTTClient(ip_address="192.0.2.7", serial_number="S", access_code="1")
    bambu._handle_message(bambu_report([(0x07000100, 0x00020001)]))
    seen = [bambu_report([(0x07000100, 0x00020001)])]

    elegoo = ElegooCentauriClient(ip_address="192.0.2.5")
    elegoo._parse_status_msg(elegoo_status(error_number=0))        # raw = the whole message, as a live push leaves it

    moon = SnapmakerExtendedClient(ip_address="192.0.2.6")
    status = moonraker_status({"state": "ready", "state_message": "ok"}, {"state": "standby", "message": ""})["params"][0]
    moon._apply_status(status)

    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(
        200, json={"result": {"status": status}}, request=httpx.Request("GET", url)))

    ran = _run_all("protocol_verification.test_alarms", bambu_report=(bambu, seen), elegoo_client=elegoo,
                   moonraker_cfg={"url": "http://192.0.2.6:7125", "api_key": None})

    assert len(ran) == 5


def test_the_suite_skips_cleanly_without_a_printer_configured():
    """Collected by hand (not by default), every check must skip — never error — when no printer is configured."""
    import subprocess
    import sys
    from pathlib import Path
    env = {k: v for k, v in __import__("os").environ.items() if not k.startswith("THEMIS_VERIFY_")}
    out = subprocess.run([sys.executable, "-m", "pytest", "protocol_verification", "-q", "-p", "no:cacheprovider"],
                         cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "skipped" in out.stdout and "failed" not in out.stdout and "error" not in out.stdout.lower()


def test_the_normal_gates_never_collect_the_real_protocol_suite():
    import tomllib
    from pathlib import Path
    cfg = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert cfg["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]
