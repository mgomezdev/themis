import json
import struct
import zipfile

from app.services.mesh_3mf_builder import (
    _model_settings_with_extruder, _patch_model_settings_extruder,
    _object_ids_from_model, build_sliceable_3mf, source_has_project_settings, stl_to_3mf,
)

_ONE_TRI_STL = """solid t
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 10 0 0
  vertex 0 10 0
 endloop
endfacet
endsolid t
"""


def test_model_settings_with_extruder_builds_objects():
    xml = _model_settings_with_extruder(["1", "2"], 3).decode("utf-8")
    assert '<object id="1">' in xml and '<object id="2">' in xml
    assert xml.count('key="extruder" value="3"') == 2


def test_patch_overrides_existing_object_extruder_and_preserves_others():
    src = (b'<?xml version="1.0" encoding="UTF-8"?>\n<config>'
           b'<object id="5"><metadata key="name" value="x"/>'
           b'<metadata key="extruder" value="1"/></object></config>')
    out = _patch_model_settings_extruder(src, 4).decode("utf-8")
    assert 'value="4"' in out and 'value="1"' not in out
    assert 'key="name"' in out  # unrelated metadata preserved


def test_patch_adds_extruder_when_absent():
    src = b'<?xml version="1.0"?>\n<config><object id="7"><metadata key="name" value="y"/></object></config>'
    out = _patch_model_settings_extruder(src, 2).decode("utf-8")
    assert 'key="extruder" value="2"' in out


def test_object_ids_from_model():
    model = b'<model><resources><object id="1" type="model"></object><object id="3"></object></resources></model>'
    assert _object_ids_from_model(model) == ["1", "3"]


def test_stl_to_3mf_writes_object_extruder(tmp_path):
    stl = tmp_path / "c.stl"; stl.write_text(_ONE_TRI_STL)
    out = tmp_path / "c.3mf"
    stl_to_3mf(str(stl), {"nozzle_diameter": ["0.4"]}, out, tool_index=2)
    with zipfile.ZipFile(out) as z:
        ms = z.read("Metadata/model_settings.config").decode("utf-8")
    assert 'key="extruder" value="3"' in ms  # tool 2 (0-based) -> extruder 3 (1-based)


def test_stl_to_3mf_omits_model_settings_when_tool_index_none(tmp_path):
    stl = tmp_path / "c.stl"; stl.write_text(_ONE_TRI_STL)
    out = tmp_path / "c.3mf"
    stl_to_3mf(str(stl), {"nozzle_diameter": ["0.4"]}, out)
    with zipfile.ZipFile(out) as z:
        assert "Metadata/model_settings.config" not in z.namelist()


def _binary_stl(path, triangles):
    data = b"\x00" * 80 + struct.pack("<I", len(triangles))
    for tri in triangles:
        data += struct.pack("<3f", 0.0, 0.0, 1.0)  # normal (ignored)
        for v in tri:
            data += struct.pack("<3f", *v)
        data += b"\x00\x00"  # attribute byte count
    path.write_bytes(data)


def test_stl_to_3mf_wraps_mesh_with_config(tmp_path):
    stl = tmp_path / "m.stl"
    # two triangles sharing an edge -> 4 unique vertices
    _binary_stl(stl, [
        ((0, 0, 0), (1, 0, 0), (1, 1, 0)),
        ((0, 0, 0), (1, 1, 0), (0, 1, 0)),
    ])
    out = stl_to_3mf(stl, {"printer_model": "X"}, tmp_path / "out.3mf")
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model",
                "Metadata/project_settings.config"} <= names
        model = z.read("3D/3dmodel.model").decode()
        assert model.count("<vertex ") == 4      # deduplicated
        assert model.count("<triangle ") == 2
        assert json.loads(z.read("Metadata/project_settings.config")) == {"printer_model": "X"}


def test_stl_to_3mf_rejects_empty(tmp_path):
    import pytest
    stl = tmp_path / "empty.stl"
    _binary_stl(stl, [])
    with pytest.raises(ValueError):
        stl_to_3mf(stl, {}, tmp_path / "out.3mf")


def _make_source(tmp_path):
    p = tmp_path / "src.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        z.writestr("3D/Objects/obj_1.model", "<mesh/>")
        z.writestr("Metadata/project_settings.config", json.dumps({"old": "settings"}))
        z.writestr("Metadata/model_settings.config", "<config><object/></config>")
        z.writestr("Metadata/slice_info.config", "<old/>")
        z.writestr("[Content_Types].xml", "<Types/>")
    return p


