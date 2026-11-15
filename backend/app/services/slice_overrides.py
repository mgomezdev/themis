"""Preset overrides for jobs that get sliced (never pre-sliced G-code).

Three sources override the routed printer's presets, highest first:

  1. the job's own overrides (`jobs.overrides`, set on the New/Edit Job screen or a project's generate),
  2. the uploaded project file's own changes to its preset (read by the format provider — e.g. a 3MF author enabled
     supports), re-applied onto whichever preset the printer that claims the job slices with,
  3. the printer's own overrides (`printers.slice_overrides`, e.g. a printer that always wants a brim).

Below all three sits the printer's bed type. The merged dict is the slice's `extra_config`, so it is also part of the
slicing-cache key. Job and printer overrides are limited to the provider's curated keys; the project file's are not
(they are whatever its author changed). Vendor-neutral: the setting vocabulary comes from the format provider.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Mapping

from .library_scanner import is_presliced_file
from .library_store import get_library_store, with_local_path
from .providers.slicing import get_format_provider

logger = logging.getLogger(__name__)

BED_TYPE_KEY = "curr_bed_type"

_EMBEDDED_MEMO: "OrderedDict[tuple, dict]" = OrderedDict()
_EMBEDDED_MEMO_MAX = 128


def clean(overrides: Mapping | None) -> dict | None:
    """Keep only the provider's curated keys with a non-blank value (as strings); None when nothing is left."""
    if not overrides:
        return None
    allowed = frozenset(get_format_provider().curated_keys())
    cleaned = {k: str(v).strip() for k, v in overrides.items() if k in allowed and v is not None and str(v).strip()}
    return cleaned or None


def effective(printer: Mapping | None, embedded: Mapping | None, job: Mapping | None) -> dict:
    """job > project file > printer."""
    return {**(printer or {}), **(embedded or {}), **(job or {})}


def extra_config(build_plate_type: str | None, printer: Mapping | None, embedded: Mapping | None,
                 job: Mapping | None) -> dict:
    """What the slicer merges over the resolved presets: bed type < printer < project file < job."""
    base = {BED_TYPE_KEY: build_plate_type} if build_plate_type else {}
    return {**base, **effective(printer, embedded, job)}


_MISSING = object()


def _read_embedded(uploaded_file):
    """{key: value}, or `_MISSING` when the file isn't in the store (never memoized)."""
    return with_local_path(get_library_store(), uploaded_file.relative_path,
                           lambda path: get_format_provider().embedded_overrides(path), _MISSING)


async def embedded_for(uploaded_file) -> dict:
    """The project file's own preset changes (see module doc); {} for G-code, a missing file or none recorded.
    File I/O runs in a worker thread (never on the queue loop). Memoized per file version, successful reads only: a
    missing file or a read error yields {} for this slice but is retried next time (and logged)."""
    if uploaded_file is None or not uploaded_file.relative_path or is_presliced_file(uploaded_file):
        return {}
    key = (uploaded_file.relative_path, uploaded_file.content_hash or "", uploaded_file.mtime or 0.0)
    if key[1] and key in _EMBEDDED_MEMO:
        _EMBEDDED_MEMO.move_to_end(key)
        return dict(_EMBEDDED_MEMO[key])
    try:
        found = await asyncio.to_thread(_read_embedded, uploaded_file)
    except Exception:
        logger.warning("could not read the project file's own settings from %s; slicing without them",
                       uploaded_file.relative_path, exc_info=True)
        return {}
    if found is _MISSING:
        return {}
    found = dict(found or {})
    if key[1]:   # only memoize a version we can tell apart from the next one
        _EMBEDDED_MEMO[key] = dict(found)
        while len(_EMBEDDED_MEMO) > _EMBEDDED_MEMO_MAX:
            _EMBEDDED_MEMO.popitem(last=False)
    return found
