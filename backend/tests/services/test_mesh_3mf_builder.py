import json
import zipfile

from app.services.mesh_3mf_builder import build_sliceable_3mf, source_has_project_settings


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


def test_preserves_model_settings_swaps_project_drops_slice_info(tmp_path):
    src = _make_source(tmp_path)
    out = build_sliceable_3mf(src, {"printer_model": "X"}, tmp_path / "out.3mf")
    names = _names(out)
    assert "3D/3dmodel.model" in names and "3D/Objects/obj_1.model" in names
    assert "Metadata/model_settings.config" in names      # preserved
    assert "Metadata/slice_info.config" not in names       # dropped
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
