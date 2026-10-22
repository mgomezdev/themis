"""Typed printer events published by the printer clients' callbacks (BIZ-251)."""
from __future__ import annotations

from typing import Any

from .events import Event


class PrintCompleted(Event):
    printer_id: int
    state: Any


class PrinterStateChanged(Event):
    printer_id: int
    state: Any


class AmsChanged(Event):
    printer_id: int
    trays: list


class AlarmsReported(Event):
    """The printer's current problems (neutral `Alarm`s, already translated from the vendor's codes by its plugin)."""
    printer_id: int
    alarms: list
