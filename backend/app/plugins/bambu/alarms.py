"""Bambu HMS decoding: `print.hms` is a list of `{"attr": int, "code": int}`. Written as `HMS_AAAA_BBBB_CCCC_DDDD` where
attr = 0xAAAABBBB and code = 0xCCCCDDDD; module = attr >> 24; severity = code >> 16 (1 fatal, 2 serious, 3 common, 4 info).
The per-code text lives in Bambu's HMS database, which we don't bundle: drop a `{"AAAA_BBBB_CCCC_DDDD": "text"}` file at
`<data dir>/hms_messages.json` to get readable text, otherwise the alarm shows the module, severity and the code with a link
to Bambu's wiki page for it."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ...services.abstract_printer_client import Alarm

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
        from ... import config
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


