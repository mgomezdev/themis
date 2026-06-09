import zipfile, re
from pathlib import Path
from app.services.paint_remap import decode_nodes, encode_nodes, remap_paint_color

_FIXTURE = Path(r"C:/Users/mgome/Downloads/Hausdeko+#41+-+Welcome+Home+-+Türschild+-+Makerworld.3mf")


def _fixture_paint_colors():
    with zipfile.ZipFile(_FIXTURE) as z:
        for n in z.namelist():
            if n.endswith(".model"):
                raw = z.read(n).decode("utf-8", "ignore")
                pcs = re.findall(r'paint_color="([^"]+)"', raw)
                if pcs:
                    return pcs
    return []


def test_decode_encode_roundtrip_on_real_paint():
    # Semantic (node-level) round-trip: re-decoding the re-encoded nodes yields
    # identical nodes. Proves the node-level codec is self-consistent regardless
    # of OrcaSlicer's non-canonical trailing padding bit (which a byte-exact
    # encode round-trip cannot reproduce and is therefore the wrong invariant).
    pcs = [p for p in _fixture_paint_colors() if p]
    assert pcs, "fixture has painted triangles"
    for pc in pcs[:200]:
        nodes = decode_nodes(pc)
        assert decode_nodes(encode_nodes(nodes)) == nodes


def test_remap_swaps_filament_leaf_nodes():
    nodes = [3, 7, 4, 5, 0, 1, 6]          # leaves 3..6 + SPLIT(7) + NONE(0) + ENFORCER(1)
    assert decode_nodes(encode_nodes(nodes)) == nodes
    remapped = decode_nodes(remap_paint_color(encode_nodes(nodes), {1: 2}))  # filament1 -> tool2 (ext3 -> node5)
    assert remapped == [5, 7, 4, 5, 0, 1, 6]


def test_remap_identity_is_noop():
    pc = next(p for p in _fixture_paint_colors() if p)
    assert remap_paint_color(pc, {}) == pc


def test_remap_identity_is_byte_exact_on_real_paint():
    pcs = [p for p in _fixture_paint_colors() if p]
    assert pcs
    for pc in pcs[:200]:
        assert remap_paint_color(pc, {}) == pc   # surgical: identity touches nothing
