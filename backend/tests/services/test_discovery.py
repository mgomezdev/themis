"""Printer discovery over a virtual LAN: each vendor hook, the range sweep (private ranges only, other VLANs),
passive SSDP merge, and the /printers/discover API."""
import asyncio
import ipaddress

import pytest

from app.services import discovery
from app.services.abstract_printer_client import AbstractPrinterClient
from app.plugins.bambu.client import BambuMQTTClient, parse_ssdp_headers
from app.plugins.elegoo_centauri.client import ElegooCentauriClient
from app.plugins.mock.client import MockPrinterClient
from app.plugins.snapmaker.client import SnapmakerExtendedClient

REGISTRY = {"bambu": BambuMQTTClient, "elegoo_centauri": ElegooCentauriClient,
            "snapmaker_extended": SnapmakerExtendedClient, "mock": MockPrinterClient}   # every vendor's client class
from app.plugins.snapmaker.client import SnapmakerExtendedClient
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

@pytest.mark.parametrize("text", [
    "8.8.8.0/24", "1.1.1.1", "10.0.0.0/15", "10.0.0.0/8", "nonsense", "::1", "2001:db8::/32",
    "127.0.0.1", "127.0.0.0/24", "0.0.0.0/24", "192.0.2.0/24", "198.18.0.0/24", "240.0.0.1", "255.255.255.255", "224.0.0.1",
    "172.32.0.0/24", "192.169.0.0/24", "169.253.0.0/24",
])
def test_ranges_must_be_private_ipv4_no_larger_than_a_slash_16(text):
    with pytest.raises(discovery.ScanRangeError):
        discovery.parse_range(text)


@pytest.mark.parametrize("text,count", [("192.168.7.0/24", 256), ("192.168.7.20", 1), ("10.1.0.0/20", 4096), ("169.254.1.0/24", 256), ("100.64.1.0/24", 256), ("172.16.5.0/255.255.255.0", 256),
    ("192.168.1.5/24", 256), ("192.168.7.20/32", 1), ("192.168.7.20/31", 2), ("172.31.255.0/24", 256)])
def test_valid_ranges(text, count):
    assert discovery.parse_range(text).num_addresses == count


def test_local_range_is_a_slash_24_or_empty():
    for r in discovery.local_ranges():
        assert ipaddress.ip_network(r).prefixlen == 24


# ── hostile input & resource bounds ──────────────────────────────────────────

async def test_a_forged_location_header_cannot_redirect_the_prefill_or_break_the_scan(lan):
    lan.announcements = [
        ("192.168.7.60", bambu_ssdp_reply("evil.example", "01P00AFORGED0001", "C12", "x")),      # hostname
        ("192.168.7.61", bambu_ssdp_reply("203.0.113.9:80", "01P00AFORGED0002", "C12", "y")),   # outside, with a port
        ("192.168.7.62", bambu_ssdp_reply("::1", "01P00AFORGED0003", "C12", "z")),              # IPv6
    ]
    res = await discovery.scan(lan, ["192.168.7.48/27"], REGISTRY, listen_s=0.01)

    forged = {d.ip: d for d in res.found if d.serial and d.serial.startswith("01P00AFORGED")}
    assert set(forged) == {"192.168.7.60", "192.168.7.61", "192.168.7.62"}                  # keyed by the SENDER
    assert all(d.connection_config["ip_address"] == d.ip for d in forged.values())           # never the header's address


async def test_one_garbage_datagram_does_not_drop_the_other_announcements(lan):
    lan.announcements = [("192.168.7.60", b"\xff\xfe\x00junk"), ("192.168.7.61", bambu_ssdp_reply("x", "01P00AGOOD00001", "C12", "ok"))]
    res = await discovery.scan(lan, ["192.168.7.48/27"], REGISTRY, listen_s=0.01)
    assert [d.serial for d in res.found if d.ip == "192.168.7.61"] == ["01P00AGOOD00001"]     # the good one survived
    assert not [d for d in res.found if d.ip == "192.168.7.60"]


