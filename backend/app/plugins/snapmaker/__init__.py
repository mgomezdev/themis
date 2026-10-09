"""Snapmaker U1 (Extended) printer plugin (BIZ-251). Declares the models it supports for the new-printer flow; the printer
client class is the factory only so its `connection_fields()` can describe the form. No capability is provided yet, so
the class is never instantiated here: a printer is built per row by the client factory."""
from __future__ import annotations

from pydantic import BaseModel

from ...services.snapmaker_client import SnapmakerExtendedClient
from ..manifest import HOST_API, Manufacturer, PluginManifest, PrinterModel


class NoSettings(BaseModel):
    """Vendor connection details are per printer (connection_config), not per plugin."""


MANIFEST = PluginManifest(
    id="snapmaker",
    name="Snapmaker U1 (Extended)",
    version="1.0.0",
    host_api=HOST_API,
    settings_model=NoSettings,
    default_enabled=True,
    factory=SnapmakerExtendedClient,
    manufacturers=(
    Manufacturer("snapmaker", "Snapmaker", (PrinterModel("u1_extended", "U1 (Extended)", bed_mm=(270, 270), toolheads=4),)),
    ),
    description="Snapmaker U1 (Extended) printers.",
)
