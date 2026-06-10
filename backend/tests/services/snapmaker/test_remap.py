"""Tests for remap_3mf() — standalone tool-routing for a prepared 3MF.

Paint node construction:
  - Filament 1 = state 3  (state = filament + 2).  Encoded via encode_nodes(("L", 3)) == "0C".
  - Mapping {1: 2} (filament 1 → tool_index 2): new_state = tool_index + 3 = 5.
  - Expected remapped node: ("L", 5).
"""
import re
import zipfile

from app.services.snapmaker.paint_remap import encode_nodes, decode_nodes
from app.services.snapmaker.remap import remap_3mf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prepared(tmp_path, *, paint=None, object_extruder="1", with_model_settings=True):
    p = tmp_path / "prepared.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        if paint is not None:
            z.writestr(
                "3D/Objects/o.model",
                f'<model><triangle paint_color="{paint}"/></model>',
            )
        z.writestr("Metadata/project_settings.config", "{}")
        if with_model_settings:
            z.writestr(
                "Metadata/model_settings.config",
                f'<?xml version="1.0"?>\n<config><object id="1">'
                f'<metadata key="extruder" value="{object_extruder}"/></object></config>',
            )
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_remap_3mf_tool_index_sets_object_extruder(tmp_path):
    p = _prepared(tmp_path, object_extruder="1")
    remap_3mf(p, tool_index=2)  # tool 2 -> extruder 3
    with zipfile.ZipFile(p) as z:
        ms = z.read("Metadata/model_settings.config").decode("utf-8")
    assert 'key="extruder" value="3"' in ms


def test_remap_3mf_filament_map_remaps_paint_and_object(tmp_path):
    # filament 1 → state 3, encoded as "0C"
    painted = encode_nodes(("L", 3))
    p = _prepared(tmp_path, paint=painted, object_extruder="1")
    remap_3mf(p, filament_map=[{"model_filament": 1, "tool_index": 2}])  # filament1 -> tool2 (ext3)
    with zipfile.ZipFile(p) as z:
        obj = z.read("3D/Objects/o.model").decode("utf-8")
        ms = z.read("Metadata/model_settings.config").decode("utf-8")
    pc = re.search(r'paint_color="([^"]+)"', obj).group(1)
    # filament 1 (state 3) remapped to tool_index 2 → state 5
    assert decode_nodes(pc) == ("L", 5)
    assert 'key="extruder" value="3"' in ms


def test_remap_3mf_noop_when_both_none(tmp_path):
    p = _prepared(tmp_path, object_extruder="2")
    before = p.read_bytes()
    remap_3mf(p)
    assert p.read_bytes() == before
