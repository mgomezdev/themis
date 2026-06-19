"""remap.py — Apply Snapmaker tool routing to an already-built prepared 3MF.

``remap_3mf`` reads a prepared 3MF, rewrites paint_color attributes and/or
object extruder metadata in model_settings.config per the supplied routing
arguments, then writes the result back to the same path atomically (via a
temporary file).

Two routing modes (mutually exclusive):
  - ``filament_map`` (non-empty list of ``{model_filament, tool_index}`` dicts):
    remap ``paint_color`` attributes in all ``3D/*.model`` files **and** patch
    each object's base extruder in ``model_settings.config`` per the map.
  - ``tool_index`` (int, 0-based): set all objects to a single extruder
    (extruder = tool_index + 1).
  - Both ``None`` / ``filament_map=[]``: no-op (returns immediately without
    touching the file).
"""
from __future__ import annotations

import re
import defusedxml.ElementTree as ET  # safe parsing of untrusted XML
from xml.etree.ElementTree import SubElement, tostring  # serialization only — no parsing risk
import zipfile
from pathlib import Path

from .paint_remap import remap_paint_color

_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_START_PART_TYPE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"


def _find_model_part(entries: dict[str, bytes]) -> str | None:
    """Return the model part path from the StartPart relationship in _rels/.rels."""
    rels_data = entries.get("_rels/.rels")
    if not rels_data:
        return None
    try:
        root = ET.fromstring(rels_data)
        for rel in root.findall(f"{{{_RELS_NS}}}Relationship"):
            if rel.get("Type") == _START_PART_TYPE:
                return rel.get("Target", "").lstrip("/")
    except Exception:
        pass
    return None


# Matches any file under the 3D/ directory with a .model extension.
_3D_MODEL_RE = re.compile(r"3D/.*\.model$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers — copied verbatim from mesh_3mf_builder.py
# ---------------------------------------------------------------------------

def _model_settings_with_extruder(object_ids: list[str], extruder_1based: int) -> bytes:
    """A fresh model_settings.config assigning each object id to the given extruder."""
    objs = "".join(
        f'<object id="{oid}"><metadata key="extruder" value="{extruder_1based}"/></object>'
        for oid in object_ids
    )
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<config>{objs}</config>'.encode("utf-8")


def _object_ids_from_model(model_xml: bytes) -> list[str]:
    """Object ids declared in a 3D/3dmodel.model (resources/object id=...)."""
    return [m.decode() for m in re.findall(rb'<object[^>]*\bid="([^"]+)"', model_xml)]


def _patch_model_settings_extruder(model_settings: bytes, extruder_1based: int) -> bytes:
    """Set/override every <object>'s extruder metadata, preserving all other content."""
    root = ET.fromstring(model_settings)
    for obj in root.findall("object"):
        for md in list(obj.findall("metadata")):
            if md.get("key") == "extruder":
                obj.remove(md)
        md = SubElement(obj, "metadata")
        md.set("key", "extruder")
        md.set("value", str(extruder_1based))
    body = tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + body).encode("utf-8")


def _patch_model_settings_filament_map(model_settings: bytes, mapping: dict) -> bytes:
    """Remap each <object>'s base extruder per the filament mapping.

    For each object's extruder value ``e`` (1-based filament):
      new value = mapping.get(e, e - 1) + 1
    i.e. if ``e`` is in the mapping → its tool's extruder number (1-based);
    else identity (extruder stays the same as the logical filament).

    All other metadata on each object is preserved unchanged.
    """
    root = ET.fromstring(model_settings)
    for obj in root.findall("object"):
        for md in list(obj.findall("metadata")):
            if md.get("key") == "extruder":
                try:
                    old_e = int(md.get("value", "1"))
                except ValueError:
                    old_e = 1
                new_e = mapping.get(old_e, old_e - 1) + 1
                obj.remove(md)
                new_md = SubElement(obj, "metadata")
                new_md.set("key", "extruder")
                new_md.set("value", str(new_e))
    body = tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + body).encode("utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def remap_3mf(
    prepared_3mf: str | Path,
    *,
    tool_index: int | None = None,
    filament_map: list | None = None,
) -> None:
    """Apply tool routing to an already-built prepared 3MF in-place.

    Reads *prepared_3mf*, rewrites the relevant entries, and writes the result
    back to the same path atomically via a sibling temp file.

    Args:
        prepared_3mf: Path to the 3MF file to rewrite.
        tool_index:   0-based tool index; all objects are assigned extruder
                      ``tool_index + 1``.  Mutually exclusive with
                      ``filament_map``.
        filament_map: List of ``{"model_filament": int, "tool_index": int}``
                      dicts.  ``model_filament`` is 1-based; ``tool_index`` is
                      0-based.  When non-empty, all ``3D/*.model`` paint_color
                      attributes are remapped and object extruders are patched.

    Returns:
        None.  The file at *prepared_3mf* is replaced in-place.
    """
    use_map = bool(filament_map)
    if tool_index is None and not use_map:
        return

    prepared_3mf = Path(prepared_3mf)
    with zipfile.ZipFile(prepared_3mf) as zin:
        entries = {n: zin.read(n) for n in zin.namelist()}

    model_part = _find_model_part(entries)
    model_xml = entries.get(model_part, b"") if model_part else b""
    src_ms = entries.get("Metadata/model_settings.config", b"")
    mapping = {e["model_filament"]: e["tool_index"] for e in (filament_map or [])}

    tmp = prepared_3mf.with_suffix(".remap.3mf")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries.items():
            if name == "Metadata/model_settings.config":
                continue
            if use_map and _3D_MODEL_RE.match(name):
                txt = re.sub(
                    r'paint_color="([^"]+)"',
                    lambda m: f'paint_color="{remap_paint_color(m.group(1), mapping)}"',
                    data.decode("utf-8"),
                )
                zout.writestr(name, txt.encode("utf-8"))
            else:
                zout.writestr(name, data)
        if use_map:
            ms = (
                _patch_model_settings_filament_map(src_ms, mapping)
                if src_ms
                else _model_settings_with_extruder(
                    _object_ids_from_model(model_xml) or ["1"], 1
                )
            )
        else:
            ext = tool_index + 1
            ms = (
                _patch_model_settings_extruder(src_ms, ext)
                if src_ms
                else _model_settings_with_extruder(
                    _object_ids_from_model(model_xml) or ["1"], ext
                )
            )
        zout.writestr("Metadata/model_settings.config", ms)

    tmp.replace(prepared_3mf)
