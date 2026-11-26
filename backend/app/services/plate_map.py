"""Plate map snapshot: a static SVG of a plate layout drawn from neutral geometry (BIZ-405).

The interactive view is `frontend/src/components/plateMap/` (layout.ts + PlateMap.tsx). Both implement the layout spec in
docs/superpowers/plans/2026-11-26-biz-405-plate-map.md § Layout spec and both are checked against the hand-computed
corpus `contracts/plate-map-cases.json` (tests/services/test_plate_map.py and the frontend tests), so the snapshot and
the view cannot drift. Change the spec, the corpus and both implementations together.

Input is data only: a bed, objects with unique labels and footprints in bed millimetres, and caller-supplied states.
No key outside that schema is accepted, so a caller can never hand this module SVG or markup to embed; every label
and state name is escaped, and a colour that is not #rgb / #rrggbb is replaced by a neutral fill.

Bed +X points right, +Y points away from the operator; `origin` is where (0,0) sits, in mm from the bed's front-left
corner. View coordinates are millimetres with the front at the bottom.
"""
from __future__ import annotations

import re
from typing import Any
from xml.sax.saxutils import escape

PATTERNS = ("solid", "diagonal", "dots", "cross", "horizontal", "checker")
MARGIN_RATIO = 0.04
MIN_HIT_RATIO = 0.06
LABEL_MAX = 40
NEUTRAL_FILL = "#8a8f98"
_EPS = 1e-6
MAX_MM = 100000  # every number must be within ±MAX_MM (no float overflow, no exponent formatting)
# XML 1.0 Char: text outside it makes the snapshot unparseable (control chars) or unencodable (lone surrogates).
_XML_TEXT = re.compile("[\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]*")
_ID_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")
_NO_GEOMETRY_SCALE = 200.0  # legend sizing when there is no bed to scale from

# Snapshot colours (a standalone file has no app stylesheet).
_BG = "#ffffff"
_BED_FILL = "#f4f5f7"
_BED_STROKE = "#9aa0a8"
_INK = "#1f2328"
_MUTED = "#5f6670"
_WARN = "#c27803"


# ── validation (messages are shared with layout.ts; the corpus checks substrings) ─────────────────────────────────

def _is_num(v: Any) -> bool:
    # abs() before any float conversion: a huge int would raise OverflowError in math.isfinite; NaN fails the <=.
    return isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) <= MAX_MM


def _unknown_key(o: dict, allowed: tuple[str, ...]) -> str | None:
    return next((k for k in o if k not in allowed), None)


def _polygon_area(pts: list[list[float]]) -> float:
    a = 0.0
    for i, (x1, y1) in enumerate(pts):
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return a / 2


def _validate_footprint(fp: Any) -> str | None:
    if fp is None:
        return None
    if not isinstance(fp, dict):
        return "footprint: needs polygon or box"
    extra = _unknown_key(fp, ("polygon", "box"))
    if extra:
        return f"unknown key {extra}"
    if ("polygon" in fp) == ("box" in fp):
        return "footprint: needs polygon or box"
    if "box" in fp:
        b = fp["box"]
        if not (isinstance(b, list) and len(b) == 4 and all(_is_num(v) for v in b) and b[0] < b[2] and b[1] < b[3]):
            return "footprint: box must be [x0, y0, x1, y1] with x0 < x1 and y0 < y1"
        return None
    poly = fp["polygon"]
    if not isinstance(poly, list) or len(poly) < 3:
        return "footprint: polygon needs at least 3 points"
    if not all(isinstance(p, list) and len(p) == 2 and all(_is_num(v) for v in p) for p in poly):
        return "footprint: coordinates must be finite numbers"
    if abs(_polygon_area(poly)) < _EPS:
        return "footprint: zero area"
    return None


