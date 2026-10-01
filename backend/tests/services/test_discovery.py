"""Printer discovery over a virtual LAN: each vendor hook, the range sweep (private ranges only, other VLANs),
passive SSDP merge, and the /printers/discover API."""
import ipaddress

import pytest

from app.services import discovery
from app.services.abstract_printer_client import AbstractPrinterClient
from app.services.bambu_mqtt import BambuMQTTClient, parse_ssdp_headers
from app.services.elegoo_centauri_client import ElegooCentauriClient
from app.services.printer_client_factory import REGISTRY
from app.services.snapmaker_client import SnapmakerExtendedClient
from tests.virtual_printers.virtual_network import VirtualNetwork, bambu_ssdp_reply


@pytest.fixture
def lan() -> VirtualNetwork:
    n = VirtualNetwork()
    n.add_bambu("192.168.7.20", serial="01P00A111111111", model="C12", name="Bambu-P1S")
    n.add_elegoo("192.168.7.40")
    n.add_moonraker("192.168.7.30")
    n.add_bystander("192.168.7.99")
    return n


async def _scan(net, *ranges, **kw):
    return await discovery.scan(net, list(ranges), REGISTRY, listen_s=0, **kw)


# ── per-vendor hooks ─────────────────────────────────────────────────────────

async def test_bambu_needs_both_documented_ports_and_reads_ssdp_details(lan):
    d = await BambuMQTTClient.discover_host(lan, "192.168.7.20")
    assert (d.printer_type, d.ip, d.serial, d.model, d.name) == ("bambu", "192.168.7.20", "01P00A111111111", "P1S", "Bambu-P1S")
    assert d.connection_config == {"ip_address": "192.168.7.20", "serial_number": "01P00A111111111"}

    lan.add_bambu("192.168.7.21", mqtt=True, ftps=False)                      # only one port: not a Bambu
    lan.add_bambu("192.168.7.22", mqtt=False, ftps=True)
    assert await BambuMQTTClient.discover_host(lan, "192.168.7.21") is None
    assert await BambuMQTTClient.discover_host(lan, "192.168.7.22") is None


async def test_bambu_that_ignores_msearch_is_still_found_but_asks_for_the_serial(lan):
    lan.add_bambu("192.168.7.23", answers_msearch=False)
    d = await BambuMQTTClient.discover_host(lan, "192.168.7.23")
    assert d.serial is None and d.connection_config["serial_number"] == "" and "Serial" in d.note


async def test_elegoo_sdcp_reply_is_parsed_and_a_reported_address_mismatch_is_noted(lan):
    d = await ElegooCentauriClient.discover_host(lan, "192.168.7.40")
    assert (d.printer_type, d.model, d.name, d.serial) == ("elegoo_centauri", "Centauri Carbon", "Centauri", "abcdef0123456789")
    assert d.connection_config == {"ip_address": "192.168.7.40", "port": 3030} and d.note is None
    lan.add_elegoo("192.168.7.41", reports_ip="10.0.0.5")
    assert "10.0.0.5" in (await ElegooCentauriClient.discover_host(lan, "192.168.7.41")).note
    assert await ElegooCentauriClient.discover_host(lan, "192.168.7.99") is None


@pytest.mark.parametrize("junk", [b"", b"not json", b'{"Data": {}}', b'{"Data": "x"}', b"[]"])
def test_elegoo_ignores_malformed_replies(junk):
    assert ElegooCentauriClient.parse_discovery_reply("1.2.3.4", junk) is None


async def test_moonraker_is_recognised_by_its_documented_endpoint_and_a_key_requirement_is_flagged(lan):
    d = await SnapmakerExtendedClient.discover_host(lan, "192.168.7.30")
    assert (d.printer_type, d.name, d.connection_config) == ("snapmaker_extended", "snapmaker-u1", {"ip_address": "192.168.7.30", "port": 7125})
    lan.add_moonraker("192.168.7.31", needs_key=True)
    locked = await SnapmakerExtendedClient.discover_host(lan, "192.168.7.31")
    assert locked.note == "Requires an API key" and locked.connection_config["ip_address"] == "192.168.7.31"
    assert await SnapmakerExtendedClient.discover_host(lan, "192.168.7.99") is None      # 7125 open but a 404 body


