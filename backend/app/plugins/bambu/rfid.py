"""What the AMS read off a spool's RFID tag, decoded into the slot tuple's manufacturer (BIZ-320).

The plugin owns this: core only sees the tuple `get_loaded_filaments` publishes. A tag is present when `tray_uuid` / `tag_uid` is a
non-zero id; the maker is read from the tag's material code (`tray_info_idx`) against `TAG_VENDORS`. A tag whose maker is not in the
table, or no tag at all, leaves the manufacturer unknown (None) — never a guessed vendor. Add a row to `TAG_VENDORS` to teach the
plugin another RFID spool maker."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


def has_tag(tray: dict) -> bool:
    """The AMS read a tag off this spool: `tray_uuid` / `tag_uid` is non-empty and not all zeros (an untagged spool reports zeros)."""
    return any(str(tray.get(k) or "").strip("0 ") for k in ("tray_uuid", "tag_uid"))


def _bambu_own(code: str) -> bool:
    """`GFA00` (PLA Basic), `GFB00` (ABS), `GFG00` (PETG), ... are Bambu Lab's tagged catalogue; the generic presets (`GFL99`,
    `GFG99`, `GFB98`, ...) are what a hand-set spool gets, never a tagged one."""
    return bool(re.fullmatch(r"GF[A-Z]\d{2}", code)) and code[2] != "L" and code[3:] not in ("98", "99")


@dataclass(frozen=True)
class TagVendor:
    manufacturer: str
    owns: Callable[[str], bool]            # the tag's material code -> is it this maker's


TAG_VENDORS: tuple[TagVendor, ...] = (
    TagVendor("Bambu Lab", _bambu_own),
)


def manufacturer(tray: dict) -> str | None:
    """The maker of the tagged spool in `tray`, or None (untagged, or a tag from a maker this plugin does not know)."""
    if not has_tag(tray):
        return None
    code = str(tray.get("tray_info_idx") or "").strip().upper()
    for vendor in TAG_VENDORS:
        if code and vendor.owns(code):
            return vendor.manufacturer
    return None
