"""Network primitives the printer discovery hooks use, behind a small interface so tests can substitute a virtual
network (`tests/virtual_printers/virtual_network.py`) and the real one stays a thin wrapper over asyncio/httpx."""
from __future__ import annotations

import asyncio
import socket
import struct
from typing import Protocol

import httpx

SSDP_MULTICAST = "239.255.255.250"


class Network(Protocol):
    async def tcp_open(self, ip: str, port: int, timeout: float) -> bool: ...

    async def udp_request(self, ip: str, port: int, payload: bytes, timeout: float) -> bytes | None:
        """Send one datagram, return the first reply (None on silence)."""

    async def http_get_json(self, url: str, timeout: float) -> tuple[int, dict | None]:
        """(status, parsed JSON body or None). Status 0 = no HTTP answer at all."""

    async def ssdp_listen(self, ports: tuple[int, ...], timeout: float) -> list[tuple[str, bytes]]:
        """Passively collect multicast announcements: [(sender ip, datagram)]."""


class RealNetwork:
    async def tcp_open(self, ip: str, port: int, timeout: float) -> bool:
        try:
            _r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
        except (OSError, asyncio.TimeoutError):
            return False
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        return True

    async def udp_request(self, ip: str, port: int, payload: bytes, timeout: float) -> bytes | None:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bytes] = loop.create_future()

        class _Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                if not fut.done():
                    fut.set_result(data)

            def error_received(self, exc):
                if not fut.done():
                    fut.set_result(b"")

        try:
            transport, _ = await loop.create_datagram_endpoint(_Proto, remote_addr=(ip, port))
        except OSError:
            return None
        try:
            transport.sendto(payload)
            data = await asyncio.wait_for(fut, timeout)
            return data or None
        except asyncio.TimeoutError:
            return None
        finally:
            transport.close()

    async def http_get_json(self, url: str, timeout: float) -> tuple[int, dict | None]:
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url)
        except Exception:
            return 0, None
        try:
            body = r.json()
        except Exception:
            body = None
        return r.status_code, body if isinstance(body, dict) else None

    async def ssdp_listen(self, ports: tuple[int, ...], timeout: float) -> list[tuple[str, bytes]]:
        """Join the SSDP multicast group on each port and collect announcements for `timeout` seconds. Needs the
        host's multicast to reach the process (not Docker bridge networking) — returns [] when it can't bind."""
        loop = asyncio.get_running_loop()
        found: list[tuple[str, bytes]] = []
        transports = []

        class _Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                found.append((addr[0], data))

        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("", port))
                mreq = struct.pack("4sl", socket.inet_aton(SSDP_MULTICAST), socket.INADDR_ANY)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                sock.setblocking(False)
                transport, _ = await loop.create_datagram_endpoint(_Proto, sock=sock)
                transports.append(transport)
            except OSError:
                sock.close()
        try:
            await asyncio.sleep(timeout)
        finally:
            for t in transports:
                t.close()
        return found
