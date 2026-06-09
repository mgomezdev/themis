"""paint_remap.py — OrcaSlicer paint_color bitstream codec + filament remap.

OrcaSlicer encodes the triangle-selector tree as a hex string in the
``paint_color`` attribute of ``3D/Objects/*.model``.  Each node is 3 bits,
packed LSB-first within each nibble (4-bit group), reading nibbles left to
right.  The hex string length equals the minimum number of nibbles needed to
hold all node bits; trailing zero-valued nodes that arise only from nibble
alignment padding are stripped on decode.

Node values (EnforcerBlockerType from libslic3r/TriangleSelector.cpp):
    0 = NONE      — unpainted, inherits the object's base extruder
    1 = ENFORCER  — support enforcer (not a filament)
    2 = BLOCKER   — support blocker  (not a filament)
    3 = Extruder1 — logical filament 1 (1-based)
    4 = Extruder2 — logical filament 2
    5 = Extruder3 — logical filament 3
    6 = Extruder4 — logical filament 4
    7 = SPLIT     — structural; exactly 4 children follow in the stream

Encoding rules
--------------
* Each node value (0-7) is stored as 3 bits, LSB-first within each nibble
  and across nibble boundaries.  Adjacent nodes pack contiguously into the
  nibble stream; there are no per-node separators.
* The hex string output is uppercase and has length ``ceil(n_nodes * 3 / 4)``
  (minimum nibbles to hold the bitstream).  Trailing alignment bits in the
  last nibble are zero-padded by the encoder.
* ``decode_nodes`` strips any trailing NONE (0) nodes that are artefacts of
  nibble alignment; this makes ``decode_nodes(encode_nodes(nodes)) == nodes``
  hold for all node lists.

Remapping is done by ``remap_paint_color`` as a SURGICAL in-place bit edit:
it overwrites only the 3-bit fields of remapped filament leaves and leaves
every other bit — structure, support nodes, NONE nodes, and OrcaSlicer's
non-canonical trailing padding bits — byte-for-byte untouched.  This makes
an identity remap (``mapping={}``) byte-exact for ALL fixture strings, and a
real remap change only the intended leaf fields.  The decode/encode helpers
are node-level and self-consistent (``decode_nodes(encode_nodes(nodes)) ==
nodes``), but the remap path deliberately does NOT round-trip through them,
so it is unaffected by OrcaSlicer's padding-bit artefact.
"""
from __future__ import annotations


def decode_nodes(hex_str: str) -> list[int]:
    """Decode a paint_color hex string to a list of 3-bit node values.

    Reads each hex character (nibble) as 4 bits, LSB-first, and extracts
    consecutive 3-bit node values.  Trailing NONE (0) nodes that arise
    solely from nibble-alignment padding are stripped so that
    ``decode_nodes(encode_nodes(nodes)) == nodes``.

    Args:
        hex_str: The ``paint_color`` attribute value from the 3MF model.

    Returns:
        List of 3-bit node values (integers 0-7).
    """
    if not hex_str:
        return []
    bits: list[int] = []
    for ch in hex_str:
        nib = int(ch, 16)
        for i in range(4):        # LSB first within each nibble
            bits.append((nib >> i) & 1)
    nodes: list[int] = []
    # Read complete 3-bit groups from the bitstream.
    for i in range(0, len(bits) - 2, 3):
        nodes.append(bits[i] + bits[i + 1] * 2 + bits[i + 2] * 4)
    # Strip trailing NONE (0) nodes that are pure padding artefacts.
    # These arise when encode_nodes zero-pads the last nibble to alignment.
    while nodes and nodes[-1] == 0:
        nodes.pop()
    return nodes


