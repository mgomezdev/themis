from __future__ import annotations
import json
import re
import defusedxml.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_START_PART_TYPE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"


def _find_model_part(names: set[str], zf: zipfile.ZipFile) -> str | None:
    """Return the model part path from the StartPart relationship in _rels/.rels.

    Per the 3MF spec §2.1.1, consumers MUST discover the primary 3D payload
    via this relationship rather than assuming a fixed filename.
    Returns the path without a leading slash, or None if not found.
    """
    if "_rels/.rels" not in names:
        return None
    try:
        root = ET.fromstring(zf.read("_rels/.rels"))
        for rel in root.findall(f"{{{_RELS_NS}}}Relationship"):
            if rel.get("Type") == _START_PART_TYPE:
                return rel.get("Target", "").lstrip("/")
    except Exception:
        pass
    return None


def _plates_from_model_settings(names: set[str], zf: zipfile.ZipFile) -> set[int]:
    """Discover plate numbers from Metadata/model_settings.config (OrcaSlicer XML format).

    Handles pre-slice 3MFs (e.g. gridfinity-customizer) that record plate
    assignments in XML rather than via thumbnails or slice_info.

    Two plate-ID encodings exist in the wild:
      - OrcaSlicer native: <metadata key="plater_id" value="N"/>
      - Simplified (bundle_3mf.py style): <id value="N"/>
    """
    if "Metadata/model_settings.config" not in names:
        return set()
    try:
        root = ET.fromstring(zf.read("Metadata/model_settings.config"))
        ids: set[int] = set()
        for p in root.findall("plate"):
            # OrcaSlicer native format
            for md in p.findall("metadata"):
                if md.get("key") == "plater_id":
                    ids.add(int(md.get("value")))
                    break
            else:
                # Simplified format: <id value="N"/>
                id_el = p.find("id")
                if id_el is not None:
                    ids.add(int(id_el.get("value")))
        return ids
    except Exception:
        return set()

from .override_inspector import CURATED_KEYS


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


_SETTING_LABELS: dict[str, str] = {
    "enable_support": "Enable supports",
    "support_type": "Support type",
    "support_threshold_angle": "Support threshold angle",
    "support_on_build_plate_only": "Support on build plate only",
    "raft_layers": "Raft layers",
    "brim_type": "Brim type",
    "brim_width": "Brim width",
    "sparse_infill_density": "Infill density",
    "sparse_infill_pattern": "Infill pattern",
    "wall_loops": "Wall loops",
    "top_shell_layers": "Top layers",
    "bottom_shell_layers": "Bottom layers",
    "layer_height": "Layer height",
    "ironing_type": "Ironing type",
}


def parse_embedded_settings(file_path: str) -> list[dict]:
    """Return curated print settings baked into the 3MF's project_settings.config."""
    try:
        with zipfile.ZipFile(file_path) as zf:
            actual = next(
                (n for n in zf.namelist()
                 if n.lower() == "metadata/project_settings.config"),
                None,
            )
            if actual is None:
                return []
            ps = json.loads(zf.read(actual))
    except (zipfile.BadZipFile, json.JSONDecodeError, OSError):
        return []

    out = []
    for key in CURATED_KEYS:
        if key not in ps:
            continue
        val = ps[key]
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val)
        else:
            val = str(val)
        out.append({"key": key, "label": _SETTING_LABELS.get(key, key), "value": val})
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

        # Pre-slice 3MFs (e.g. gridfinity-customizer) may only have model_settings.config
        if not plate_numbers:
            plate_numbers = _plates_from_model_settings(names, zf)

        # Last resort: any 3MF with a declared StartPart model is a single-plate file
        # (e.g. PrusaSlicer exports). Per spec §2.1.1, discover via _rels/.rels.
        if not plate_numbers and _find_model_part(names, zf) is not None:
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
