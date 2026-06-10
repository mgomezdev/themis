import json
import struct
import zipfile

from app.services.mesh_3mf_builder import (
    build_sliceable_3mf, source_has_project_settings, stl_to_3mf,
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


def _binary_stl(path, triangles):
    data = b"\x00" * 80 + struct.pack("<I", len(triangles))
    for tri in triangles:
        data += struct.pack("<3f", 0.0, 0.0, 1.0)  # normal (ignored)
        for v in tri:
            data += struct.pack("<3f", *v)
        data += b"\x00\x00"  # attribute byte count
    path.write_bytes(data)


def test_stl_to_3mf_omits_model_settings(tmp_path):
    stl = tmp_path / "c.stl"; stl.write_text(_ONE_TRI_STL)
    out = tmp_path / "c.3mf"
    stl_to_3mf(str(stl), {"nozzle_diameter": ["0.4"]}, out)
    with zipfile.ZipFile(out) as z:
        assert "Metadata/model_settings.config" not in z.namelist()


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
