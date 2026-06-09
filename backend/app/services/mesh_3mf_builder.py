"""Assemble a sliceable 3MF: keep the uploaded model's geometry and model-attached
overrides, swap in Themis's generated project config.

Per the override-handling design, only the global ``project_settings.config`` is
replaced. ``model_settings.config`` (per-object overrides, modifiers, support
enforcers/blockers, per-part extruder map) and the mesh paint inside ``3D/*`` are
preserved so they re-layer onto the new presets. ``slice_info.config`` is dropped
(Orca regenerates it). See the ``slicer-cli-architecture`` memory.
"""
from __future__ import annotations

import json
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from . import paint_remap as _paint_remap

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
    "</Relationships>"
)

# Embedded files Themis owns / regenerates — everything else is copied verbatim.
_REPLACED = "metadata/project_settings.config"
_MODEL = "metadata/model_settings.config"
_DROPPED = ()


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
        md = ET.SubElement(obj, "metadata")
        md.set("key", "extruder")
        md.set("value", str(extruder_1based))
    body = ET.tostring(root, encoding="unicode")
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
                new_md = ET.SubElement(obj, "metadata")
                new_md.set("key", "extruder")
                new_md.set("value", str(new_e))
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + body).encode("utf-8")


def build_sliceable_3mf(
    source_3mf: str | Path,
    project_config: dict,
    out_path: str | Path,
    geometry_only: bool = False,
    tool_index: int | None = None,
    filament_map: list | None = None,
) -> Path:
    """Copy ``source_3mf`` preserving geometry, replacing ``project_settings.config``
    with ``project_config``.

    Normally ``model_settings.config`` (per-object overrides, modifiers, paint
    refs) is preserved. ``geometry_only=True`` drops it too — the recovery tier,
    mirroring the GUI's "import geometry only": discard all the file's settings
    and apply ours fresh.

    ``filament_map`` and ``tool_index`` are mutually exclusive:
    - ``filament_map`` (non-empty list of ``{model_filament, tool_index}`` dicts):
      remap paint_color attributes in all ``3D/*.model`` files and patch each
      object's base extruder in ``model_settings.config`` per the map.
    - ``tool_index`` (int, 0-based): set all objects to a single extruder.
    - ``filament_map=None`` or ``filament_map=[]``: no extruder rewriting; the
      source model_settings is copied verbatim (byte-identical to old behaviour).
    """
    source_3mf, out_path = Path(source_3mf), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    config_bytes = json.dumps(project_config).encode("utf-8")

    # Determine which path to take.
    use_filament_map = bool(filament_map)  # non-None AND non-empty
    drop = set(_DROPPED) | {_REPLACED}
    if geometry_only:
        drop.add(_MODEL)
    if tool_index is not None and not use_filament_map:
        drop.add(_MODEL)  # single-extruder path re-emits model_settings below
    if use_filament_map:
        drop.add(_MODEL)  # multi-material path re-emits model_settings below

    # Build the {model_filament(1-based): tool_index(0-based)} mapping dict.
    mapping: dict[int, int] = {}
    if use_filament_map:
        mapping = {e["model_filament"]: e["tool_index"] for e in filament_map}

    _3D_MODEL_RE = re.compile(r"3D/.*\.model$", re.IGNORECASE)

    with zipfile.ZipFile(source_3mf) as zin, zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        model_xml = b""
        src_model_settings = b""
        for item in zin.namelist():
            low = item.lower()
            if low == "3d/3dmodel.model":
                model_xml = zin.read(item)
            if low == _MODEL:
                src_model_settings = zin.read(item)
            if low in drop:
                continue
            if use_filament_map and _3D_MODEL_RE.match(item):
                # Rewrite paint_color attributes in-place for all 3D model files.
                content = zin.read(item).decode("utf-8")
                content = re.sub(
                    r'paint_color="([^"]+)"',
                    lambda m: f'paint_color="{_paint_remap.remap_paint_color(m.group(1), mapping)}"',
                    content,
                )
                zout.writestr(item, content.encode("utf-8"))
            else:
                zout.writestr(item, zin.read(item))
        zout.writestr("Metadata/project_settings.config", config_bytes)
        if use_filament_map:
            if src_model_settings:
                ms = _patch_model_settings_filament_map(src_model_settings, mapping)
            else:
                # No source model_settings (geometry_only or bare 3MF) — nothing to remap.
                ms = _model_settings_with_extruder(
                    _object_ids_from_model(model_xml) or ["1"], 1
                )
            zout.writestr("Metadata/model_settings.config", ms)
        elif tool_index is not None:
            ext = tool_index + 1
            if src_model_settings and not geometry_only:
                ms = _patch_model_settings_extruder(src_model_settings, ext)
            else:
                ms = _model_settings_with_extruder(_object_ids_from_model(model_xml) or ["1"], ext)
            zout.writestr("Metadata/model_settings.config", ms)
    return out_path


def _parse_stl(path: Path) -> list[tuple[tuple[float, float, float], ...]]:
    """Return a list of triangles (each 3 (x,y,z) vertices) from a binary or ASCII STL."""
    data = path.read_bytes()
    if len(data) >= 84:
        ntri = struct.unpack_from("<I", data, 80)[0]
        if len(data) == 84 + ntri * 50:  # exact binary-STL size → binary
            tris = []
            off = 84
            for _ in range(ntri):
                vs = struct.unpack_from("<9f", data, off + 12)  # skip 12-byte normal
                tris.append(((vs[0], vs[1], vs[2]), (vs[3], vs[4], vs[5]), (vs[6], vs[7], vs[8])))
                off += 50
            return tris
    # ASCII fallback
    nums = re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data.decode("utf-8", "ignore"))
    verts = [(float(a), float(b), float(c)) for a, b, c in nums]
    return [tuple(verts[i:i + 3]) for i in range(0, len(verts) - 2, 3)]


def stl_to_3mf(stl_path: str | Path, project_config: dict, out_path: str | Path,
               tool_index: int | None = None, filament_map: list | None = None) -> Path:
    """Wrap an STL mesh into a sliceable 3MF carrying the generated project config.

    Deduplicates vertices and emits a single model object. Used when the upload is
    a bare STL (no 3MF container / model_settings to preserve).
    """
    stl_path, out_path = Path(stl_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tris = _parse_stl(stl_path)
    if not tris:
        raise ValueError(f"no triangles parsed from STL: {stl_path}")

    index: dict[tuple[float, float, float], int] = {}
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for tri in tris:
        face = []
        for v in tri:
            i = index.get(v)
            if i is None:
                i = len(vertices)
                index[v] = i
                vertices.append(v)
            face.append(i)
        faces.append((face[0], face[1], face[2]))

    v_xml = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in vertices)
    t_xml = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces)
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        "<resources><object id=\"1\" type=\"model\"><mesh>"
        f"<vertices>{v_xml}</vertices><triangles>{t_xml}</triangles>"
        "</mesh></object></resources>"
        '<build><item objectid="1"/></build></model>'
    )

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/project_settings.config", json.dumps(project_config))
        if tool_index is not None:
            z.writestr("Metadata/model_settings.config",
                       _model_settings_with_extruder(["1"], tool_index + 1))
    return out_path


def source_has_project_settings(source_3mf: str | Path) -> bool:
    """True if the uploaded 3MF carries embedded slicer settings (a real project,
    not a bare geometry 3MF). Used to decide whether the override check runs."""
    try:
        with zipfile.ZipFile(source_3mf) as z:
            return any(n.lower() == _REPLACED for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
