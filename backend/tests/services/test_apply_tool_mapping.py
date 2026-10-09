"""Tool mapping is a slicing-provider capability (BIZ-251 Phase E)."""
import re
import shutil
import zipfile

import pytest

from app.services.providers.laminus.adapter import LaminusSlicingProvider
from app.services.providers.slicing import SlicingProvider, SlicingProviderError
from app.services.snapmaker.paint_remap import decode_nodes, encode_nodes
from app.services.snapmaker.remap import remap_3mf
from tests.fake_providers import FakeSlicingProvider


def _prepared(tmp_path, name="prepared.3mf", *, paint=None, object_extruder="1"):
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        if paint is not None:
            z.writestr("3D/Objects/o.model", f'<model><triangle paint_color="{paint}"/></model>')
        z.writestr("Metadata/project_settings.config", "{}")
        z.writestr("Metadata/model_settings.config",
                   '<?xml version="1.0"?>\n<config><object id="1">'
                   f'<metadata key="extruder" value="{object_extruder}"/></object></config>')
    return p


def _members(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


@pytest.fixture
def laminus():
    return LaminusSlicingProvider("http://127.0.0.1:1")  # unreachable on purpose


def test_base_default_unsupported():
    assert SlicingProvider.TOOL_MAPPING is False
    assert FakeSlicingProvider.TOOL_MAPPING is False
    with pytest.raises(SlicingProviderError, match="(?i)tool mapping|unsupported"):
        FakeSlicingProvider().apply_tool_mapping(None, tool_index=1)


def test_laminus_declares_tool_mapping(laminus):
    assert LaminusSlicingProvider.TOOL_MAPPING is True


def test_tool_index_matches_remap_3mf(tmp_path, laminus):
    a = _prepared(tmp_path, "a.3mf")
    b = tmp_path / "b.3mf"
    shutil.copy(a, b)
    laminus.apply_tool_mapping(a, tool_index=2)
    remap_3mf(b, tool_index=2)
    assert _members(a) == _members(b)
    assert b'key="extruder" value="3"' in _members(a)["Metadata/model_settings.config"]


def test_filament_map_matches_remap_3mf_and_rewrites_paint(tmp_path, laminus):
    fmap = [{"model_filament": 1, "tool_index": 2}]
    a = _prepared(tmp_path, "a.3mf", paint=encode_nodes(("L", 3)))
    b = tmp_path / "b.3mf"
    shutil.copy(a, b)
    laminus.apply_tool_mapping(a, filament_map=fmap)
    remap_3mf(b, filament_map=fmap)
    assert _members(a) == _members(b)
    pc = re.search(r'paint_color="([^"]+)"', _members(a)["3D/Objects/o.model"].decode()).group(1)
    assert decode_nodes(pc) == ("L", 5)


def test_noop_when_nothing_requested(tmp_path, laminus):
    p = _prepared(tmp_path, object_extruder="2")
    before = p.read_bytes()
    laminus.apply_tool_mapping(p)
    assert p.read_bytes() == before


def test_empty_filament_map_is_noop(tmp_path, laminus):
    p = _prepared(tmp_path, object_extruder="2")
    before = p.read_bytes()
    laminus.apply_tool_mapping(p, filament_map=[])
    assert p.read_bytes() == before


def test_tool_index_and_filament_map_are_exclusive(tmp_path, laminus):
    p = _prepared(tmp_path)
    before = p.read_bytes()
    with pytest.raises((SlicingProviderError, ValueError)):
        laminus.apply_tool_mapping(p, tool_index=1, filament_map=[{"model_filament": 1, "tool_index": 0}])
    assert p.read_bytes() == before