def _names(path):
    with zipfile.ZipFile(path) as z:
        return set(z.namelist())


def test_source_has_project_settings(tmp_path):
    src = _make_source(tmp_path)
    assert source_has_project_settings(src) is True
    bare = tmp_path / "bare.3mf"
    with zipfile.ZipFile(bare, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
    assert source_has_project_settings(bare) is False


def test_preserves_model_settings_swaps_project_keeps_slice_info(tmp_path):
    src = _make_source(tmp_path)
    out = build_sliceable_3mf(src, {"printer_model": "X"}, tmp_path / "out.3mf")
    names = _names(out)
    assert "3D/3dmodel.model" in names and "3D/Objects/obj_1.model" in names
    assert "Metadata/model_settings.config" in names      # preserved
    assert "Metadata/slice_info.config" in names          # preserved
    with zipfile.ZipFile(out) as z:
        cfg = json.loads(z.read("Metadata/project_settings.config"))
    assert cfg == {"printer_model": "X"}                   # swapped


def test_geometry_only_drops_model_settings(tmp_path):
    src = _make_source(tmp_path)
    out = build_sliceable_3mf(src, {"printer_model": "X"}, tmp_path / "geo.3mf", geometry_only=True)
    names = _names(out)
    assert "3D/3dmodel.model" in names                     # geometry kept
    assert "Metadata/model_settings.config" not in names   # dropped in recovery
    assert "Metadata/project_settings.config" in names


def test_build_sliceable_3mf_tool_index_patches_object_extruder(tmp_path):
    # Source has a model_settings.config; non-geometry_only → patch path.
    src = _make_source(tmp_path)
    out = build_sliceable_3mf(src, {"printer_model": "X"}, tmp_path / "tool.3mf", tool_index=1)
    with zipfile.ZipFile(out) as z:
        ms = z.read("Metadata/model_settings.config").decode("utf-8")
        ps = json.loads(z.read("Metadata/project_settings.config"))
    assert 'key="extruder" value="2"' in ms                # tool 1 (0-based) -> extruder 2 (1-based)
    assert ps == {"printer_model": "X"}                    # project settings still swapped


def test_build_sliceable_3mf_geometry_only_tool_index_creates_object_extruder(tmp_path):
    # geometry_only drops the source model_settings → recreate from the model's object ids
    # (this source's <model/> has none, so it falls back to id "1").
    src = _make_source(tmp_path)
    out = build_sliceable_3mf(src, {"printer_model": "X"}, tmp_path / "geo_tool.3mf",
                              geometry_only=True, tool_index=2)
    with zipfile.ZipFile(out) as z:
        ms = z.read("Metadata/model_settings.config").decode("utf-8")
    assert '<object id="1">' in ms and 'key="extruder" value="3"' in ms  # tool 2 -> extruder 3


def test_build_sliceable_3mf_remaps_paint_and_object_extruder(tmp_path):
    import re as _re
    from app.services.snapmaker.paint_remap import encode_nodes, decode_nodes
    painted = encode_nodes(("L", 3))                 # one triangle on filament 1 (state=3)
    src = tmp_path / "src.3mf"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        z.writestr("3D/Objects/o.model", f'<model><triangle paint_color="{painted}"/></model>')
        z.writestr("Metadata/project_settings.config", '{"old":1}')
        z.writestr("Metadata/model_settings.config",
                   '<?xml version="1.0"?>\n<config><object id="1">'
                   '<metadata key="extruder" value="1"/></object></config>')
    out = tmp_path / "out.3mf"
    build_sliceable_3mf(str(src), {"new": 1}, out,
                        filament_map=[{"model_filament": 1, "tool_index": 2}])  # filament1 -> tool2 (ext3)
    with zipfile.ZipFile(out) as z:
        obj = z.read("3D/Objects/o.model").decode("utf-8")
        ms = z.read("Metadata/model_settings.config").decode("utf-8")
    pc = _re.search(r'paint_color="([^"]+)"', obj).group(1)
    assert decode_nodes(pc) == ("L", 5)              # filament1 -> extruder3 -> state=5
    assert 'key="extruder" value="3"' in ms          # object base extruder remapped too
