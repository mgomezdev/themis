"""The filament tuple (manufacturer, type, subtype, color) and the one matching rule (spec 2026-10-19-filament-matching-design.md).

A job's ask and a printer slot are both a `FilamentTuple`. For an ask each part is defined or undefined (None); undefined matches
anything and `subtype == "any"` is the only wildcard. A slot's parts are known or unknown (None). Pure functions, no vendor or provider
names: a provider/printer plugin translates its own strings into the four parts (`split_type` holds the shared suffix rules)."""
from __future__ import annotations

import re
from dataclasses import dataclass

ANY = "any"                 # the subtype wildcard (never "undefined": that is None)
STANDARD = "standard"       # plain filament
PLUS = "plus"

# Suffix text -> canonical subtype. Anything else is kept (lower-cased) as the subtype text.
_SUFFIX_ALIASES = {"+": PLUS, "plus": PLUS, "silk": "silk", "cf": "cf", "gf": "gf", "hf": "hf", "carbon fiber": "cf",
                   "carbon": "cf", "glass fiber": "gf", "glass": "gf", "high flow": "hf", "basic": STANDARD, "standard": STANDARD}
# Base materials: "PC-ABS" is a blend of two of these, so it is one type, not PC with an "abs" subtype.
_BASE_TYPES = frozenset({"pla", "petg", "pet", "abs", "asa", "pc", "pa", "tpu", "hips", "pp", "pva", "pps", "pei", "pctg"})
_SPLIT = re.compile(r"[\s_/-]+")


def clean(value) -> str | None:
    """A trimmed string, or None when blank / not text."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def norm_name(value) -> str | None:
    """Case-insensitive comparison key for manufacturer / type / subtype (None when blank)."""
    text = clean(value)
    return text.casefold() if text else None


def norm_color(value) -> str | None:
    """`#` and case ignored, otherwise exact (also the key for non-hex colour text until BIZ-311: identical text only)."""
    text = clean(value)
    return text.lstrip("#").strip().casefold() or None if text else None


def split_type(raw) -> tuple[str | None, str | None]:
    """Split a combined material string into (base type, subtype). Blank -> (None, None); plain -> (type, "standard");
    "PLA+" -> ("PLA", "plus"); "PLA-CF"/"PLA CF" -> ("PLA", "cf"); "PLA Silk" -> ("PLA", "silk"); unknown suffix kept as text;
    a blend of two base materials ("PC-ABS") stays whole as the type with subtype "standard"."""
    text = clean(raw)
    if not text:
        return None, None
    if text.endswith("+"):
        base, suffix = text[:-1].strip(), "+"
    else:
        parts = _SPLIT.split(text, maxsplit=1)
        base, suffix = parts[0], (parts[1].strip() if len(parts) > 1 else "")
    if not base:
        return text, STANDARD
    if not suffix:
        return base, STANDARD
    if base.casefold() in _BASE_TYPES and suffix.casefold() in _BASE_TYPES:
        return text, STANDARD
    return base, _SUFFIX_ALIASES.get(suffix.casefold(), suffix.casefold())


def canon_subtype(raw, default: str | None = STANDARD) -> str | None:
    """A reported variant text as a subtype ("Basic" -> standard, "Silk" -> silk, "+" -> plus; unknown text kept lower-cased).
    Blank gives `default`."""
    text = clean(raw)
    if not text:
        return default
    return _SUFFIX_ALIASES.get(text.casefold(), text.casefold())


@dataclass(frozen=True)
class FilamentTuple:
    manufacturer: str | None = None
    type: str | None = None
    subtype: str | None = None
    color: str | None = None

    @classmethod
    def of(cls, manufacturer=None, type=None, subtype=None, color=None) -> "FilamentTuple":
        return cls(clean(manufacturer), clean(type), clean(subtype), clean(color))

    def is_undefined(self) -> bool:
        """Every part undefined: the "defer" ask."""
        return not any((self.manufacturer, self.type, self.subtype, self.color))


def validate_ask(t: FilamentTuple) -> str | None:
    """Why `t` is not a valid ask (None = valid): subtype is required whenever type is defined."""
    if t.type and not t.subtype:
        return "a filament type needs a subtype (use 'any' if it does not matter, 'standard' for plain filament)"
    return None


def ask_from_legacy(filament_type, filament_color) -> FilamentTuple:
    """A pre-tuple ask (single type string + colour; blank or "any" = no constraint) as a tuple (migration rules)."""
    ftype, fcolor = clean(filament_type), clean(filament_color)
    if ftype and ftype.casefold() == ANY:
        ftype = None
    if fcolor and fcolor.casefold() == ANY:
        fcolor = None
    if ftype:
        base, _ = split_type(ftype)
        return FilamentTuple(None, base, ANY, fcolor)
    return FilamentTuple(None, None, None, fcolor)


def slot_from_legacy(slot: dict) -> FilamentTuple:
    """A stored slot's tuple; a slot without `subtype` yet is read through the suffix rules (type "PLA+" -> PLA/plus)."""
    raw_type = clean(slot.get("type"))
    subtype = clean(slot.get("subtype"))
    if raw_type and not subtype:
        raw_type, subtype = split_type(raw_type)
    return FilamentTuple(clean(slot.get("manufacturer")), raw_type, subtype, clean(slot.get("color")))


def slot_tuple(slot: dict | None) -> FilamentTuple:
    return slot_from_legacy(slot or {})


def mismatches(ask: FilamentTuple, slot: FilamentTuple) -> list[str]:
    """The parts of `ask` the slot does not satisfy (empty = it satisfies the ask). Names: manufacturer, type, subtype, color."""
    out: list[str] = []
    if ask.manufacturer and norm_name(ask.manufacturer) != norm_name(slot.manufacturer):
        out.append("manufacturer")                      # an unknown slot manufacturer fails a manufacturer ask
    if ask.type and norm_name(ask.type) != norm_name(slot.type):
        out.append("type")
    if ask.subtype and norm_name(ask.subtype) != ANY and norm_name(ask.subtype) != norm_name(slot.subtype):
        out.append("subtype")
    if ask.color and norm_color(ask.color) != norm_color(slot.color):
        out.append("color")
    return out


def satisfies(ask: FilamentTuple, slot: FilamentTuple) -> bool:
    return not mismatches(ask, slot)


def conflicts(slot: FilamentTuple, spool: FilamentTuple) -> bool:
    """A slot is in conflict with its bound spool's material when they differ in any part known on both sides."""
    for a, b, norm in ((slot.manufacturer, spool.manufacturer, norm_name), (slot.type, spool.type, norm_name),
                       (slot.subtype, spool.subtype, norm_name), (slot.color, spool.color, norm_color)):
        if a and b and norm(a) != norm(b):
            return True
    return False


def describe(t: FilamentTuple) -> str:
    """Human text for reasons: "Acme PLA silk #FF0000"; undefined parts are left out."""
    parts = [t.manufacturer, t.type, t.subtype, f"#{norm_color(t.color).upper()}" if t.color and norm_color(t.color) and re.fullmatch(r"[0-9a-fA-F]{3,8}", norm_color(t.color) or "") else t.color]
    return " ".join(p for p in parts if p) or "any filament"
