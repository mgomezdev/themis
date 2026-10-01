"""Moonraker file_manager: assumptions behind `SnapmakerExtendedClient.list_files/download_file/delete_file` and
`tests/virtual_printers/fake_moonraker.py` (https://moonraker.readthedocs.io/en/latest/web_api/#file-operations)."""
import httpx
import pytest

from app.services.snapmaker_client import SnapmakerExtendedClient


@pytest.fixture
def client(moonraker_cfg) -> SnapmakerExtendedClient:
    from urllib.parse import urlparse
    u = urlparse(moonraker_cfg["url"])
    return SnapmakerExtendedClient(ip_address=u.hostname, port=u.port or 7125, api_key=moonraker_cfg["api_key"])


def _get(client, endpoint, **params):
    r = httpx.get(f"{client._http_base}{endpoint}", params=params, headers=client._headers(), timeout=20)
    r.raise_for_status()
    return r.json()


def test_server_is_moonraker(client):
    info = _get(client, "/server/info")["result"]
    print("server/info:", info)
    assert "klippy_state" in info and "moonraker_version" in info


def test_directory_listing_shape_and_units(client):
    result = _get(client, "/server/files/directory", path="gcodes", extended="true")["result"]
    print("dirs:", [d.get("dirname") for d in result["dirs"]])
    print("files:", [(f.get("filename"), f.get("size")) for f in result["files"]][:10])
    for d in result["dirs"]:
        assert isinstance(d["dirname"], str)
    for f in result["files"]:
        assert isinstance(f["filename"], str) and isinstance(f["size"], int) and isinstance(f["modified"], (int, float))
        assert f["modified"] > 1_000_000_000, f"modified should be a UTC epoch in seconds, got {f['modified']!r}"
        for key, kinds in (("estimated_time", (int, float)), ("filament_total", (int, float)),
                           ("filament_weight_total", (int, float)), ("slicer", str)):
            if key in f:                                  # metadata is only present for files Moonraker has scanned
                assert isinstance(f[key], kinds), f"{f['filename']}: {key}={f[key]!r}"
    # unit sanity the client relies on: estimated_time is seconds, filament_total is millimetres
    timed = [f for f in result["files"] if f.get("estimated_time")]
    if timed:
        print("sample estimated_time (seconds?):", timed[0]["filename"], timed[0]["estimated_time"])


def test_client_listing_agrees_with_the_raw_api(client):
    raw = _get(client, "/server/files/directory", path="gcodes", extended="true")["result"]
    listed = client.list_files("/")
    assert {f.id for f in listed if not f.is_dir} == {f["filename"] for f in raw["files"]}
    assert {f.id for f in listed if f.is_dir} == {d["dirname"] for d in raw["dirs"]}


def test_download_matches_the_listed_size(client):
    files = sorted((f for f in client.list_files("/") if not f.is_dir), key=lambda f: f.size)
    if not files:
        pytest.skip("no gcode files in the gcodes root")
    f = files[0]
    data = client.download_file(f.id)
    assert data is not None and len(data) == f.size


def test_missing_file_download_and_delete_fail_cleanly(client):
    assert client.download_file("themis-verify-does-not-exist.gcode") is None
    assert client.delete_file("themis-verify-does-not-exist.gcode") is False


def test_write_round_trip_upload_list_delete(client, require_write, sacrificial_name):
    from protocol_verification.conftest import SACRIFICIAL_BODY
    assert client.upload_file(SACRIFICIAL_BODY, sacrificial_name)
    try:
        listed = {f.id: f for f in client.list_files("/")}
        assert sacrificial_name in listed and listed[sacrificial_name].size == len(SACRIFICIAL_BODY)
        assert client.download_file(sacrificial_name) == SACRIFICIAL_BODY
    finally:
        assert client.delete_file(sacrificial_name)
    assert sacrificial_name not in {f.id for f in client.list_files("/")}
