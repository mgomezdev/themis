"""Build a combined multi-material 3MF from project items for Orca arrangement.

The output 3MF contains:
- One <object> per unique (file_id, slot_index) pair (with full STL geometry)
- N <item> references per object (N = quantity), so OrcaSlicer treats each
  copy as an independent part for plate-packing
- Metadata/model_settings.config — per-object extruder (slot) assignments
- Metadata/project_settings.config — filament parallel arrays + bed dimensions
"""
from __future__ import annotations

import json
import os
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class FilamentSlot:
    uuid: str
    display_name: str
    filament_type: str
    color_hex: str


# ---------------------------------------------------------------------------
# STL parsing (binary and ASCII)
# ---------------------------------------------------------------------------

def _is_binary_stl(path: Path) -> bool:
    with open(path, "rb") as f:
        header = f.read(80)
        if not header.startswith(b"solid"):
            return True
        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            return False
        count = struct.unpack("<I", count_bytes)[0]
    return os.path.getsize(path) == 80 + 4 + count * 50


def _parse_binary_stl(path: Path) -> list[tuple]:
    tris = []
    with open(path, "rb") as f:
        f.read(80)
        count = struct.unpack("<I", f.read(4))[0]
        for _ in range(count):
            f.read(12)  # normal
            verts = [struct.unpack("<fff", f.read(12)) for _ in range(3)]
            f.read(2)   # attribute
            tris.append(tuple(verts))
    return tris


def _parse_ascii_stl(path: Path) -> list[tuple]:
    tris, verts = [], []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("vertex"):
                parts = line.split()
                if len(parts) >= 4:
                    verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
                    if len(verts) == 3:
                        tris.append(tuple(verts))
                        verts = []
    return tris


def _parse_stl(path: Path) -> list[tuple]:
    return _parse_binary_stl(path) if _is_binary_stl(path) else _parse_ascii_stl(path)


# ---------------------------------------------------------------------------
# 3MF XML builders
# ---------------------------------------------------------------------------

def _object_xml(object_id: int, tris: list[tuple]) -> str:
    vlines, tlines = [], []
    idx = 0
    for tri in tris:
        for x, y, z in tri:
            vlines.append(f'          <vertex x="{x}" y="{y}" z="{z}"/>')
        tlines.append(f'          <triangle v1="{idx}" v2="{idx+1}" v3="{idx+2}"/>')
        idx += 3
    vertices = "\n".join(vlines)
    triangles = "\n".join(tlines)
    return (
        f'  <object id="{object_id}" type="model">\n'
        f'    <mesh>\n'
        f'      <vertices>\n{vertices}\n      </vertices>\n'
        f'      <triangles>\n{triangles}\n      </triangles>\n'
        f'    </mesh>\n'
        f'  </object>'
    )


def _model_xml(objects_xml: list[str], build_items: list[str]) -> str:
    resources = "\n".join(objects_xml)
    build = "\n    ".join(build_items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
        f'  <resources>\n{resources}\n  </resources>\n'
        f'  <build>\n    {build}\n  </build>\n'
        '</model>'
    )


def _model_settings_xml(object_slot_map: dict[int, int]) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    for obj_id, slot in sorted(object_slot_map.items()):
        lines.append(
            f'  <object id="{obj_id}">'
            f'<metadata key="extruder" value="{slot}"/>'
            f'</object>'
        )
    lines.append("</config>")
    return "\n".join(lines)


def _content_types_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        '<Default Extension="config" ContentType="application/octet-stream"/>'
        '</Types>'
    )


_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
    '</Relationships>'
)


# ---------------------------------------------------------------------------
# ProjectPackBuilder
# ---------------------------------------------------------------------------

class ProjectPackBuilder:
    """Assemble a combined multi-material 3MF ready for Orca arrangement.

    Each item dict must have:
        file_path: Path   — path to an STL file on disk
        quantity:  int    — number of copies (≥1)
        slot_index: int   — 1-based filament slot (from caller's slot assignment)
    """

    def build(
        self,
        items: list[dict],
        bed_x: float,
        bed_y: float,
        filament_slots: list[FilamentSlot],
        out_path: Path,
    ) -> None:
        # Group by (file_path, slot_index) so same STL at same slot shares one object def.
        groups: dict[tuple, dict] = {}  # (file_path, slot_index) → {object_id, quantity, tris}
        object_id = 0

        for item in items:
            key = (str(item["file_path"]), item["slot_index"])
            if key not in groups:
                object_id += 1
                groups[key] = {
                    "object_id": object_id,
                    "slot_index": item["slot_index"],
                    "quantity": item["quantity"],
                    "tris": _parse_stl(Path(item["file_path"])),
                }
            else:
                # Same file at same slot — accumulate quantity
                groups[key]["quantity"] += item["quantity"]

        # Build model XML: one <object> per group, N <item> refs per group
        objects_xml: list[str] = []
        build_items: list[str] = []
        object_slot_map: dict[int, int] = {}

        for group in groups.values():
            oid = group["object_id"]
            objects_xml.append(_object_xml(oid, group["tris"]))
            object_slot_map[oid] = group["slot_index"]
            for _ in range(group["quantity"]):
                build_items.append(f'<item objectid="{oid}"/>')

        # project_settings.config — minimal filament arrays + bed
        corners = [
            f"0x0",
            f"{bed_x}x0",
            f"{bed_x}x{bed_y}",
            f"0x{bed_y}",
        ]
        project_cfg = {
            "printable_area": corners,
            "printable_height": "300",
            "filament_settings_id": [s.display_name for s in filament_slots],
            "filament_colour": [s.color_hex for s in filament_slots],
            "filament_type": [s.filament_type for s in filament_slots],
            "from": "user",
        }

        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", _content_types_xml())
            zf.writestr("_rels/.rels", _RELS)
            zf.writestr(
                "3D/3dmodel.model",
                _model_xml(objects_xml, build_items).encode("utf-8"),
            )
            zf.writestr(
                "Metadata/model_settings.config",
                _model_settings_xml(object_slot_map).encode("utf-8"),
            )
            zf.writestr(
                "Metadata/project_settings.config",
                json.dumps(project_cfg, ensure_ascii=False, indent=2).encode("utf-8"),
            )
