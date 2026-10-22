"""Mock Printer (Testing) printer plugin (BIZ-251). Declares the models it supports for the new-printer flow; the printer
client class is the factory only so its `connection_fields()` can describe the form. No capability is provided yet, so
the class is never instantiated here: a printer is built per row by the client factory."""
from __future__ import annotations

from pydantic import BaseModel

from .client import MockPrinterClient
from ..manifest import HOST_API, Manufacturer, PluginManifest, PrinterModel
from ..job_fields import PRINTER_JOB_FIELDS


class NoSettings(BaseModel):
    """Vendor connection details are per printer (connection_config), not per plugin."""


MANIFEST = PluginManifest(
    id="mock",
    name="Mock Printer (Testing)",
    version="1.0.0",
    host_api=HOST_API,
    settings_model=NoSettings,
    job_fields=PRINTER_JOB_FIELDS,
    # Off in production; dev and the test suite turn it on through core (defaults.toml maps the deprecated THEMIS_MOCK_PRINTERS).
    default_enabled=False,
    factory=MockPrinterClient,
    manufacturers=(
    Manufacturer("mock", "Mock", (PrinterModel("mock", "Mock Printer", bed_mm=(256, 256), toolheads=1),)),
    ),
    description="Mock Printer (Testing) printers.",
)
