"""Moonraker `file_manager` helpers shared by every Moonraker-protocol plugin (root `gcodes`)."""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone

from ..abstract_printer_client import PrinterFile


def gcodes_url_path(file_id: str) -> str:
    """`/server/files/gcodes/<quoted path>` — each segment quoted, `..` and absolute paths refused."""
    segments = [s for s in file_id.split("/") if s]
    if not segments or any(s in (".", "..") for s in segments):
        raise ValueError(f"Invalid file id: {file_id!r}")
    return "/server/files/gcodes/" + "/".join(urllib.parse.quote(s, safe="") for s in segments)


def directory_query(directory: str) -> tuple[str, str]:
    """(relative directory, Moonraker `path` param) for a gcodes-root directory; `..` refused."""
    rel = directory.strip("/")
    if any(s in (".", "..") for s in rel.split("/")):
        raise ValueError(f"Invalid directory: {directory!r}")
    return rel, "gcodes" + (f"/{rel}" if rel else "")


def _iso(ts) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def parse_directory(result: dict, rel: str) -> list[PrinterFile]:
    """A `/server/files/directory?extended=true` result as PrinterFiles: directories first (`is_dir`, id = path to pass back as
    `directory`), then files with the slicer metadata Moonraker inlines (estimated time, filament length/weight, slicer)."""
    prefix = f"{rel}/" if rel else ""
    out = [PrinterFile(id=f"{prefix}{d['dirname']}", name=d["dirname"], size=0, modified_at=_iso(d.get("modified")),
                       is_dir=True) for d in result.get("dirs", []) if d.get("dirname")]
    for f in result.get("files", []):
        if not f.get("filename"):
            continue
        meta = {k: v for k, v in {
            "estimated_seconds": f.get("estimated_time"),
            "filament_mm": f.get("filament_total"),
            "filament_grams": f.get("filament_weight_total"),
            "slicer": f.get("slicer"),
        }.items() if v is not None}
        out.append(PrinterFile(id=f"{prefix}{f['filename']}", name=f["filename"], size=int(f.get("size") or 0),
                               modified_at=_iso(f.get("modified")), metadata=meta or None))
    return out
