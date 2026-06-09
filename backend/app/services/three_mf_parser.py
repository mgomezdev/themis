from __future__ import annotations
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class PlateInfo:
    plate_number: int
    thumbnail_path: Optional[str]
    estimated_time: int
    filament_g: float


def parse_model_filaments(file_path: str) -> list[dict]:
    """The filaments a 3MF declares: [{index(1-based), color, type}], from
    project_settings.config (filament_colour / filament_type). [] if none/not a 3MF."""
    try:
        with zipfile.ZipFile(file_path) as zf:
            if "Metadata/project_settings.config" not in zf.namelist():
                return []
            ps = json.loads(zf.read("Metadata/project_settings.config"))
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, OSError):
        return []
    colours = ps.get("filament_colour") or []
    types = ps.get("filament_type") or []
    out = []
    for i, colour in enumerate(colours):
        out.append({"index": i + 1, "color": colour,
                    "type": types[i] if i < len(types) else ""})
    return out


def parse_three_mf(file_path: str, thumbnail_dir: Optional[str] = None) -> list[PlateInfo]:
    """Parse a 3MF ZIP and return plate metadata. Extracts thumbnails if thumbnail_dir given."""
    plates: list[PlateInfo] = []

    with zipfile.ZipFile(file_path, "r") as zf:
        names = set(zf.namelist())

        # Load timing/weight data from slice_info.config if present
        meta: dict[int, dict] = {}
        if "Metadata/slice_info.config" in names:
            try:
                data = json.loads(zf.read("Metadata/slice_info.config"))
                for p in data.get("plate", []):
                    idx = int(p.get("index", 0))
                    raw_weight = p.get("weight", [0])
                    if not isinstance(raw_weight, list):
                        raw_weight = [raw_weight]
                    meta[idx] = {
                        "estimated_time": int(p.get("prediction", 0)),
                        "filament_g": sum(float(w) for w in raw_weight),
                    }
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                pass

        # Discover plate numbers from thumbnail files
        thumb_re = re.compile(r"Metadata/plate_(\d+)\.png")
        plate_numbers = {int(m.group(1)) for name in names if (m := thumb_re.match(name))}

        # Fall back to plate numbers found in slice_info if no thumbnails
        if not plate_numbers:
            plate_numbers = set(meta.keys())

        # Non-Bambu 3MFs (e.g. PrusaSlicer) have no plate metadata — treat as single plate
        if not plate_numbers and "3D/3dmodel.model" in names:
            plate_numbers = {1}

        if not plate_numbers:
            return []

        if thumbnail_dir:
            Path(thumbnail_dir).mkdir(parents=True, exist_ok=True)

        # Global thumbnail fallback (e.g. PrusaSlicer / eufyMake — no per-plate images)
        global_thumb = next(
            (n for n in ["Metadata/thumbnail.png", "Metadata/preview.png"] if n in names),
            None,
        )

        for num in sorted(plate_numbers):
            thumb_zip_path = f"Metadata/plate_{num}.png"
            thumb_disk_path: Optional[str] = None

            if thumb_zip_path in names and thumbnail_dir:
                dest = Path(thumbnail_dir) / f"plate_{num}.png"
                dest.write_bytes(zf.read(thumb_zip_path))
                thumb_disk_path = str(dest)
            elif thumb_zip_path not in names and global_thumb and thumbnail_dir:
                # Use the global thumbnail for all plates in non-Bambu 3MFs
                dest = Path(thumbnail_dir) / f"plate_{num}.png"
                if not dest.exists():
                    dest.write_bytes(zf.read(global_thumb))
                thumb_disk_path = str(dest)
            # If no thumbnail available, leave path as None

            m_data = meta.get(num, {})
            plates.append(PlateInfo(
                plate_number=num,
                thumbnail_path=thumb_disk_path,
                estimated_time=m_data.get("estimated_time", 0),
                filament_g=m_data.get("filament_g", 0.0),
            ))

    return plates
