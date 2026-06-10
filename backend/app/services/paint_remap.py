"""paint_remap.py — OrcaSlicer TriangleSelector paint_color codec + filament remap.

OrcaSlicer encodes each triangle's subdivision tree as a hex string in the
``paint_color`` attribute of ``3D/Objects/*.model``.  The bitstream layout is:

**Bit ↔ hex mapping (authoritative from libslic3r/TriangleSelector.cpp):**
  Nibbles are read/written **right-to-left** (the last hex character is the
  start of the bitstream).  Within each nibble bits are LSB-first:
  bit i of nibble n = ``(n >> i) & 1``.

  This means the tree root occupies the LSB of the *last* nibble, and the hex
  string grows leftward as the tree grows.  When serializing, bits are packed
  into nibbles LSB-first, the final (leftmost) nibble is zero-padded, and the
  nibble sequence is reversed before converting to a hex string.

**Tree encoding — one node at a time:**
  1. Read 2 bits ``split_sides`` (LSB-first: ``b0 | (b1<<1)``).
  2. If ``split_sides == 0`` → **LEAF**: read 2 bits ``code``.
     - ``code == 3`` (both bits set): read a 4-bit nibble ``n``; ``state = n + 3``.
     - Else ``state = code`` (0, 1, or 2).
  3. If ``split_sides ∈ {1,2,3}`` → **SPLIT**: read 2 bits ``special_side``,
     then read ``split_sides + 1`` child nodes **in reverse order**
     (the serializer wrote child[split_sides], …, child[0]); recurse.

**State values:**
  0 = NONE (unpainted), 1 = ENFORCER, 2 = BLOCKER,
  state s ≥ 3 → filament (s − 2) in 1-based numbering
  (filament f → state f + 2; tool_index t (0-based) → state t + 3).

**Remap:**
  For every LEAF with state ≥ 3: if ``filament = state − 2`` is in *mapping*,
  replace ``state = mapping[filament] + 3`` (mapping value is 0-based
  tool_index).  NONE / ENFORCER / BLOCKER / SPLIT nodes are preserved exactly.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _hex_to_bitstream(hex_str: str) -> list[int]:
    """Unpack hex string to a flat bit list (RTL nibble order, LSB-first per nibble)."""
    bits: list[int] = []
    for ch in reversed(hex_str):
        nib = int(ch, 16)
        for i in range(4):
            bits.append((nib >> i) & 1)
    return bits


def _bitstream_to_hex(bits: list[int]) -> str:
    """Pack a flat bit list back to a hex string (RTL nibble order, LSB-first per nibble)."""
    bits = list(bits)
    while len(bits) % 4:
        bits.append(0)
    nibbles: list[str] = []
    for i in range(0, len(bits), 4):
        nib = bits[i] | (bits[i+1] << 1) | (bits[i+2] << 2) | (bits[i+3] << 3)
        nibbles.append(format(nib, "X"))
    return "".join(reversed(nibbles))


def _parse_node(bits: list[int], pos: int) -> tuple[Any, int]:
    """Parse one tree node from *bits* starting at *pos*.

    Returns ``(node, new_pos)`` or ``(None, pos)`` on underflow.

    Node representation:
      LEAF  → ``('L', state)``
      SPLIT → ``('S', split_sides, special_side, [child0, child1, …])``
    """
    if pos + 2 > len(bits):
        return None, pos
    split_sides = bits[pos] | (bits[pos + 1] << 1)
    pos += 2
    if split_sides == 0:  # LEAF
        if pos + 2 > len(bits):
            return None, pos
        code = bits[pos] | (bits[pos + 1] << 1)
        pos += 2
        if code == 3:  # extended state: read 4-bit n, state = n + 3
            if pos + 4 > len(bits):
                return None, pos
            n = (bits[pos]
                 | (bits[pos + 1] << 1)
                 | (bits[pos + 2] << 2)
                 | (bits[pos + 3] << 3))
            pos += 4
            return ("L", n + 3), pos
        return ("L", code), pos
    else:  # SPLIT
        if pos + 2 > len(bits):
            return None, pos
        special_side = bits[pos] | (bits[pos + 1] << 1)
        pos += 2
        children: list[Any] = []
        # Serializer wrote children in *reverse* order; read them in that order
        # then reverse to restore child[0], child[1], … ordering.
        for _ in range(split_sides + 1):
            child, pos = _parse_node(bits, pos)
            if child is None:
                return None, pos
            children.append(child)
        children.reverse()
        return ("S", split_sides, special_side, children), pos


def _serialize_node(node: Any, bits: list[int]) -> None:
    """Append the serialized form of *node* to *bits* (exact inverse of _parse_node)."""
    if node[0] == "L":
        state: int = node[1]
        bits.append(0); bits.append(0)           # split_sides = 0
        if state <= 2:
            bits.append(state & 1)
            bits.append((state >> 1) & 1)
        else:                                      # code = 3 (extended)
            bits.append(1); bits.append(1)
            n = state - 3
            for i in range(4):
                bits.append((n >> i) & 1)
    else:  # SPLIT
        _, split_sides, special_side, children = node
        bits.append(split_sides & 1)
        bits.append((split_sides >> 1) & 1)
        bits.append(special_side & 1)
        bits.append((special_side >> 1) & 1)
        # Write children in *reverse* order (as the original serializer does)
        for child in reversed(children):
            _serialize_node(child, bits)


def _remap_node(node: Any, mapping: dict) -> Any:
    """Return a (possibly new) node with filament states remapped.

    *mapping* maps 1-based filament numbers to 0-based tool indices.
    NONE / ENFORCER / BLOCKER leaves and SPLIT nodes are returned unchanged.
    """
    if node[0] == "L":
        state: int = node[1]
        if state >= 3:                             # filament leaf
            filament = state - 2                   # 1-based filament
            if filament in mapping:
                new_state = mapping[filament] + 3  # tool_index (0-based) + 3
                if new_state != state:
                    return ("L", new_state)
        return node
    else:  # SPLIT — recurse into children
        _, split_sides, special_side, children = node
        new_children = [_remap_node(c, mapping) for c in children]
        # Only allocate a new tuple if something actually changed
        if all(nc is oc for nc, oc in zip(new_children, children)):
            return node
        return ("S", split_sides, special_side, new_children)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def decode_nodes(hex_str: str) -> Any:
    """Decode a paint_color hex string to a tree node structure.

    Returns the root node of the parsed TriangleSelector tree:
      ``('L', state)`` for a LEAF (state: 0=NONE, 1=ENFORCER, 2=BLOCKER, ≥3=filament)
      ``('S', split_sides, special_side, [children])`` for a SPLIT node.

    Args:
        hex_str: The ``paint_color`` attribute value from the 3MF model.

    Returns:
        Root node of the tree, or ``('L', 0)`` for an empty string.
    """
    if not hex_str:
        return ("L", 0)
    bits = _hex_to_bitstream(hex_str)
    node, _ = _parse_node(bits, 0)
    if node is None:
        raise ValueError(f"Failed to parse paint_color: {hex_str!r}")
    return node


def encode_nodes(node: Any) -> str:
    """Encode a tree node structure back to a paint_color hex string.

    Exact inverse of ``decode_nodes``: ``encode_nodes(decode_nodes(s)) == s``
    for all valid OrcaSlicer paint_color strings.

    Args:
        node: Root node as returned by ``decode_nodes``.

    Returns:
        Uppercase hex string suitable for the ``paint_color`` attribute.
    """
    if node is None:
        return ""
    bits: list[int] = []
    _serialize_node(node, bits)
    return _bitstream_to_hex(bits)


def remap_paint_color(hex_str: str, mapping: dict) -> str:
    """Remap filament indices in a paint_color hex string.

    Deserializes the TriangleSelector tree, swaps every filament leaf state
    according to *mapping*, and re-serializes.  The result is byte-exact when
    *mapping* is empty (identity remap).

    Args:
        hex_str:  The ``paint_color`` attribute value from the 3MF model file.
        mapping:  ``{filament (1-based int): tool_index (0-based int)}``.
                  Empty dict is a no-op (returns the input byte-exact).

    Returns:
        A new paint_color hex string with remapped filament leaves.

    State semantics:
        0 (NONE), 1 (ENFORCER), 2 (BLOCKER) — preserved unchanged.
        state s ≥ 3 → filament = s − 2 (1-based); if in mapping →
        new_state = mapping[filament] + 3.
    """
    if not hex_str:
        return hex_str
    if not mapping:
        return hex_str
    node = decode_nodes(hex_str)
    remapped = _remap_node(node, mapping)
    return encode_nodes(remapped)
