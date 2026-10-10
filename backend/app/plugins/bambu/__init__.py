"""Bambu Lab printer plugin (BIZ-251). Declares the models it supports for the new-printer flow; the printer
client class is the factory only so its `connection_fields()` can describe the form. No capability is provided yet, so
the class is never instantiated here: a printer is built per row by the client factory."""
from __future__ import annotations

from pydantic import BaseModel

from .client import BambuMQTTClient
from ..manifest import HOST_API, Manufacturer, PluginManifest, PrinterModel


class NoSettings(BaseModel):
    """Vendor connection details are per printer (connection_config), not per plugin."""


MANIFEST = PluginManifest(
    id="bambu",
    name="Bambu Lab",
    version="1.0.0",
    host_api=HOST_API,
    settings_model=NoSettings,
    default_enabled=True,
    factory=BambuMQTTClient,
    # Build volumes are Bambu Lab's published figures (X/Y in mm). The H2D is dual-nozzle: 350x320 is the full plate
    # (each nozzle alone reaches 325x320; both together 300x320).
    manufacturers=(
        Manufacturer("bambu", "Bambu Lab", (
            PrinterModel("x1c", "X1 Carbon", bed_mm=(256, 256), toolheads=1),
            PrinterModel("x1e", "X1E", bed_mm=(256, 256), toolheads=1),
            PrinterModel("p1p", "P1P", bed_mm=(256, 256), toolheads=1),
            PrinterModel("p1s", "P1S", bed_mm=(256, 256), toolheads=1),
            PrinterModel("p2s", "P2S", bed_mm=(256, 256), toolheads=1),
            PrinterModel("a1", "A1", bed_mm=(256, 256), toolheads=1),
            PrinterModel("a1_mini", "A1 mini", bed_mm=(180, 180), toolheads=1),
            PrinterModel("h2s", "H2S", bed_mm=(340, 320), toolheads=1),
            PrinterModel("h2d", "H2D", bed_mm=(350, 320), toolheads=2),
        )),
    ),
    description="Bambu Lab printers.",
)
