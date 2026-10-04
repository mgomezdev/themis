"""OrcaSlicer gcode summary parsing: the estimate lines OrcaSlicer writes into its gcode (and sliced
`.gcode.3mf` archives). Owned by the Laminus adapter; core reads it through `SlicingProvider.parse_estimates`."""
from __future__ import annotations

import os
import re
import zipfile


_HEADER_BYTES = 16000
_TAIL_BYTES = 64000   # OrcaSlicer writes the "filament used" / "estimated printing time" summary at the END of the file


def _gcode_summary_text(path: str, plate: int | None) -> str | None:
    """The head + tail of a gcode file (or of one plate's gcode inside a sliced .gcode.3mf archive) — where slicers
    put the estimate lines — without reading the whole (often multi-MB) file into memory."""
    if path.lower().endswith(".3mf"):
        with zipfile.ZipFile(path) as z:
            names = sorted(n for n in z.namelist() if n.endswith(".gcode"))
            if not names:
                return None
            wanted = f"Metadata/plate_{plate}.gcode"
            with z.open(wanted if wanted in names else names[0]) as fh:
                head = fh.read(_HEADER_BYTES)
                tail = b""
                while chunk := fh.read(1 << 20):   # stream (zip members can't seek cheaply); keep only the end
                    tail = (tail + chunk)[-_TAIL_BYTES:]
        return (head + b"\n" + tail).decode("utf-8", errors="replace")
    with open(path, "rb") as f:
        head = f.read(_HEADER_BYTES)
        f.seek(0, os.SEEK_END)
        size = f.tell()
        tail = b""
        if size > _HEADER_BYTES:
            f.seek(max(_HEADER_BYTES, size - _TAIL_BYTES))
            tail = f.read()
    return (head + b"\n" + tail).decode("utf-8", errors="replace")


def parse_gcode_estimates(
    path: str, plate: int | None = None,
) -> tuple[float | None, int | None, list[float] | None]:
    """Extract filament_grams (total), estimated_seconds, per-extruder grams from gcode.

    Returns (total_grams, seconds, extruder_grams_list). extruder_grams_list has one
    entry per comma-separated value in the 'filament used [g]' line. Returns None for
    each field independently if parsing fails. Both the start and the end of the file are
    searched. For a sliced archive (.gcode.3mf), `plate` picks that plate's
    `Metadata/plate_N.gcode`; otherwise the first gcode in it is read.
    """
    try:
        text = _gcode_summary_text(path, plate)
    except Exception:
        return None, None, None
    if text is None:
        return None, None, None

    grams: float | None = None
    extruder_grams: list[float] | None = None
    seconds: int | None = None
    for raw in text.splitlines():
        line = raw.lstrip("; ").strip()
        if grams is None and "filament used [g]" in line.lower():
            raw_val = line.split("=")[-1].strip()
            parts = [p.strip() for p in raw_val.split(",")]
            try:
                extruder_grams = [float(p) for p in parts if p]
                grams = sum(extruder_grams)
            except ValueError:
                extruder_grams = None
                grams = None
        if seconds is None and "estimated printing time" in line.lower():
            time_str = re.split(r"\s*\(", line.split("=")[-1].strip())[0].strip()
            total = 0
            for num, unit in re.findall(r"(\d+)([dhms])", time_str):
                if unit == "d":
                    total += int(num) * 86400
                elif unit == "h":
                    total += int(num) * 3600
                elif unit == "m":
                    total += int(num) * 60
                else:
                    total += int(num)
            if total > 0:
                seconds = total
        if grams is not None and seconds is not None:
            break
    return grams, seconds, extruder_grams
