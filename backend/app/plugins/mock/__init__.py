"""Mock Printer (Testing) printer plugin (BIZ-251). Declares the models it supports for the new-printer flow; the printer
client class is the factory only so its `connection_fields()` can describe the form. No capability is provided yet, so
the class is never instantiated here: a printer is built per row by the client factory."""
from __future__ import annotations

import os

from pydantic import BaseModel

from ...services.mock_printer_client import MockPrinterClient
from ..manifest import HOST_API, Manufacturer, PluginManifest, PrinterModel


class NoSettings(BaseModel):
    """Vendor connection details are per printer (connection_config), not per plugin."""


MANIFEST = PluginManifest(
    id="mock",
    name="Mock Printer (Testing)",
    version="1.0.0",
    host_api=HOST_API,
    settings_model=NoSettings,
    # Off in production (a fleet option only when THEMIS_MOCK_PRINTERS is set: dev and the test suite).
    default_enabled=os.environ.get("THEMIS_MOCK_PRINTERS", "").lower() in {"1", "true", "yes"},
    factory=MockPrinterClient,
    manufacturers=(
    Manufacturer("mock", "Mock", (PrinterModel("mock", "Mock Printer", bed_mm=(256, 256), toolheads=1),)),
    ),
    description="Mock Printer (Testing) printers.",
)
