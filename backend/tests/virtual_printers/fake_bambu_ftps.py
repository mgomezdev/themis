"""A Bambu printer's FTPS storage, at the `ftplib` API surface the client uses (implicit FTPS on :990, user
`bblp` + access code, vsFTPd-style `ls -l` LIST output). Install with `install(monkeypatch, printer)`."""
from __future__ import annotations

import ftplib
from datetime import datetime, timezone

ACCESS_CODE = "12345678"


class VirtualBambuStorage:
    def __init__(self, files: dict[str, bytes] | None = None, access_code: str = ACCESS_CODE) -> None:
        self.files: dict[str, bytes] = dict(files or {})        # "x.gcode.3mf" or "cache/x.3mf"
        self.access_code = access_code
        self.logins: list[tuple[str, str]] = []
        self.commands: list[str] = []

    @property
    def dirs(self) -> set[str]:
        return {p.split("/")[0] for p in self.files if "/" in p} | {"timelapse"}

    def list_lines(self, directory: str) -> list[str]:
        directory = directory.strip("/")
        if directory and directory not in self.dirs:
            raise ftplib.error_perm("550 Failed to change directory.")
        stamp = datetime.now(timezone.utc).strftime("%b %d %H:%M")
        lines = []
        if not directory:
            lines += [f"drwxr-xr-x    2 0        0            4096 {stamp} {d}" for d in sorted(self.dirs)]
        prefix = f"{directory}/" if directory else ""
        for path, data in sorted(self.files.items()):
            if path.startswith(prefix) and "/" not in path[len(prefix):]:
                lines.append(f"-rw-r--r--    1 0        0      {len(data):>8} Oct 01  2025 {path[len(prefix):]}")
        return lines


class _FakeFTP:
    def __init__(self, storage: VirtualBambuStorage, context=None) -> None:
        self._s = storage

    def connect(self, host, port, timeout=None):
        self._s.commands.append(f"CONNECT {host}:{port}")

    def login(self, user, passwd):
        self._s.logins.append((user, passwd))
        if (user, passwd) != ("bblp", self._s.access_code):
            raise ftplib.error_perm("530 Login incorrect.")

    def getwelcome(self):
        return "220 (vsFTPd 3.0.3)"

    def voidcmd(self, cmd):
        self._s.commands.append(cmd)
        return "200 Switching to Binary mode."

    def size(self, path):
        return len(self._s.files[path.lstrip("/")])

    def prot_p(self):
        self._s.commands.append("PROT P")

    def retrlines(self, cmd, callback):
        self._s.commands.append(cmd)
        arg = cmd[4:].strip() if cmd.startswith("LIST") else ""
        for line in self._s.list_lines(arg):
            callback(line)

    def retrbinary(self, cmd, callback):
        self._s.commands.append(cmd)
        path = cmd[5:].lstrip("/")
        if path not in self._s.files:
            raise ftplib.error_perm("550 Failed to open file.")
        callback(self._s.files[path])

    def storbinary(self, cmd, fh):
        self._s.commands.append(cmd)
        self._s.files[cmd[5:].lstrip("/")] = fh.read()

    def delete(self, path):
        self._s.commands.append(f"DELE {path}")
        if path.lstrip("/") not in self._s.files:
            raise ftplib.error_perm("550 Delete operation failed.")
        del self._s.files[path.lstrip("/")]

    def quit(self):
        self._s.commands.append("QUIT")

    def close(self):
        pass


def install(monkeypatch, storage: VirtualBambuStorage):
    from app.plugins.bambu import client as bambu_mqtt
    monkeypatch.setattr(bambu_mqtt, "_ImplicitFTP_TLS", lambda context=None: _FakeFTP(storage, context))


def make_client(access_code: str = ACCESS_CODE):
    """A real BambuMQTTClient (no MQTT connection) pointed at the virtual storage."""
    from app.plugins.bambu.client import BambuMQTTClient
    c = BambuMQTTClient.__new__(BambuMQTTClient)
    c._ip, c._access_code, c._serial_number = "192.0.2.10", access_code, "01P00A000000000"
    return c
