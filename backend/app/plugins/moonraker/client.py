"""Generic Moonraker/Klipper printer client (BIZ-148): the shared transport (`services/moonraker/client.py`) with the user's
declared toolhead count. Model-specific quirks belong in a model declaration or a focused subclass, never in core."""
from __future__ import annotations

from typing import ClassVar

from ...services.abstract_printer_client import ConnectionField, DiscoveredPrinter
from ...services.moonraker.client import MoonrakerClientBase


class MoonrakerClient(MoonrakerClientBase):
    printer_type: ClassVar[str] = "moonraker"
    label = "Moonraker"

    @classmethod
    async def discover_host(cls, net, ip: str) -> DiscoveredPrinter | None:
        return await cls._discover_moonraker(net, ip, "Moonraker / Klipper")

    @classmethod
    def connection_fields(cls) -> list[ConnectionField]:
        return cls.connection_fields_for(None)

    @classmethod
    def connection_fields_for(cls, model) -> list[ConnectionField]:
        """The form for one declared model: the Moonraker connection plus how many toolheads the machine has (a declared model
        pre-fills its own count; the custom model starts at one and the user states theirs). Stored in `connection_config`, so it is
        user-owned configuration that survives upgrades."""
        toolheads = int(getattr(model, "toolheads", 1) or 1)
        return [*cls.base_connection_fields(),
                ConnectionField(name="toolheads", label="Toolheads / extruders", field_type="number", default=toolheads, required=False,
                                help_text="How many extruders Klipper drives (extruder, extruder1…). Telemetry is read for these.")]
