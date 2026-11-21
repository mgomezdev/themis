"""Manufacturer-neutral Klipper plugin. Vendor adapters share transport, not ownership."""
from __future__ import annotations

from pydantic import BaseModel

from .client import MoonrakerClient
from ..manifest import HOST_API, Manufacturer, PluginManifest, PrinterModel
from ..job_fields import PRINTER_JOB_FIELDS


class NoSettings(BaseModel):
    """Connection details are per printer (connection_config), not per plugin."""


MANIFEST = PluginManifest(
    id="moonraker",
    name="Generic Klipper (Moonraker)",
    version="1.1.0",
    host_api=HOST_API,
    settings_model=NoSettings,
    default_enabled=True,
    job_fields=PRINTER_JOB_FIELDS,
    factory=MoonrakerClient,
    manufacturers=(
        Manufacturer("generic", "Generic", (
            PrinterModel("custom_klipper", "Custom Klipper printer", bed_mm=(250, 250), custom=True),
        )),
    ),
    description="Manufacturer-neutral Klipper printers through Moonraker. Use the manufacturer plugin for supported branded printers.",
)