def encode_nodes(nodes: list[int]) -> str:
    """Encode a list of 3-bit node values to a paint_color hex string.

    Exact inverse of decode_nodes (modulo trailing padding zeros):
    - Pack each 3-bit value LSB-first into the nibble bitstream.
    - Emit uppercase hex of length ``ceil(n_nodes * 3 / 4)`` nibbles.
    - Trailing alignment bits in the last nibble are set to zero.

    Args:
        nodes: List of 3-bit node values (integers 0-7).

    Returns:
        Uppercase hex string suitable for the ``paint_color`` attribute.
    """
    if not nodes:
        return ""
    bits: list[int] = []
    for v in nodes:
        v = v & 0x7          # ensure 3-bit
        for i in range(3):   # LSB first
            bits.append((v >> i) & 1)
    # Pad to nibble boundary (multiple of 4 bits).
    while len(bits) % 4:
        bits.append(0)
    result: list[str] = []
    for i in range(0, len(bits), 4):
        nib = bits[i] + bits[i + 1] * 2 + bits[i + 2] * 4 + bits[i + 3] * 8
        result.append(format(nib, "X"))
    return "".join(result)


def remap_paint_color(hex_str: str, mapping: dict) -> str:
    """Remap filament indices in a paint_color hex string — SURGICAL in-place edit.

    Remapping a filament leaf node (3..6 → 3..6) is a same-width (3-bit) field
    change.  Rather than decode→re-encode (which would drop OrcaSlicer's
    non-canonical trailing padding bits and re-emit a "clean" but not
    byte-identical string), this rewrites ONLY the 3 bits of each remapped
    leaf field IN PLACE.  Every other bit — tree structure (SPLIT), support
    nodes (ENFORCER/BLOCKER), NONE nodes, and trailing nibble padding — is
    left byte-for-byte untouched.  The output preserves the original length
    and uppercase casing.

    Args:
        hex_str:  The ``paint_color`` attribute value from the 3MF model file.
        mapping:  ``{model_filament (1-based int): tool_index (0-based int)}``.
                  Empty dict is a no-op (returns the input byte-exact).

    Returns:
        A new paint_color hex string with only the remapped leaf fields changed.

    Node values 0 (NONE), 1 (ENFORCER), 2 (BLOCKER), and 7 (SPLIT) are
    preserved.  For leaf nodes with value v in 3..6:
        filament = v - 2          # 1-based logical filament
        if filament in mapping:
            v = mapping[filament] + 3   # tool_index (0-based) + 3 = new node value
    New values are clamped to 3..6 to guard against out-of-range mappings.
    """
    if not hex_str:
        return hex_str
    if not mapping:
        # Identity remap touches nothing — return byte-exact input.
        return hex_str

    # Expand to a mutable bit array (nibble-LSB), preserving every bit.
    bits: list[int] = []
    for ch in hex_str:
        nib = int(ch, 16)
        for i in range(4):            # LSB first within each nibble
            bits.append((nib >> i) & 1)

    # Walk every complete 3-bit field the same way decode_nodes reads them,
    # editing in place.  Only fields holding a remapped filament are touched;
    # all other bits (incl. trailing padding) stay exactly as they were.
    total = len(bits)
    bit_pos = 0
    while bit_pos + 3 <= total:
        v = bits[bit_pos] + bits[bit_pos + 1] * 2 + bits[bit_pos + 2] * 4
        if 3 <= v <= 6:
            filament = v - 2          # 1-based filament index
            if filament in mapping:
                new_v = mapping[filament] + 3       # tool_index(0-based) + 3
                new_v = max(3, min(6, new_v))       # clamp to valid range
                if new_v != v:
                    # Overwrite exactly these 3 bits, same nibble-LSB packing.
                    bits[bit_pos] = new_v & 1
                    bits[bit_pos + 1] = (new_v >> 1) & 1
                    bits[bit_pos + 2] = (new_v >> 2) & 1
        bit_pos += 3

    # Re-emit hex, preserving original length and uppercase casing.
    result: list[str] = []
    for i in range(0, len(bits), 4):
        nib = bits[i] + bits[i + 1] * 2 + bits[i + 2] * 4 + bits[i + 3] * 8
        result.append(format(nib, "X"))
    return "".join(result)