def validate_plate(raw: Any) -> str | None:
    """None when the plate is usable, else the first problem found."""
    if not isinstance(raw, dict):
        return "plate: must be an object"
    extra = _unknown_key(raw, ("bed", "states", "objects"))
    if extra:
        return f"unknown key {extra}"
    bed = raw.get("bed")
    if bed is not None:
        if not isinstance(bed, dict):
            return "bed: width and depth must be positive numbers"
        extra = _unknown_key(bed, ("width", "depth", "origin"))
        if extra:
            return f"unknown key {extra}"
        if not (_is_num(bed.get("width")) and _is_num(bed.get("depth")) and bed["width"] > 0 and bed["depth"] > 0):
            return "bed: width and depth must be positive numbers"
        if "origin" in bed:
            origin = bed["origin"]
            if not isinstance(origin, dict):
                return "bed: origin must be finite numbers"
            extra = _unknown_key(origin, ("x", "y"))
            if extra:
                return f"unknown key {extra}"
            if not (_is_num(origin.get("x")) and _is_num(origin.get("y"))):
                return "bed: origin must be finite numbers"
    states = raw.get("states")
    if not isinstance(states, list) or not 1 <= len(states) <= len(PATTERNS):
        return "states: 1 to 6 states required"
    ids: set[str] = set()
    for s in states:
        if not isinstance(s, dict):
            return "states: id, name and color must be strings"
        extra = _unknown_key(s, ("id", "name", "color"))
        if extra:
            return f"unknown key {extra}"
        if not all(isinstance(s.get(k), str) for k in ("id", "name", "color")):
            return "states: id, name and color must be strings"
        if not _XML_TEXT.fullmatch(s["name"]):
            return "states: name has invalid characters"
        if s["id"] in ids:
            return "states: duplicate id"
        ids.add(s["id"])
    objects = raw.get("objects")
    if not isinstance(objects, list):
        return "objects: must be a list"
    labels: set[str] = set()
    for o in objects:
        if not isinstance(o, dict):
            return "objects: must be a list"
        extra = _unknown_key(o, ("label", "state", "selectable", "footprint"))
        if extra:
            return f"unknown key {extra}"
        label = o.get("label")
        if not isinstance(label, str) or label == "":
            return "objects: label must be a non-empty string"
        if not _XML_TEXT.fullmatch(label):
            return "objects: label has invalid characters"
        if label in labels:
            return "objects: duplicate label"
        labels.add(label)
        if not isinstance(o.get("state"), str) or o["state"] not in ids:
            return "objects: unknown state"
        if not isinstance(o.get("selectable"), bool):
            return "objects: selectable must be a boolean"
        problem = _validate_footprint(o.get("footprint"))
        if problem:
            return problem
    return None


# ── layout ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def safe_color(color: str) -> str | None:
    return color if _HEX.fullmatch(color) else None


def _footprint_points(fp: dict) -> list[list[float]]:
    if "box" in fp:
        x0, y0, x1, y1 = fp["box"]
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return [[x, y] for x, y in fp["polygon"]]


def _bbox(pts: list[list[float]]) -> list[float]:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


def _anchor(pts: list[list[float]]) -> list[float]:
    """Area centroid's y; on that horizontal line, the midpoint of the widest span inside the polygon."""
    a = _polygon_area(pts)
    cx = cy = 0.0
    for i, (x1, y1) in enumerate(pts):
        x2, y2 = pts[(i + 1) % len(pts)]
        f = x1 * y2 - x2 * y1
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    cx /= 6 * a
    cy /= 6 * a
    xs = []
    for i, (ax, ay) in enumerate(pts):
        bx, by = pts[(i + 1) % len(pts)]
        if ay <= cy < by or by <= cy < ay:
            xs.append(ax + (cy - ay) * (bx - ax) / (by - ay))
    xs.sort()
    best, width = [cx, cy], -1.0
    for i in range(0, len(xs) - 1, 2):
        if xs[i + 1] - xs[i] > width:
            width = xs[i + 1] - xs[i]
            best = [(xs[i] + xs[i + 1]) / 2, cy]
    return best


