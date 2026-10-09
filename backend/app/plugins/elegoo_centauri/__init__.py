"""Elegoo Centauri printer plugin (BIZ-251). Declares the models it supports for the new-printer flow; the printer
client class is the factory only so its `connection_fields()` can describe the form. No capability is provided yet, so
the class is never instantiated here: a printer is built per row by the client factory."""
from __future__ import annotations

from pydantic import BaseModel

from ...services.elegoo_centauri_client import ElegooCentauriClient
from ..manifest import HOST_API, Manufacturer, PluginManifest, PrinterModel


class NoSettings(BaseModel):
    """Vendor connection details are per printer (connection_config), not per plugin."""


MANIFEST = PluginManifest(
    id="elegoo_centauri",
    name="Elegoo Centauri",
    version="1.0.0",
    host_api=HOST_API,
    settings_model=NoSettings,
    factory=ElegooCentauriClient,
    manufacturers=(
    Manufacturer("elegoo", "Elegoo", (PrinterModel("centauri", "Centauri Carbon", bed_mm=(256, 256), toolheads=1),)),
    ),
    description="Elegoo Centauri printers.",
)
