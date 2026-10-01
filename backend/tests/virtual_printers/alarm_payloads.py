"""Virtual printer error reports: payload builders that reproduce each vendor's *documented* error reporting, fed
into the real client message parsers (Bambu MQTT `print.hms`, Elegoo SDCP `Status.PrintInfo.ErrorNumber`, Moonraker
`webhooks`/`print_stats` status updates)."""
from __future__ import annotations

import json


def bambu_report(hms: list[tuple[int, int]] | None = None, **extra) -> dict:
    """A `device/<serial>/report` message body. `hms` = [(attr, code)]; None = key absent (partial update)."""
    p: dict = {"command": "push_status", "gcode_state": "IDLE", **extra}
    if hms is not None:
        p["hms"] = [{"attr": a, "code": c} for a, c in hms]
    return {"print": p}


def elegoo_status(error_number: int = 0, **print_info) -> dict:
    return {"Topic": "sdcp/status/abcdef", "Status": {
        "CurrentStatus": [0],
        "PrintInfo": {"Status": 0, "ErrorNumber": error_number, "Filename": "x.gcode", **print_info},
    }}


def moonraker_status(webhooks: dict | None = None, print_stats: dict | None = None) -> dict:
    status: dict = {}
    if webhooks is not None:
        status["webhooks"] = webhooks
    if print_stats is not None:
        status["print_stats"] = print_stats
    return {"jsonrpc": "2.0", "method": "notify_status_update", "params": [status, 1234.5]}


def as_message(d: dict) -> str:
    return json.dumps(d)
