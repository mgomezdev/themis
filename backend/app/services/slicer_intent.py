"""Validated target overrides and queue preference shared by jobs and project imports."""
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator


class OverrideFields(BaseModel):
    overrides: dict | None = None

    @field_validator("overrides")
    @classmethod
    def valid_overrides(cls, value):
        from .providers.slicing import get_format_provider
        if not value:
            return None
        allowed = {s.key for s in get_format_provider().curated_settings()}
        if not set(value) <= allowed:
            raise ValueError("Unknown process override key")
        if any(not isinstance(v, (str, int, float, bool)) or (isinstance(v, float) and not isfinite(v)) for v in value.values()):
            raise ValueError("Process overrides must be scalar finite values")
        return {k: ("1" if v else "0") if isinstance(v, bool) else str(v) for k, v in value.items()}


class PrinterPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[StrictInt | StrictStr] = Field(min_length=1, max_length=100)
    strict: bool = False

    @field_validator("entries")
    @classmethod
    def valid_entries(cls, entries):
        if len(set(entries)) != len(entries) or any((isinstance(e,int) and e < 1) or (isinstance(e,str) and not e.strip()) for e in entries):
            raise ValueError("Preference entries must be distinct printer IDs or model UUIDs")
        return entries


def rank(preference, printer):
    entries = (preference or {}).get("entries", [])
    return next((i for i,e in enumerate(entries) if e == printer.id or e == printer.model_uuid), len(entries))


async def validate_preference(session, preference):
    from ..models import Printer, PrinterModelRecord
    from fastapi import HTTPException
    if preference:
        for entry in preference.entries:
            if await session.get(Printer if isinstance(entry,int) else PrinterModelRecord,entry) is None:
                raise HTTPException(422,"Unknown printer or model in printer_preference")
