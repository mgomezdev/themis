"""End-to-end test: slicing a painted multi-material 3MF with a non-identity
filament_map produces DIFFERENT emitted tool usage than the identity map.

This test requires the real OrcaSlicer executable and the Hausdeko #41 fixture
file. It is skipped automatically when either is absent.

The paint_color remap now uses the correct OrcaSlicer TriangleSelector format
(nibbles read right-to-left, LSB-first within each nibble), so the remapped
3MF is valid by construction and does not trigger any OrcaSlicer crash.
"""
import sys
from pathlib import Path
import pytest

_FIXTURE = Path(r"C:/Users/mgome/Downloads/Hausdeko+#41+-+Welcome+Home+-+Türschild+-+Makerworld.3mf")


@pytest.mark.skipif(not _FIXTURE.exists(), reason="Hausdeko fixture 3MF not present")
def test_remap_changes_emitted_tool_usage(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from spike_filament_remap import _build_config, _run_slice, _extract_tool_info
    from app.services.preset_resolver import PresetResolver
    from app.services.mesh_3mf_builder import build_sliceable_3mf
    from app.config import get_orca_executable
    cfg = _build_config(PresetResolver())
    ident = tmp_path / "id.3mf"
    swap = tmp_path / "sw.3mf"
    build_sliceable_3mf(str(_FIXTURE), cfg, ident, filament_map=[])
    build_sliceable_3mf(str(_FIXTURE), cfg, swap,
                        filament_map=[{"model_filament": 1, "tool_index": 1},
                                      {"model_filament": 2, "tool_index": 0}])
    orca = get_orca_executable()
    gcodes_a = _run_slice(orca, ident, tmp_path / "a")
    gcodes_b = _run_slice(orca, swap, tmp_path / "b")
    assert gcodes_a, "OrcaSlicer produced no gcode for identity variant"
    assert gcodes_b, "OrcaSlicer produced no gcode for swapped variant"
    a = _extract_tool_info(gcodes_a[0].read_text(errors="ignore"))
    b = _extract_tool_info(gcodes_b[0].read_text(errors="ignore"))
    assert a != b, (
        f"Expected different tool usage after remap but got identical results.\n"
        f"identity: {a}\nswapped: {b}"
    )
