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
import zipfile
from pathlib import Path

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


def build_sliceable_3mf(
    source_3mf: str | Path,
    project_config: dict,
    out_path: str | Path,
    geometry_only: bool = False,
) -> Path:
    """Copy ``source_3mf`` preserving geometry, replacing ``project_settings.config``
    with ``project_config``.

    Normally ``model_settings.config`` (per-object overrides, modifiers, paint
    refs) is preserved. ``geometry_only=True`` drops it too — the recovery tier,
    mirroring the GUI's "import geometry only": discard all the file's settings
    and apply ours fresh.

    Vendor-specific routing (extruder remapping, paint recolor) is applied
    separately via the printer client's ``remap_sliceable_3mf`` hook after this
    call — this function is vendor-agnostic.
    """
    source_3mf, out_path = Path(source_3mf), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    config_bytes = json.dumps(project_config).encode("utf-8")

    drop = set(_DROPPED) | {_REPLACED}
    if geometry_only:
        drop.add(_MODEL)

    with zipfile.ZipFile(source_3mf) as zin, zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.namelist():
            low = item.lower()
            if low in drop:
                continue
            zout.writestr(item, zin.read(item))
        zout.writestr("Metadata/project_settings.config", config_bytes)
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


def stl_to_3mf(stl_path: str | Path, project_config: dict, out_path: str | Path) -> Path:
    """Wrap an STL mesh into a sliceable 3MF carrying the generated project config.

    Deduplicates vertices and emits a single model object. Used when the upload is
    a bare STL (no 3MF container / model_settings to preserve).

    Vendor-specific routing (extruder assignment) is applied separately via the
    printer client's ``remap_sliceable_3mf`` hook after this call.
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
    return out_path


def source_has_project_settings(source_3mf: str | Path) -> bool:
    """True if the uploaded 3MF carries embedded slicer settings (a real project,
    not a bare geometry 3MF). Used to decide whether the override check runs."""
    try:
        with zipfile.ZipFile(source_3mf) as z:
            return any(n.lower() == _REPLACED for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
