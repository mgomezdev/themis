"""Decoders that turn each vendor's raw error reports into normalised `Alarm`s.

Documented formats used:
* Bambu HMS (OpenBambuAPI "mqtt" → `print.hms`: a list of `{"attr": int, "code": int}`). Written as
  `HMS_AAAA_BBBB_CCCC_DDDD` where attr = 0xAAAABBBB and code = 0xCCCCDDDD; module = attr >> 24; severity = code >> 16
  (1 fatal, 2 serious, 3 common, 4 info). The per-code text lives in Bambu's HMS database, which we don't bundle:
  drop a `{"AAAA_BBBB_CCCC_DDDD": "text"}` file at `<data dir>/hms_messages.json` to get readable text, otherwise the
  alarm shows the module, severity and the code with a link to Bambu's wiki page for it.
* Elegoo SDCP `Status.PrintInfo.ErrorNumber` (SDCP v3 spec, "print error codes").
* Klipper/Moonraker: `webhooks.state` ("startup"/"ready"/"shutdown"/"error") + `state_message`, and
  `print_stats.state == "error"` + `print_stats.message`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .abstract_printer_client import Alarm

logger = logging.getLogger(__name__)

# ── Bambu HMS ────────────────────────────────────────────────────────────────

HMS_MODULES = {0x03: "Mainboard", 0x05: "Mainboard", 0x07: "AMS", 0x08: "Toolhead", 0x0C: "Xcam"}
_HMS_SEVERITY = {1: "fatal", 2: "error", 3: "warning", 4: "info"}
HMS_WIKI = "https://wiki.bambulab.com/en/x1/troubleshooting/hmscode/{key}"

_messages: dict[str, str] | None = None


def _load_messages(path: Path | None = None) -> dict[str, str]:
    global _messages
    if path is None and _messages is not None:
        return _messages
    if path is None:
        from .. import config
        path = config.get_data_dir() / "hms_messages.json"
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        result = {str(k).upper(): str(v) for k, v in loaded.items()} if isinstance(loaded, dict) else {}
    except FileNotFoundError:
        result = {}
    except Exception:
        logger.warning("Could not read %s; HMS alarms will show raw codes", path)
        result = {}
    if path is not None and _messages is None:
        _messages = result
    return result


def reset_hms_messages_cache() -> None:
    global _messages
    _messages = None


def hms_key(attr: int, code: int) -> str:
    """`AAAA_BBBB_CCCC_DDDD` — the form Bambu's wiki and HMS database use."""
    a, c = f"{attr & 0xFFFFFFFF:08X}", f"{code & 0xFFFFFFFF:08X}"
    return f"{a[:4]}_{a[4:]}_{c[:4]}_{c[4:]}"


def decode_hms(attr: int, code: int, messages: dict[str, str] | None = None) -> Alarm:
    key = hms_key(attr, code)
    severity = _HMS_SEVERITY.get((code >> 16) & 0xFFFF, "warning")
    module = HMS_MODULES.get((attr >> 24) & 0xFF, f"module 0x{(attr >> 24) & 0xFF:02X}")
    text = (messages if messages is not None else _load_messages()).get(key)
    message = f"{module}: {text}" if text else f"{module} reported HMS {key}"
    return Alarm(code=f"HMS_{key}", severity=severity, message=message, source="hms", help_url=HMS_WIKI.format(key=key))


def hms_alarms(entries) -> list[Alarm]:
    """`print.hms` → alarms. Entries that aren't `{attr, code}` ints are skipped (never raise on firmware oddities)."""
    out: dict[str, Alarm] = {}
    for e in entries or []:
        try:
            a = decode_hms(int(e["attr"]), int(e["code"]))
        except (KeyError, TypeError, ValueError):
            continue
        out[a.code] = a
    return list(out.values())


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


# ── Klipper / Moonraker ──────────────────────────────────────────────────────

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
