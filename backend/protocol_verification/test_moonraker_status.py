"""Moonraker/Klipper status + camera assumptions behind `SnapmakerExtendedClient` (read-only GETs)."""
import httpx
import pytest

from app.plugins.snapmaker.client import SnapmakerExtendedClient


@pytest.fixture
def client(moonraker_cfg) -> SnapmakerExtendedClient:
    from urllib.parse import urlparse
    u = urlparse(moonraker_cfg["url"])
    return SnapmakerExtendedClient(ip_address=u.hostname, port=u.port or 7125, api_key=moonraker_cfg["api_key"])


def _status(client, *objects):
    r = httpx.get(f"{client._http_base}/printer/objects/query", params={o: "" for o in objects},
                  headers=client._headers(), timeout=15)
    r.raise_for_status()
    return r.json()["result"]["status"]


def test_display_status_progress_is_a_zero_to_one_fraction(client):
    """The serializer multiplies by 100; a percent here would show 100x too large."""
    p = _status(client, "display_status")["display_status"]["progress"]
    print("display_status.progress:", p)
    assert 0.0 <= p <= 1.0


def test_status_objects_the_client_reads_exist_with_the_expected_keys(client):
    s = _status(client, "print_stats", "heater_bed", "extruder", "toolhead", "webhooks")
    print({k: sorted(v) for k, v in s.items()})
    assert {"state", "filename", "print_duration"} <= set(s["print_stats"])
    assert {"temperature", "target"} <= set(s["heater_bed"]) and {"temperature", "target"} <= set(s["extruder"])
    assert isinstance(s["toolhead"]["extruder"], str)            # "extruder2" etc. maps to the active tool index
    assert {"state", "state_message"} <= set(s["webhooks"])


def test_camera_mjpeg_url_serves_an_mjpeg_stream(client):
    url = client.camera_mjpeg_url
    with httpx.stream("GET", url, timeout=10) as r:
        print(url, r.status_code, r.headers.get("content-type"))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("multipart/x-mixed-replace")


def test_print_task_config_describes_each_tool_s_filament(client):
    """Source of the U1's loaded_filaments: four parallel per-tool lists, `filament_color_rgba` as RRGGBBAA."""
    cfg = _status(client, "print_task_config")["print_task_config"]
    print({k: cfg.get(k) for k in ("filament_exist", "filament_type", "filament_vendor", "filament_color_rgba")})
    for key in ("filament_exist", "filament_type", "filament_vendor", "filament_sub_type", "filament_color_rgba"):
        assert isinstance(cfg[key], list) and len(cfg[key]) == 4, key
    assert all(len(c) == 8 for c in cfg["filament_color_rgba"])
    from app.plugins.snapmaker.client import _trays_from_task_config
    trays = _trays_from_task_config(cfg)
    assert [t["slot"] for t in trays] == [0, 1, 2, 3]