def compute_layout(plate: dict) -> dict:
    """The shared layout for a *validated* plate (see validate_plate)."""
    bed = plate.get("bed")
    origin = (bed or {}).get("origin") or {"x": 0, "y": 0}
    ox, oy = origin["x"], origin["y"]
    max_y = (bed["depth"] if bed else 0) - oy

    def to_view(p):
        return [p[0] + ox, max_y - p[1]]

    state_index = {s["id"]: i for i, s in enumerate(plate["states"])}
    has_geometry = bool(bed) and any(o.get("footprint") for o in plate["objects"])
    m_dim = max(bed["width"], bed["depth"]) if bed else 0.0
    min_hit = MIN_HIT_RATIO * m_dim

    objects = []
    for index, o in enumerate(plate["objects"]):
        si = state_index[o["state"]]
        state = plate["states"][si]
        entry = {"label": o["label"], "index": index, "state": state, "pattern": PATTERNS[si],
                 "fill": safe_color(state["color"]), "selectable": o["selectable"],
                 "points": None, "hit": None, "anchor": None, "off_bed": False, "area": 0.0}
        if has_geometry and o.get("footprint"):
            pts = [to_view(p) for p in _footprint_points(o["footprint"])]
            bx, by, bw, bh = _bbox(pts)
            hit = None
            if bw < min_hit or bh < min_hit:
                w, h = max(bw, min_hit), max(bh, min_hit)
                hit = [bx + bw / 2 - w / 2, by + bh / 2 - h / 2, w, h]
            off = any(x < -_EPS or x > bed["width"] + _EPS or y < -_EPS or y > bed["depth"] + _EPS for x, y in pts)
            entry.update(points=pts, hit=hit, anchor=_anchor(pts), off_bed=off, area=abs(_polygon_area(pts)))
        objects.append(entry)

    drawn = sorted((o for o in objects if o["points"]), key=lambda o: (-o["area"], o["index"]))
    if not has_geometry:
        return {"geometry": None, "objects": objects, "draw_order": [], "drawn": []}
    x0, y0, x1, y1 = 0.0, 0.0, float(bed["width"]), float(bed["depth"])
    for o in drawn:
        bx, by, bw, bh = _bbox(o["points"])
        x0, y0, x1, y1 = min(x0, bx), min(y0, by), max(x1, bx + bw), max(y1, by + bh)
    m = MARGIN_RATIO * m_dim
    geometry = {"view_box": [x0 - m, y0 - m, x1 - x0 + 2 * m, y1 - y0 + 2 * m],
                "bed": [0, 0, bed["width"], bed["depth"]], "scale": m_dim}
    return {"geometry": geometry, "objects": objects, "draw_order": [o["label"] for o in drawn], "drawn": drawn}


# ── rendering ───────────────────────────────────────────────────────────────────────────────────────────────────────

def fmt(n: float) -> str:
    """At most 3 decimals, trailing zeros dropped, never "-0" (matches layout.ts formatNum)."""
    s = f"{n:.3f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def truncate_label(label: str, limit: int = LABEL_MAX) -> str:
    return label[:limit] + "…" if len(label) > limit else label


def _attr(v: str) -> str:
    return escape(v, {'"': "&quot;"})


def _points(pts: list[list[float]]) -> str:
    return " ".join(f"{fmt(x)},{fmt(y)}" for x, y in pts)


