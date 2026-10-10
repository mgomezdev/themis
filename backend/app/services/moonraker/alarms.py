"""Klipper/Moonraker: `webhooks.state` ("startup"/"ready"/"shutdown"/"error") + `state_message`, and
`print_stats.state == "error"` + `print_stats.message` -> normalised alarms. Shared by every Moonraker-protocol plugin."""
from __future__ import annotations

from ..abstract_printer_client import Alarm


def klipper_alarms(webhooks: dict | None, print_stats: dict | None) -> list[Alarm]:
    out: list[Alarm] = []
    wh = webhooks or {}
    state = wh.get("state")
    if state in ("shutdown", "error"):
        msg = (wh.get("state_message") or "").strip() or f"Klipper is in the {state} state"
        out.append(Alarm(code=f"KLIPPER_{state.upper()}", severity="fatal" if state == "shutdown" else "error",
                         message=" — ".join(l.strip() for l in msg.splitlines() if l.strip())[:300], source="klipper"))
    ps = print_stats or {}
    if ps.get("state") == "error":
        msg = (ps.get("message") or "").strip() or "The print stopped with an error"
        out.append(Alarm(code="KLIPPER_PRINT_ERROR", severity="error", message=msg[:300], source="klipper"))
    return out
