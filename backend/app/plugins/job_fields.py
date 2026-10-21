"""The job-detail fields every printer plugin is expected to supply (docs/printer-interface.md): checked, not enforced.

`print_start_time` / `print_end_time` (iso8601) and `result` (`success` | `failure`) are the minimum; `print_seconds` and
`filament_usage` (`{"grams": float, "breakdown": [...]|None}`) are the standard time/filament fields. A printer plugin lists each in
`PluginManifest.job_fields` or, when its hardware cannot provide it, in `unsupported_job_fields`."""
from __future__ import annotations

from .manifest import JobFieldDef

PRINTER_MIN_JOB_FIELDS: tuple[str, ...] = ("print_start_time", "print_end_time", "result")

PRINTER_JOB_FIELDS: tuple[JobFieldDef, ...] = (
    JobFieldDef("print_start_time", "When the printer began the physical print (UTC).", "iso8601"),
    JobFieldDef("print_end_time", "When the print ended (UTC).", "iso8601"),
    JobFieldDef("result", "How the print ended: `success` or `failure`.", "success|failure"),
    JobFieldDef("print_seconds", "Actual print duration.", "seconds"),
    JobFieldDef("filament_usage", "Filament used: `{grams, breakdown}`. Core derives it from the sliced job until a printer reports actuals.", "grams"),
)


def missing_min_fields(manifest) -> list[str]:
    """Minimum fields the manifest neither declares nor marks unsupported."""
    known = {f.name for f in manifest.job_fields} | set(manifest.unsupported_job_fields)
    return [f for f in PRINTER_MIN_JOB_FIELDS if f not in known]
