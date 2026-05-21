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

        if not plate_numbers:
            return []

        if thumbnail_dir:
            Path(thumbnail_dir).mkdir(parents=True, exist_ok=True)

        for num in sorted(plate_numbers):
            thumb_zip_path = f"Metadata/plate_{num}.png"
            thumb_disk_path: Optional[str] = None

            if thumb_zip_path in names and thumbnail_dir:
                dest = Path(thumbnail_dir) / f"plate_{num}.png"
                dest.write_bytes(zf.read(thumb_zip_path))
                thumb_disk_path = str(dest)
            elif thumb_zip_path not in names:
                thumb_disk_path = None
            # If thumbnail exists in ZIP but no thumbnail_dir requested, leave path as None

            m_data = meta.get(num, {})
            plates.append(PlateInfo(
                plate_number=num,
                thumbnail_path=thumb_disk_path,
                estimated_time=m_data.get("estimated_time", 0),
                filament_g=m_data.get("filament_g", 0.0),
            ))

    return plates