def test_ssdp_header_parsing_is_case_insensitive_and_rejects_other_traffic():
    h = parse_ssdp_headers(bambu_ssdp_reply("1.2.3.4", "SERIAL", "C11", "N"))
    assert h["usn"] == "SERIAL" and h["devmodel.bambu.com"] == "C11" and h["location"] == "1.2.3.4"
    assert parse_ssdp_headers(b"GET / HTTP/1.1\r\n\r\n") == {}
    assert parse_ssdp_headers(b"\xff\xfe") == {}
    assert BambuMQTTClient.parse_announcement("1.2.3.4", b"NOTIFY * HTTP/1.1\r\nNT: urn:other:thing\r\n\r\n") is None


def test_only_vendors_that_implement_discovery_are_swept():
    overriding = {n for n, c in REGISTRY.items() if c.discover_host.__func__ is not AbstractPrinterClient.discover_host.__func__}
    assert overriding == {"bambu", "elegoo_centauri", "snapmaker_extended"}      # the mock printer has no signature


# ── sweep ────────────────────────────────────────────────────────────────────

async def test_a_sweep_of_another_vlan_finds_each_printer_once_and_skips_bystanders(lan):
    res = await _scan(lan, "192.168.7.0/24")

    assert [(d.printer_type, d.ip) for d in res.found] == [
        ("bambu", "192.168.7.20"), ("snapmaker_extended", "192.168.7.30"), ("elegoo_centauri", "192.168.7.40")]
    assert res.scanned == 254 and res.truncated is False


async def test_only_the_requested_ranges_are_ever_probed(lan):
    await _scan(lan, "192.168.7.16/30", "192.168.7.40")
    probed = {p[1].split("://")[-1].split(":")[0].split("/")[0] for p in lan.probes if p[0] != "listen"}
    assert probed <= {"192.168.7.17", "192.168.7.18", "192.168.7.40"}              # /30 hosts + the single address
    assert "192.168.7.40" in probed


async def test_passive_announcements_enrich_and_are_filtered_to_the_scanned_range(lan):
    lan.add_bambu("192.168.7.50", answers_msearch=False)                           # found by ports, no serial
    lan.announcements = [
        ("192.168.7.50", bambu_ssdp_reply("192.168.7.50", "01P00AHEARD0001", "N2S", "A1-shop")),
        ("192.168.9.9", bambu_ssdp_reply("192.168.9.9", "01P00AOUTSIDE001", "C11", "elsewhere")),   # not in range
        ("192.168.7.50", b"garbage"),
    ]
    res = await discovery.scan(lan, ["192.168.7.48/29"], REGISTRY, listen_s=0.01)

    (d,) = res.found
    assert (d.ip, d.serial, d.model) == ("192.168.7.50", "01P00AHEARD0001", "A1")      # the richer record wins


async def test_the_deadline_truncates_and_reports_it(lan):
    res = await discovery.scan(lan, ["192.168.7.0/24"], REGISTRY, listen_s=0, deadline_s=0)
    assert res.truncated is True and res.scanned < 254


# ── range validation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["8.8.8.0/24", "1.1.1.1", "192.168.0.0/16", "10.0.0.0/8", "nonsense", "::1", "2001:db8::/32"])
def test_ranges_must_be_private_ipv4_no_larger_than_a_slash_20(text):
    with pytest.raises(discovery.ScanRangeError):
        discovery.parse_range(text)


@pytest.mark.parametrize("text,count", [("192.168.7.0/24", 256), ("192.168.7.20", 1), ("10.1.0.0/20", 4096), ("169.254.1.0/24", 256), ("100.64.1.0/24", 256), ("172.16.5.0/255.255.255.0", 256)])
def test_valid_ranges(text, count):
    assert discovery.parse_range(text).num_addresses == count


def test_local_range_is_a_slash_24_or_empty():
    for r in discovery.local_ranges():
        assert ipaddress.ip_network(r).prefixlen == 24
