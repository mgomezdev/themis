"""A virtual LAN: addresses with open TCP ports and UDP / HTTP responders that reproduce each vendor's *documented*
discovery behaviour (Bambu SSDP + MQTT/FTPS ports, Elegoo SDCP `M99999`, Moonraker `/server/info`).
Implements `app.services.discovery_net.Network`."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field


@dataclass
class VirtualHost:
    tcp: set[int] = field(default_factory=set)
    udp: dict[int, "callable"] = field(default_factory=dict)          # port -> (payload) -> reply bytes | None
    http: dict[tuple[int, str], tuple[int, dict | None]] = field(default_factory=dict)   # (port, path) -> (status, json)


class VirtualNetwork:
    def __init__(self) -> None:
        self.hosts: dict[str, VirtualHost] = {}
        self.announcements: list[tuple[str, bytes]] = []
        self.probes: list[tuple[str, str, str]] = []                  # (kind, ip, detail) — every packet "sent"

    def host(self, ip: str) -> VirtualHost:
        return self.hosts.setdefault(ip, VirtualHost())

    # ── Network interface ────────────────────────────────────────────────────
    async def tcp_open(self, ip: str, port: int, timeout: float) -> bool:
        self.probes.append(("tcp", ip, str(port)))
        await asyncio.sleep(0)
        return port in self.hosts.get(ip, VirtualHost()).tcp

    async def udp_request(self, ip: str, port: int, payload: bytes, timeout: float) -> bytes | None:
        self.probes.append(("udp", ip, f"{port}:{payload[:7]!r}"))
        await asyncio.sleep(0)
        handler = self.hosts.get(ip, VirtualHost()).udp.get(port)
        return handler(payload) if handler else None

    async def http_get_json(self, url: str, timeout: float) -> tuple[int, dict | None]:
        self.probes.append(("http", url, ""))
        await asyncio.sleep(0)
        rest = url.split("://", 1)[1]
        hostport, _, path = rest.partition("/")
        ip, _, port = hostport.partition(":")
        return self.hosts.get(ip, VirtualHost()).http.get((int(port or 80), "/" + path), (0, None))

    async def ssdp_listen(self, ports: tuple[int, ...], timeout: float) -> list[tuple[str, bytes]]:
        self.probes.append(("listen", "239.255.255.250", ",".join(map(str, ports))))
        return list(self.announcements)

    # ── device builders ──────────────────────────────────────────────────────
    def add_bambu(self, ip: str, *, serial: str = "01P00A000000001", model: str = "C12", name: str = "3DP-01P-001",
                  answers_msearch: bool = True, mqtt: bool = True, ftps: bool = True) -> None:
        h = self.host(ip)
        if mqtt:
            h.tcp.add(8883)
        if ftps:
            h.tcp.add(990)
        if answers_msearch:
            h.udp[1990] = lambda payload: bambu_ssdp_reply(ip, serial, model, name, kind="response") \
                if b"urn:bambulab-com:device:3dprinter:1" in payload else None

    def add_elegoo(self, ip: str, *, name: str = "Centauri", machine: str = "Centauri Carbon",
                   board: str = "abcdef0123456789", reports_ip: str | None = None) -> None:
        reply = json.dumps({"Id": "uuid", "Data": {
            "Name": name, "MachineName": machine, "BrandName": "ELEGOO", "MainboardIP": reports_ip or ip,
            "MainboardID": board, "ProtocolVersion": "V3.0.0", "FirmwareVersion": "V1.1.25"}}).encode()
        self.host(ip).udp[3000] = lambda payload: reply if payload == b"M99999" else None

    def add_moonraker(self, ip: str, *, hostname: str | None = "snapmaker-u1", needs_key: bool = False) -> None:
        h = self.host(ip)
        h.tcp.add(7125)
        if needs_key:
            h.http[(7125, "/server/info")] = (401, {"error": {"code": 401, "message": "Unauthorized"}})
            return
        h.http[(7125, "/server/info")] = (200, {"result": {"klippy_state": "ready", "moonraker_version": "v0.9.3"}})
        h.http[(7125, "/printer/info")] = (200, {"result": {"hostname": hostname, "software_version": "v0.12"}})

    def add_bystander(self, ip: str) -> None:
        """A non-printer: a web server and an SSH port, which must not be mistaken for a printer."""
        h = self.host(ip)
        h.tcp |= {22, 80, 7125}
        h.http[(7125, "/server/info")] = (404, None)
        h.http[(80, "/")] = (200, {"hello": "world"})


def bambu_ssdp_reply(ip: str, serial: str, model: str, name: str, kind: str = "notify") -> bytes:
    start = "NOTIFY * HTTP/1.1\r\nHOST: 239.255.255.250:1990\r\n" if kind == "notify" else "HTTP/1.1 200 OK\r\n"
    nt = "NT" if kind == "notify" else "ST"
    return (f"{start}Server: UPnP/1.0\r\nLocation: {ip}\r\n{nt}: urn:bambulab-com:device:3dprinter:1\r\n"
            f"USN: {serial}\r\nCache-Control: max-age=1800\r\nDevModel.bambu.com: {model}\r\n"
            f"DevName.bambu.com: {name}\r\nDevConnect.bambu.com: lan\r\nDevBind.bambu.com: free\r\n"
            f"DevVersion.bambu.com: 01.07.00.00\r\n\r\n").encode()
