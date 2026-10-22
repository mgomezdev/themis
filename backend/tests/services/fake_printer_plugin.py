"""A fake printer vendor plugin (client class + manifest) shared by the vendor-extraction tests."""
from __future__ import annotations

from app import plugins
from app.services.abstract_printer_client import (
    AbstractPrinterClient, ConnectionField, DiscoveredPrinter,
)
from tests.plugins.dummy_plugin import make_manifest

FAKE_ID = "fake_vendor"


class FakeClient(AbstractPrinterClient):
    printer_type = FAKE_ID
    instances: list["FakeClient"] = []
    discover_target = "192.168.7.20"

    def __init__(self, ip_address, use_tls=False, on_state=None):
        self.ip_address, self.use_tls, self.on_state = ip_address, use_tls, on_state
        self._connected = False
        FakeClient.instances.append(self)

    @classmethod
    def connection_fields(cls):
        return [ConnectionField("ip_address", "IP", "text"),
                ConnectionField("use_tls", "TLS", "text", required=False)]

    @classmethod
    async def discover_host(cls, net, ip):
        if ip == cls.discover_target:
            return DiscoveredPrinter(printer_type=cls.printer_type, ip=ip, name="Fake",
                                     connection_config={"ip_address": ip})
        return None

    @property
    def connected(self):
        return self._connected

    def connect(self, loop=None): self._connected = True
    def disconnect(self, timeout=0): self._connected = False
    def start_print(self, file_name, options=None): return True
    def stop_print(self): return True
    def pause_print(self): return True
    def resume_print(self): return True
    def send_gcode(self, gcode): return True
    def request_status_update(self): return None


def register_fake(plugin_id: str = FAKE_ID, factory=FakeClient):
    """Register a printer plugin whose manifest factory is the client class itself."""
    FakeClient.instances.clear()
    manifest = make_manifest(plugin_id, factory=factory)
    plugins.register_plugin(manifest)
    return manifest