async def test_the_host_cap_applies_across_all_ranges_not_per_range(lan):
    with pytest.raises(discovery.ScanRangeError):
        await _scan(lan, "10.0.0.0/16", "10.1.0.0/24")
    assert lan.probes == []


@pytest.mark.parametrize('text', ['192.168.x.1', '192.x.0.x', '192.168.x', '192.168.999.x', 'x.x.x.x', '10.x.x.x'])
def test_invalid_or_oversized_wildcards_are_rejected(text):
    with pytest.raises(discovery.ScanRangeError):
        discovery.parse_range(text)


@pytest.mark.parametrize('text,expected', [('192.168.x.x', '192.168.0.0/16'),
    ('192.168.7.*', '192.168.7.0/24'), ('192.168.7.X', '192.168.7.0/24')])
def test_wildcard_ranges(text, expected):
    assert str(discovery.parse_range(text)) == expected


async def test_default_wildcard_sweeps_entire_subnet_with_bounded_workers(lan):
    baseline = len(asyncio.all_tasks())
    peak = baseline
    real = lan.http_get_json
    async def counting(*args):
        nonlocal peak
        peak = max(peak, len(asyncio.all_tasks()))
        return await real(*args)
    lan.http_get_json = counting
    result = await discovery.scan(lan, ['192.168.x.x'], {'snapmaker': SnapmakerExtendedClient}, listen_s=0)
    assert result.scanned == 65534 and result.truncated is False
    assert [p.ip for p in result.found] == ['192.168.7.30']
    assert peak - baseline <= 2 * discovery._CONCURRENCY + 2


async def test_cancelling_scan_drains_every_worker(lan):
    entered = asyncio.Event()
    in_flight = 0
    async def blocked(*args):
        nonlocal in_flight
        in_flight += 1
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            in_flight -= 1
    lan.http_get_json = blocked
    task = asyncio.create_task(discovery.scan(lan, ['192.168.x.x'], {'snapmaker': SnapmakerExtendedClient}))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert in_flight == 0


async def test_in_flight_probes_never_exceed_the_concurrency_bound(lan):
    in_flight = peak = 0
    real = lan.tcp_open

    async def counting(ip, port, timeout):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.001)
        try:
            return await real(ip, port, timeout)
        finally:
            in_flight -= 1
    lan.tcp_open = counting
    await _scan(lan, "192.168.7.0/24")
    assert 0 < peak <= 2 * discovery._CONCURRENCY          # a host runs both Bambu port probes concurrently


async def test_a_deadline_hit_mid_flight_cancels_the_rest(lan):
    started = 0

    async def slow(ip, port, timeout):
        nonlocal started
        started += 1
        await asyncio.sleep(10)
        return False
    lan.tcp_open = slow
    res = await discovery.scan(lan, ["192.168.7.0/24"], REGISTRY, listen_s=0, deadline_s=0.05)
    assert res.truncated and started > 0 and res.scanned < 254
    assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]   # nothing left running


async def test_results_are_sorted_numerically_by_address():
    n = VirtualNetwork()
    n.add_bambu("192.168.7.100")
    n.add_bambu("192.168.7.9")
    res = await _scan(n, "192.168.7.0/24")
    assert [d.ip for d in res.found] == ["192.168.7.9", "192.168.7.100"]


# ── the real network layer, over loopback ────────────────────────────────────

async def test_real_network_tcp_open_distinguishes_open_from_closed():
    from app.services.discovery_net import RealNetwork
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    net = RealNetwork()
    try:
        assert await net.tcp_open("127.0.0.1", port, 1.0) is True
        server.close()
        await server.wait_closed()
        assert await net.tcp_open("127.0.0.1", port, 0.5) is False
    finally:
        await net.aclose()