def _pattern_shapes(name: str, t: float) -> str:
    """Mirrors patternShapes() in PlateMap.tsx."""
    w = t * 0.16
    line = f'stroke="{_INK}" stroke-opacity="0.45" fill="none"'
    dot = f'fill="{_INK}" fill-opacity="0.45"'
    if name == "diagonal":
        return (f'<path {line} stroke-width="{fmt(w)}" d="M0,{fmt(t)} L{fmt(t)},0 M{fmt(-t / 4)},{fmt(t / 4)} '
                f'L{fmt(t / 4)},{fmt(-t / 4)} M{fmt(3 * t / 4)},{fmt(5 * t / 4)} L{fmt(5 * t / 4)},{fmt(3 * t / 4)}"/>')
    if name == "dots":
        return f'<circle {dot} cx="{fmt(t / 2)}" cy="{fmt(t / 2)}" r="{fmt(t * 0.2)}"/>'
    if name == "cross":
        return f'<path {line} stroke-width="{fmt(w)}" d="M0,0 L{fmt(t)},{fmt(t)} M{fmt(t)},0 L0,{fmt(t)}"/>'
    if name == "horizontal":
        return f'<path {line} stroke-width="{fmt(w * 1.5)}" d="M0,{fmt(t / 2)} H{fmt(t)}"/>'
    if name == "checker":
        h = fmt(t / 2)
        return f'<rect {dot} width="{h}" height="{h}"/><rect {dot} x="{h}" y="{h}" width="{h}" height="{h}"/>'
    return ""


def _pattern_defs(tile: float, prefix: str) -> str:
    return "<defs>" + "".join(
        f'<pattern id="{prefix}-{p}" patternUnits="userSpaceOnUse" width="{fmt(tile)}" height="{fmt(tile)}">'
        f"{_pattern_shapes(p, tile)}</pattern>" for p in PATTERNS if p != "solid") + "</defs>"


def _key_line(o: dict, has_map: bool) -> str:
    flags = []
    if o["off_bed"]:
        flags.append("off bed")
    if not o["selectable"]:
        flags.append("not selectable")
    if o["points"] is None and has_map:  # list-only plate: nothing is on a map, as in the view
        flags.append("not on map")
    text = f'{o["index"] + 1}. {truncate_label(o["label"])}'
    return f"{text} ({', '.join(flags)})" if flags else text


