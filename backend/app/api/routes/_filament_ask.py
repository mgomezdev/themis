"""HTTP glue for the filament ask tuple (spec 2026-10-19-filament-matching-design.md): the four request/response fields, their
normalisation (blank / the legacy "any" = undefined = NULL; a type needs a subtype) and the column mapping.

API fields: `filament_manufacturer`, `filament_type`, `filament_subtype`, `filament_color`. Columns: `ask_*` (rows) or the same
four keys (filament_map entries, order parts)."""
from __future__ import annotations

from pydantic import BaseModel, model_validator

from ...services.filament_tuple import ANY, FilamentTuple, clean, validate_ask

FIELDS = ("filament_manufacturer", "filament_type", "filament_subtype", "filament_color")
_COLUMNS = ("ask_manufacturer", "ask_type", "ask_subtype", "ask_color")


def _undefined(value, *, legacy_any: bool) -> str | None:
    text = clean(value)
    if text is None or (legacy_any and text.casefold() == ANY):
        return None                   # "any" on manufacturer/type/color is the pre-tuple way to say "no constraint"
    return text


def normalize(manufacturer, type_, subtype, color) -> FilamentTuple:
    """The tuple as it will be stored; raises ValueError (a 422 in a request model) when a type has no subtype."""
    t = FilamentTuple(_undefined(manufacturer, legacy_any=True), _undefined(type_, legacy_any=True),
                      _undefined(subtype, legacy_any=False), _undefined(color, legacy_any=True))
    problem = validate_ask(t)
    if problem:
        raise ValueError(problem)
    return t


class AskFields(BaseModel):
    """Mix into a request model that carries a filament ask."""
    filament_manufacturer: str | None = None
    filament_type: str | None = None
    filament_subtype: str | None = None
    filament_color: str | None = None

    @model_validator(mode="after")
    def _normalize_ask(self):
        t = normalize(self.filament_manufacturer, self.filament_type, self.filament_subtype, self.filament_color)
        self.filament_manufacturer, self.filament_type, self.filament_subtype, self.filament_color = t.manufacturer, t.type, t.subtype, t.color
        return self


def ask_columns(body) -> dict:
    """`{ask_*: value}` for a row writer, from anything with the four `filament_*` attributes."""
    return {c: getattr(body, f) for c, f in zip(_COLUMNS, FIELDS)}


def tuple_columns(t: FilamentTuple) -> dict:
    return dict(zip(_COLUMNS, (t.manufacturer, t.type, t.subtype, t.color)))


def ask_out(row) -> dict:
    """The four response fields of a row that has `ask_*` columns."""
    return {f: getattr(row, c) for c, f in zip(_COLUMNS, FIELDS)}


def row_tuple(row) -> FilamentTuple:
    return FilamentTuple(row.ask_manufacturer, row.ask_type, row.ask_subtype, row.ask_color)


def entry_tuple(entry: dict) -> FilamentTuple:
    """The ask of a `filament_map` entry (dict with the four `filament_*` keys)."""
    return FilamentTuple(entry.get("filament_manufacturer"), entry.get("filament_type"), entry.get("filament_subtype"),
                         entry.get("filament_color"))


def type_labels(rows) -> list[str]:
    """Sorted distinct "PLA" / "PLA silk" labels of the rows' type asks (the job list's `materials` chips); `any` and `standard`
    subtypes add nothing to the label."""
    labels = set()
    for r in rows:
        if r.ask_type:
            extra = r.ask_subtype if r.ask_subtype and r.ask_subtype.casefold() not in (ANY, "standard") else None
            labels.add(f"{r.ask_type} {extra}" if extra else r.ask_type)
    return sorted(labels)
