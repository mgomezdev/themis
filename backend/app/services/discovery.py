"""Printer discovery: sweep one or more IP ranges (so printers on another VLAN are reachable — multicast never
crosses routers), asking every vendor client class to recognise its own printer at each address via its documented
signature (`AbstractPrinterClient.discover_host`), plus passively merging SSDP announcements heard on this subnet."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field

from .abstract_printer_client import DiscoveredPrinter
from .discovery_net import Network

MAX_HOSTS = 4096              # a /20
DEFAULT_DEADLINE_S = 45.0
_CONCURRENCY = 128


class ScanRangeError(ValueError):
    """The requested range is malformed, too large, or not a private network."""


@dataclass
class ScanResult:
    found: list[DiscoveredPrinter] = field(default_factory=list)
    scanned: int = 0
    truncated: bool = False          # the deadline hit before every address was probed


_ALLOWED = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10")]   # RFC1918, link-local, CGNAT


def parse_range(text: str) -> ipaddress.IPv4Network:
    """`192.168.7.0/24`, `192.168.7.20` (one host) or `192.168.7.0/255.255.255.0`. Only RFC 1918, link-local and CGNAT
    space (an explicit allowlist — NOT Python's `is_private`, which also admits loopback, 0.0.0.0/8, TEST-NETs and
    reserved space): this endpoint must not be usable to probe the server itself or arbitrary hosts."""
    try:
        net = ipaddress.ip_network(text.strip(), strict=False)
    except ValueError as e:
        raise ScanRangeError(f"{text!r} is not an IP range ({e})") from e
    if net.version != 4:
        raise ScanRangeError("Only IPv4 ranges are supported")
    if not any(net.subnet_of(a) for a in _ALLOWED):
        raise ScanRangeError(f"{net} is not a private LAN network (allowed: 10/8, 172.16/12, 192.168/16, 169.254/16, 100.64/10)")
    if net.num_addresses > MAX_HOSTS + 2:
        raise ScanRangeError(f"{net} has {net.num_addresses} addresses; the limit is a /20 ({MAX_HOSTS})")
    return net  # type: ignore[return-value]


def local_ranges() -> list[str]:
    """Best-effort /24 of this host's outbound interface (no packet is sent). Inside Docker's default bridge network
    this is the container's own 172.x network, not the LAN — callers should pass the printers' range explicitly."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 9))
            ip = s.getsockname()[0]
        return [str(ipaddress.ip_network(f"{ip}/24", strict=False))]
    except OSError:
        return []


def _richer(a: DiscoveredPrinter, b: DiscoveredPrinter) -> DiscoveredPrinter:
    score = lambda d: sum(bool(x) for x in (d.serial, d.model, d.name))      # noqa: E731
    return a if score(a) >= score(b) else b


async def scan(
    net: Network, ranges: list[str], registry: dict[str, type], *, deadline_s: float = DEFAULT_DEADLINE_S,
    listen_s: float = 3.0,
) -> ScanResult:
    networks = [parse_range(r) for r in ranges]
    hosts: list[str] = []
    for n in networks:
        hosts.extend(str(h) for h in (n.hosts() if n.num_addresses > 2 else n))
    hosts = list(dict.fromkeys(hosts))
    if len(hosts) > MAX_HOSTS:
        raise ScanRangeError(f"{len(hosts)} addresses across all ranges; the limit is {MAX_HOSTS} per scan")

    classes = [c for c in registry.values() if c.discover_host.__func__ is not _base_discover()]      # type: ignore[attr-defined]
    result = ScanResult(scanned=0)
    found: dict[tuple[str, str], DiscoveredPrinter] = {}
    sem = asyncio.Semaphore(_CONCURRENCY)

    def keep(d: DiscoveredPrinter) -> None:
        key = (d.printer_type, d.ip)
        found[key] = _richer(found[key], d) if key in found else d

    async def probe(ip: str) -> None:
        async with sem:
            outs = await asyncio.gather(*(c.discover_host(net, ip) for c in classes), return_exceptions=True)
        for o in outs:
            if isinstance(o, DiscoveredPrinter):
                keep(o)
        result.scanned += 1

    async def listen() -> None:
        ports = sorted({p for c in registry.values() for p in getattr(c, "SSDP_PORTS", ())})
        if not ports:
            return
        try:
            heard = await net.ssdp_listen(tuple(ports), listen_s)
        except Exception:
            return                                  # multicast unavailable (Docker bridge, no permissions): sweep only
        for ip, dgram in heard:
            try:                                    # one hostile/garbled datagram must not drop the rest
                if not any(ipaddress.ip_address(ip) in n for n in networks):
                    continue
                for c in registry.values():
                    d = c.parse_announcement(ip, dgram)
                    if d is not None:
                        keep(d)
            except Exception:
                continue

    tasks = [asyncio.ensure_future(probe(h)) for h in hosts] + [asyncio.ensure_future(listen())]
    done, pending = await asyncio.wait(tasks, timeout=deadline_s)
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    result.truncated = result.scanned < len(hosts)
    result.found = sorted(found.values(), key=lambda d: (ipaddress.ip_address(d.ip), d.printer_type))
    return result


def _base_discover():
    from .abstract_printer_client import AbstractPrinterClient
    return AbstractPrinterClient.discover_host.__func__            # type: ignore[attr-defined]