def render_snapshot_svg(plate: dict, width_px: int = 800, id_prefix: str = "pm") -> str:
    """A standalone SVG: bed, objects (state fill + pattern), number markers, a state legend and a numbered key.

    Raises ValueError for anything validate_plate rejects (including any key outside the schema). `id_prefix` names the
    pattern ids; give each snapshot its own when several are inlined into one HTML page (pattern tiles are per bed)."""
    if not _ID_PREFIX.fullmatch(id_prefix):
        raise ValueError("plate map: id_prefix must be a letter then letters, digits, - or _")
    problem = validate_plate(plate)
    if problem:
        raise ValueError(f"plate map: {problem}")
    layout = compute_layout(plate)
    geo = layout["geometry"]
    s = geo["scale"] if geo else _NO_GEOMETRY_SCALE
    vx, vy, vw, vh = geo["view_box"] if geo else [0.0, 0.0, s, 0.0]
    row = 0.05 * s
    font = 0.032 * s
    left = vx + MARGIN_RATIO * s
    rows = len(plate["states"]) + len(layout["objects"])
    legend_top = vy + vh + row * 0.5
    total_h = vh + row * (rows + 1.5)
    stroke = 0.004 * s

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{fmt(vx)} {fmt(vy)} {fmt(vw)} {fmt(total_h)}" '
           f'width="{width_px}" height="{round(width_px * total_h / vw)}" preserveAspectRatio="xMidYMid meet" '
           f'font-family="IBM Plex Sans, Helvetica, Arial, sans-serif">',
           f'<rect class="plate-map-background" x="{fmt(vx)}" y="{fmt(vy)}" width="{fmt(vw)}" height="{fmt(total_h)}" fill="{_BG}"/>',
           _pattern_defs(s * 0.025, id_prefix)]

    if geo:
        bw, bd = geo["bed"][2], geo["bed"][3]
        out.append(f'<g class="plate-map-plate" data-view-box="{" ".join(fmt(v) for v in geo["view_box"])}">')
        out.append(f'<rect class="plate-map-bed" x="0" y="0" width="{fmt(bw)}" height="{fmt(bd)}" fill="{_BED_FILL}" '
                   f'stroke="{_BED_STROKE}" stroke-width="{fmt(stroke)}"/>')
        out.append(f'<text class="plate-map-front" x="{fmt(bw / 2)}" y="{fmt(bd + s * 0.03)}" font-size="{fmt(s * 0.024)}" '
                   f'text-anchor="middle" dominant-baseline="middle" fill="{_MUTED}">Front</text>')
        for o in layout["drawn"]:
            pts = _points(o["points"])
            style = f'stroke="{_WARN if o["off_bed"] else _INK}" stroke-width="{fmt(stroke * (2 if o["off_bed"] else 1))}"'
            if not o["selectable"]:
                style += f' fill-opacity="0.4" stroke-dasharray="{fmt(stroke * 4)} {fmt(stroke * 3)}"'
            out.append(f'<g><title>{escape(o["label"])}</title>'
                       f'<polygon class="plate-map-object" points="{pts}" fill="{o["fill"] or NEUTRAL_FILL}" {style} '
                       f'data-label="{_attr(o["label"])}" data-pattern="{o["pattern"]}" '
                       f'data-off-bed="{str(o["off_bed"]).lower()}" data-selectable="{str(o["selectable"]).lower()}"/>')
            if o["pattern"] != "solid":
                out.append(f'<polygon class="plate-map-pattern" points="{pts}" fill="url(#{id_prefix}-{o["pattern"]})"/>')
            if o["hit"]:
                hx, hy, hw, hh = o["hit"]
                out.append(f'<rect class="plate-map-hit" x="{fmt(hx)}" y="{fmt(hy)}" width="{fmt(hw)}" height="{fmt(hh)}" '
                           f'fill="none" stroke="{_MUTED}" stroke-width="{fmt(stroke)}" '
                           f'stroke-dasharray="{fmt(stroke * 3)} {fmt(stroke * 2)}"/>')
            out.append("</g>")
        for o in layout["objects"]:
            if o["anchor"]:
                ax, ay = o["anchor"]
                out.append(f'<text class="plate-map-marker" x="{fmt(ax)}" y="{fmt(ay)}" font-size="{fmt(s * 0.035)}" '
                           f'font-weight="600" text-anchor="middle" dominant-baseline="central" fill="{_INK}" '
                           f'stroke="{_BG}" stroke-width="{fmt(s * 0.008)}" paint-order="stroke">{o["index"] + 1}</text>')
        out.append("</g>")

    sw, sh = row * 1.2, row * 0.7
    out.append('<g class="plate-map-legend">')
    for i, st in enumerate(plate["states"]):
        y = legend_top + i * row
        pattern = PATTERNS[i]
        out.append(f'<rect x="{fmt(left)}" y="{fmt(y)}" width="{fmt(sw)}" height="{fmt(sh)}" '
                   f'fill="{safe_color(st["color"]) or NEUTRAL_FILL}" stroke="{_INK}" stroke-width="{fmt(stroke)}" '
                   f'data-pattern="{pattern}"/>')
        if pattern != "solid":
            out.append(f'<rect x="{fmt(left)}" y="{fmt(y)}" width="{fmt(sw)}" height="{fmt(sh)}" fill="url(#{id_prefix}-{pattern})"/>')
        out.append(f'<text x="{fmt(left + sw + row * 0.3)}" y="{fmt(y + sh / 2)}" font-size="{fmt(font)}" '
                   f'dominant-baseline="central" fill="{_INK}">{escape(st["name"])}</text>')
    out.append("</g>")

    out.append('<g class="plate-map-key">')
    key_top = legend_top + len(plate["states"]) * row + row * 0.3
    for i, o in enumerate(layout["objects"]):
        out.append(f'<text x="{fmt(left)}" y="{fmt(key_top + i * row + sh / 2)}" font-size="{fmt(font)}" '
                   f'dominant-baseline="central" fill="{_INK}">{escape(_key_line(o, geo is not None))}</text>')
    out.append("</g></svg>")
    return "".join(out)
