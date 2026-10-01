"""Network discovery: assumptions behind `discover_host` / `parse_announcement` of each client and
`tests/virtual_printers/virtual_network.py`. Needs a printer of the vendor reachable at the matching
THEMIS_VERIFY_* address (see README); the sweep test additionally needs THEMIS_VERIFY_DISCOVERY_RANGE."""
import asyncio
import json

import pytest
from urllib.parse import urlparse

from app.services import discovery
from app.services.bambu_mqtt import BAMBU_MSEARCH, SSDP_PORTS, BambuMQTTClient, bambu_from_ssdp, parse_ssdp_headers
from app.services.elegoo_centauri_client import ElegooCentauriClient
from app.services.printer_client_factory import REGISTRY
from app.services.snapmaker_client import SnapmakerExtendedClient


def run(coro):
    return asyncio.run(coro)


# ── Bambu ────────────────────────────────────────────────────────────────────

def test_bambu_lan_mode_ports_are_open(net, bambu_cfg):
    assert run(net.tcp_open(bambu_cfg["host"], 8883, 3)), "MQTT 8883 closed (is LAN mode on?)"
    assert run(net.tcp_open(bambu_cfg["host"], 990, 3)), "FTPS 990 closed"


def test_bambu_answers_a_unicast_msearch_with_the_documented_headers(net, bambu_cfg):
    reply = run(net.udp_request(bambu_cfg["host"], SSDP_PORTS[0], BAMBU_MSEARCH, 3))
    print("raw M-SEARCH reply:", reply)
    assert reply, ("no reply to a unicast M-SEARCH on UDP 1990 — discovery then only finds the printer by its open ports, "
                   "without serial/model; if it answers on another port (2021?) update SSDP_PORTS and the virtual network")
    headers = parse_ssdp_headers(reply)
    print("parsed headers:", headers)
    assert headers.get("usn") and headers.get("devmodel.bambu.com"), f"missing USN / DevModel.bambu.com in {sorted(headers)}"
    found = bambu_from_ssdp(bambu_cfg["host"], reply)
    print("decoded:", found)
    assert found and found.serial == headers["usn"]


def test_bambu_discover_host_finds_it_with_a_serial(net, bambu_cfg):
    d = run(BambuMQTTClient.discover_host(net, bambu_cfg["host"]))
    assert d is not None and d.ip == bambu_cfg["host"]
    print("discover_host:", d)
    assert d.serial, "found by ports but no serial: the M-SEARCH assumption does not hold on this firmware"


def test_bambu_multicast_announcements_are_recorded(net, bambu_cfg):
    heard = run(net.ssdp_listen(SSDP_PORTS, 8))
    print("heard", len(heard), "datagrams:", [(ip, d[:60]) for ip, d in heard])
    bambu_shaped = [(ip, d) for ip, d in heard if b"bambulab" in d.lower() or b"devmodel.bambu.com" in d.lower()]
    if not bambu_shaped:
        pytest.skip("no Bambu announcement heard in 8 s (multicast may not reach this host, e.g. Docker bridge)")
    for ip, d in bambu_shaped:
        found = BambuMQTTClient.parse_announcement(ip, d)
        assert found is not None and found.ip == ip, f"a Bambu announcement from {ip} did not parse: {d[:120]!r}"


# ── Elegoo ───────────────────────────────────────────────────────────────────

def test_elegoo_answers_m99999_with_the_sdcp_description(net, elegoo_cfg):
    reply = run(net.udp_request(elegoo_cfg["host"], 3000, b"M99999", 3))
    print("raw M99999 reply:", reply)
    assert reply, "no reply to a unicast M99999 on UDP 3000"
    data = json.loads(reply)["Data"]
    for key in ("Name", "MachineName", "MainboardID", "MainboardIP"):
        assert key in data, f"SDCP discovery reply lacks {key}: {sorted(data)}"
    found = ElegooCentauriClient.parse_discovery_reply(elegoo_cfg["host"], reply)
    print("decoded:", found)
    assert found and found.serial == data["MainboardID"]


def test_elegoo_discover_host(net, elegoo_cfg):
    d = run(ElegooCentauriClient.discover_host(net, elegoo_cfg["host"]))
    assert d is not None and d.printer_type == "elegoo_centauri"
    assert d.note is None, f"printer reports a different address than we reached it on: {d.note}"


# ── Moonraker ────────────────────────────────────────────────────────────────

def test_moonraker_server_info_is_the_discovery_signature(net, moonraker_cfg):
    status, body = run(net.http_get_json(f"{moonraker_cfg['url']}/server/info", 5))
    print("server/info:", status, body)
    assert status in (200, 401, 403)
    if status == 200:
        assert "moonraker_version" in body["result"]


def test_moonraker_discover_host(net, moonraker_cfg):
    u = urlparse(moonraker_cfg["url"])
    d = run(SnapmakerExtendedClient.discover_host(net, u.hostname))
    print("discover_host:", d)
    assert d is not None and d.connection_config["ip_address"] == u.hostname


# ── whole sweep ──────────────────────────────────────────────────────────────

def test_a_sweep_of_the_range_finds_every_configured_printer(net, discovery_cfg):
    import os
    expected = {os.environ[v] for v in ("THEMIS_VERIFY_BAMBU_HOST", "THEMIS_VERIFY_ELEGOO_HOST") if os.environ.get(v)}
    if os.environ.get("THEMIS_VERIFY_MOONRAKER_URL"):
        expected.add(urlparse(os.environ["THEMIS_VERIFY_MOONRAKER_URL"]).hostname)
    assert expected, "set at least one of THEMIS_VERIFY_BAMBU_HOST / _ELEGOO_HOST / _MOONRAKER_URL inside the range"
    res = run(discovery.scan(net, [discovery_cfg["range"]], REGISTRY))
    print("scanned", res.scanned, "truncated", res.truncated)
    for d in res.found:
        print("found:", d)
    assert not res.truncated
    assert expected <= {d.ip for d in res.found}, f"not found: {sorted(expected - {d.ip for d in res.found})}"
