"""Klipper/Moonraker alarms are shared by every Moonraker-protocol plugin (`services/moonraker/alarms.py`); re-exported here so
the Snapmaker plugin's historical import path keeps working."""
from ...services.moonraker.alarms import klipper_alarms  # noqa: F401

__all__ = ["klipper_alarms"]
