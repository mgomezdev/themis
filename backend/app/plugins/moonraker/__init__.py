"""Moonraker (Klipper) protocol-family printer plugin (BIZ-148). The plugin boundary is the connection protocol, not a manufacturer:
every model below is served by the same client, and support is declared per model — never inferred from "it runs Klipper".

Declared models are the ones whose Moonraker behaviour the shared client is exercised against (the virtual Moonraker in
`tests/virtual_printers/`); hardware verification is tracked in `docs/agent/printers.md`. `custom_klipper` is the explicit
generic path: the user supplies the bed size (stored on the printer row) and toolhead count (stored in `connection_config`)."""
from __future__ import annotations

from pydantic import BaseModel

from .client import MoonrakerClient
from ..manifest import HOST_API, Manufacturer, PluginManifest, PrinterModel


class NoSettings(BaseModel):
    """Connection details are per printer (connection_config), not per plugin."""


MANIFEST = PluginManifest(
    id="moonraker",
    name="Moonraker (Klipper)",
    version="1.0.0",
    host_api=HOST_API,
    settings_model=NoSettings,
    default_enabled=True,
    factory=MoonrakerClient,
    manufacturers=(
        Manufacturer("voron", "Voron Design", (
            PrinterModel("v2_4_300", "2.4 (300 mm)", bed_mm=(300, 300)),
            PrinterModel("trident_300", "Trident (300 mm)", bed_mm=(300, 300)),
        )),
        Manufacturer("sovol", "Sovol", (PrinterModel("sv08", "SV08", bed_mm=(350, 350)),)),
        Manufacturer("generic", "Generic", (
            PrinterModel("custom_klipper", "Custom Klipper printer", bed_mm=(250, 250), custom=True),
        )),
    ),
    description="Klipper printers reached through the Moonraker API: declared models plus a generic custom printer.",
)