async def test_real_network_udp_request_returns_the_reply_or_none_on_silence():
    from app.services.discovery_net import RealNetwork
    loop = asyncio.get_running_loop()

    class Echo(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.t = transport

        def datagram_received(self, data, addr):
            if data == b"M99999":
                self.t.sendto(b'{"Data": {"MainboardID": "x"}}', addr)
    transport, _ = await loop.create_datagram_endpoint(Echo, local_addr=("127.0.0.1", 0))
    port = transport.get_extra_info("sockname")[1]
    net = RealNetwork()
    try:
        assert await net.udp_request("127.0.0.1", port, b"M99999", 1.0) == b'{"Data": {"MainboardID": "x"}}'
        assert await net.udp_request("127.0.0.1", port, b"other", 0.2) is None
    finally:
        transport.close()
        await net.aclose()


async def test_real_network_http_get_json_reports_status_and_body_and_reuses_one_client():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from app.services.discovery_net import RealNetwork

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            code, body = (200, b'{"result": {"moonraker_version": "v1"}}') if self.path == "/server/info" else (401, b"nope")
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    net = RealNetwork()
    try:
        assert await net.http_get_json(f"{base}/server/info", 2) == (200, {"result": {"moonraker_version": "v1"}})
        assert await net.http_get_json(f"{base}/other", 2) == (401, None)           # non-JSON body → None
        first = net._http
        await net.http_get_json(f"{base}/server/info", 2)
        assert net._http is first                                                    # one client for the whole scan
        assert await net.http_get_json("http://127.0.0.1:1/server/info", 0.5) == (0, None)
    finally:
        await net.aclose()
        srv.shutdown()
    assert net._http is None


async def test_real_http_pool_can_serve_every_scan_worker_without_dropping_queued_hosts():
    from app.services.discovery_net import RealNetwork
    ready = asyncio.Event()
    entered = 0
    handlers = set()

    async def respond(reader, writer):
        nonlocal entered
        task = asyncio.current_task()
        handlers.add(task)
        try:
            await reader.readuntil(b'\r\n\r\n')
            entered += 1
            if entered == discovery._CONCURRENCY:
                ready.set()
            # Every worker must reach the server before any response is released.
            # A smaller client pool would time out queued probes as unreachable.
            await ready.wait()
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}')
            await writer.drain()
        except OSError:
            pass
        finally:
            writer.close()
            handlers.discard(task)

    server = await asyncio.start_server(respond, '127.0.0.1', 0, backlog=256)
    port = server.sockets[0].getsockname()[1]
    net = RealNetwork()
    try:
        answers = await asyncio.gather(*(net.http_get_json(f'http://127.0.0.1:{port}/server/info', 5)
                                        for _ in range(discovery._CONCURRENCY)))
        assert answers == [(200, {})] * discovery._CONCURRENCY
    finally:
        ready.set()
        await net.aclose()
        server.close()
        await server.wait_closed()
        await asyncio.gather(*list(handlers), return_exceptions=True)


async def test_real_network_http_probes_are_not_starved_by_a_connection_pool():
    """A /24 sweep has hundreds of probes in flight, most to dead addresses that hold a connection until they time out.
    A live printer's probe must not queue behind them for a pooled connection and time out (httpx's default pool is
    100) — a Snapmaker at a real address was missed this way; the scan's own semaphore bounds concurrency."""
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from app.services.discovery_net import RealNetwork

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/dead":
                time.sleep(1.5)                              # holds its connection like an address that never answers
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    class Srv(ThreadingHTTPServer):
        request_queue_size = 512
        daemon_threads = True
    srv = Srv(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    net = RealNetwork()
    try:
        base = f"http://127.0.0.1:{srv.server_port}"
        dead = [asyncio.ensure_future(net.http_get_json(f"{base}/dead", 3.0)) for _ in range(110)]   # more than 100
        await asyncio.sleep(0.3)                             # the sweep's dead probes are in flight first
        live = await net.http_get_json(f"{base}/server/info", 1.0)
        assert live == (200, {"ok": True})
        await asyncio.gather(*dead)
    finally:
        await net.aclose()
        srv.shutdown()
        srv.server_close()


async def test_real_network_listen_never_raises_without_multicast():
    from app.services.discovery_net import RealNetwork
    net = RealNetwork()
    assert isinstance(await net.ssdp_listen((1990, 2021), 0.05), list)
