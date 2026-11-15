"""Preset overrides for jobs that get sliced (never pre-sliced G-code).

Three sources override the routed printer's presets, highest first:

  1. the job's input: `jobs.overrides` then its per-target config's `overrides` (New/Edit Job, project generate, groups),
  2. the uploaded project file's own changes to its preset (e.g. a 3MF author enabled supports), re-applied onto whichever
     preset the printer that claims the job slices with,
  3. the printer's own overrides (`printers.slice_overrides`, e.g. a printer that always wants a brim).

Below all three sits the printer's bed type. At slice time core sends (1) as `SliceRequest.extra_config` and (3) as
`SliceRequest.printer_overrides` (both in the slicing-cache key; (2) follows from the source's content hash), and the
provider — which reads the file it slices — merges them (`SliceSettings`). This module holds the vendor-neutral helpers:
the curated-key filter, the precedence for previews, and reading (2) for the New Job check.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Mapping

from .library_scanner import is_presliced_file
from .library_store import get_library_store, with_local_path
from .providers.slicing import get_format_provider

logger = logging.getLogger(__name__)

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


_MISSING = object()


def _read_embedded(uploaded_file):
    """{key: value}, or `_MISSING` when the file isn't in the store (never memoized)."""
    return with_local_path(get_library_store(), uploaded_file.relative_path,
                           lambda path: get_format_provider().embedded_overrides(path), _MISSING)


async def embedded_for(uploaded_file) -> dict:
    """The project file's own preset changes (see module doc); {} for G-code, a missing file or none recorded. Read in a
    worker thread (never on the queue loop); a read error is logged and yields {}. Used by the New Job check — at slice time
    the provider reads the file it slices itself."""
    if uploaded_file is None or not uploaded_file.relative_path or is_presliced_file(uploaded_file):
        return {}
    try:
        found = await asyncio.to_thread(_read_embedded, uploaded_file)
    except Exception:
        logger.warning("could not read the project file's own settings from %s", uploaded_file.relative_path, exc_info=True)
        return {}
    return {} if found is _MISSING else dict(found or {})
