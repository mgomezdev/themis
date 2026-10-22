"""Elegoo SDCP `Status.PrintInfo.ErrorNumber` (SDCP v3 spec, "print error codes") -> normalised alarms."""
from __future__ import annotations

from ...services.abstract_printer_client import Alarm

# ── Elegoo SDCP ──────────────────────────────────────────────────────────────

SDCP_ERRORS = {
    1: "File MD5 check failed",
    2: "File read failed",
    3: "File resolution mismatch",
    4: "File format mismatch",
    5: "Machine model mismatch",
}


def sdcp_alarms(error_number) -> list[Alarm]:
    try:
        n = int(error_number or 0)
    except (TypeError, ValueError):
        return []
    if n == 0:
        return []
    return [Alarm(code=f"SDCP_{n}", severity="error", source="sdcp",
                  message=SDCP_ERRORS.get(n, f"Printer reported error {n}"))]
