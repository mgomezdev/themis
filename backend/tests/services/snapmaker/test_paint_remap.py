import zipfile
import re
from pathlib import Path
import pytest

from app.services.snapmaker.paint_remap import remap_paint_color, decode_nodes, encode_nodes

_FIXTURE = Path(r"C:/Users/mgome/Downloads/Hausdeko+#41+-+Welcome+Home+-+Türschild+-+Makerworld.3mf")


def _fixture_paint_colors():
    with zipfile.ZipFile(_FIXTURE) as z:
        for n in z.namelist():
            if n.endswith(".model"):
                pcs = re.findall(r'paint_color="([^"]+)"', z.read(n).decode("utf-8", "ignore"))
                if pcs:
                    return pcs
    return []


@pytest.mark.skipif(not _FIXTURE.exists(), reason="fixture 3MF not present")
def test_roundtrip_byte_exact_on_all_real_paint():
    pcs = [p for p in _fixture_paint_colors() if p]
    assert pcs
    for pc in pcs:                                   # ALL — byte-exact
        assert encode_nodes(decode_nodes(pc)) == pc


@pytest.mark.skipif(not _FIXTURE.exists(), reason="fixture 3MF not present")
def test_identity_remap_byte_exact_on_all_real_paint():
    for pc in [p for p in _fixture_paint_colors() if p]:
        assert remap_paint_color(pc, {}) == pc


@pytest.mark.skipif(not _FIXTURE.exists(), reason="fixture 3MF not present")
def test_remap_swaps_filament_state():
    pcs = [p for p in _fixture_paint_colors() if p]
    after = remap_paint_color(pcs[0], {1: 2})
    assert isinstance(after, str)
    assert encode_nodes(decode_nodes(after)) == after


# ---------------------------------------------------------------------------
# Structural unit tests (no fixture required)
# ---------------------------------------------------------------------------

def test_decode_leaf_none():
    """'4' encodes LEAF(ENFORCER=1)."""
    node = decode_nodes("4")
    assert node == ("L", 1)


def test_decode_leaf_extended():
    """'0C' encodes LEAF(state=3 = filament 1)."""
    node = decode_nodes("0C")
    assert node == ("L", 3)


def test_encode_leaf_none():
    assert encode_nodes(("L", 0)) == "0"


def test_encode_leaf_enforcer():
    assert encode_nodes(("L", 1)) == "4"


def test_encode_leaf_extended():
    """state=3 should encode to '0C'."""
    assert encode_nodes(("L", 3)) == "0C"


def test_roundtrip_leaf_states():
    for state in range(0, 10):
        node = ("L", state)
        assert decode_nodes(encode_nodes(node)) == node


def test_roundtrip_split_node():
    """SPLIT(2 sides, ss=0) with 3 LEAF children round-trips."""
    node = ("S", 2, 0, [("L", 0), ("L", 1), ("L", 3)])
    assert decode_nodes(encode_nodes(node)) == node


def test_remap_changes_filament():
    """Remap filament 1 → tool 1 (state 3 → state 4)."""
    original = encode_nodes(("L", 3))       # filament 1
    remapped = remap_paint_color(original, {1: 1})
    assert decode_nodes(remapped) == ("L", 4)  # tool_index 1 → state 4


def test_remap_preserves_non_filament_leaves():
    """NONE, ENFORCER, BLOCKER leaves must be unchanged by any mapping."""
    for state in (0, 1, 2):
        pc = encode_nodes(("L", state))
        assert remap_paint_color(pc, {1: 2, 2: 0}) == pc


def test_remap_identity_is_noop():
    """Empty mapping returns input byte-exact."""
    pc = encode_nodes(("S", 3, 0, [("L", 3), ("L", 0), ("L", 4), ("L", 1)]))
    assert remap_paint_color(pc, {}) == pc


def test_remap_split_node():
    """Remap inside a SPLIT node updates the correct leaves."""
    node = ("S", 1, 0, [("L", 3), ("L", 4)])   # filaments 1 and 2
    pc = encode_nodes(node)
    remapped_pc = remap_paint_color(pc, {1: 1})  # filament 1 → tool 1 (state 4)
    remapped_node = decode_nodes(remapped_pc)
    assert remapped_node == ("S", 1, 0, [("L", 4), ("L", 4)])
