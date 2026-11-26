"""Plate-map snapshot renderer (BIZ-405) against the shared corpus.

`contracts/plate-map-cases.json` is also run by frontend/src/components/plateMap/*.test.ts(x): both the interactive view
and this snapshot must reproduce the same hand-computed layout numbers, so the two renderers cannot drift.
"""
import ast
import copy
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.services import plate_map
from app.services.plate_map import PATTERNS, compute_layout, render_snapshot_svg, truncate_label, validate_plate

ROOT = Path(__file__).resolve().parents[3]
CORPUS = json.loads((ROOT / "contracts" / "plate-map-cases.json").read_text(encoding="utf-8"))
CASES = {c["name"]: c for c in CORPUS["cases"]}
SVG = "{http://www.w3.org/2000/svg}"

REQUIRED = ["square-front-left", "centre-origin", "back-right-origin", "scale-wide-large", "scale-small-portrait",
            "tiny-object", "overlap", "concave-l", "rotated-rect", "off-bed", "labels", "six-states", "mixed-selectable",
            "missing-footprint", "no-geometry"]


def close(actual, expected):
    assert len(actual) == len(expected), (actual, expected)
    for a, e in zip(actual, expected):
        if isinstance(e, (list, tuple)):
            close(a, e)
        else:
            assert math.isclose(a, e, abs_tol=1e-3), (actual, expected)


def parse_points(s):
    return [[float(v) for v in pair.split(",")] for pair in s.split()]


def snapshot(plate):
    return ET.fromstring(render_snapshot_svg(copy.deepcopy(plate)))


def object_polygons(root):
    return [p for p in root.iter(f"{SVG}polygon") if "plate-map-object" in p.get("class", "").split()]


def test_corpus_covers_every_tricky_part():
    for name in REQUIRED:
        assert name in CASES


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda c: c["name"])
def test_corpus_cases_are_valid(case):
    assert validate_plate(copy.deepcopy(case["plate"])) is None


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda c: c["name"])
def test_corpus_layout(case):
    layout = compute_layout(copy.deepcopy(case["plate"]))
    e = case["expect"]
    if "list_only" in e:
        assert (layout["geometry"] is None) is e["list_only"]
    if "view_box" in e:
        close(layout["geometry"]["view_box"], e["view_box"])
    if "draw_order" in e:
        assert layout["draw_order"] == e["draw_order"]
    by_label = {o["label"]: o for o in layout["objects"]}
    for label, want in e.get("objects", {}).items():
        got = by_label[label]
        if "points" in want:
            close(got["points"], want["points"])
        if "off_bed" in want:
            assert got["off_bed"] is want["off_bed"], label
        if "hit" in want:
            if want["hit"] is None:
                assert got["hit"] is None
            else:
                close(got["hit"], want["hit"])
        if "anchor" in want:
            close(got["anchor"], want["anchor"])
        if "pattern" in want:
            assert got["pattern"] == want["pattern"]
        if "fill" in want:
            assert got["fill"] == want["fill"]
        if "on_map" in want:
            assert (got["points"] is not None) is want["on_map"]
    for label, want in e.get("truncated", {}).items():
        assert truncate_label(label) == want


@pytest.mark.parametrize("case", CORPUS["invalid"], ids=lambda c: c["name"])
def test_corpus_invalid(case):
    message = validate_plate(copy.deepcopy(case["plate"]))
    assert message is not None and case["message"] in message
    with pytest.raises(ValueError, match="plate map"):
        render_snapshot_svg(copy.deepcopy(case["plate"]))


DRAWN = [c for c in CORPUS["cases"] if {"view_box", "objects", "draw_order"} & set(c["expect"])]


@pytest.mark.parametrize("case", DRAWN, ids=lambda c: c["name"])
def test_snapshot_polygons_match_corpus(case):
    """The rendered markup, not just the layout dict, carries the shared numbers."""
    root = snapshot(case["plate"])
    e = case["expect"]
    polys = object_polygons(root)
    if "view_box" in e:
        plate_group = next(g for g in root.iter(f"{SVG}g") if g.get("class") == "plate-map-plate")
        close([float(v) for v in plate_group.get("data-view-box").split()], e["view_box"])
        x, y, w, h = (float(v) for v in root.get("viewBox").split())
        close([x, y, w], e["view_box"][:3])
        assert h > e["view_box"][3]  # the legend sits below the plate
        assert root.get("preserveAspectRatio") == "xMidYMid meet"
    if "draw_order" in e:
        assert [p.get("data-label") for p in polys] == e["draw_order"]
    by_label = {p.get("data-label"): p for p in polys}
    for label, want in e.get("objects", {}).items():
        if want.get("on_map") is False:
            assert label not in by_label
            continue
        poly = by_label[label]
        if "points" in want:
            close(parse_points(poly.get("points")), want["points"])
        if "off_bed" in want:
            assert poly.get("data-off-bed") == str(want["off_bed"]).lower()
        if "pattern" in want:
            assert poly.get("data-pattern") == want["pattern"]
        if "fill" in want:
            assert poly.get("fill") == (want["fill"] or plate_map.NEUTRAL_FILL)
        if want.get("hit"):
            rings = [r for r in root.iter(f"{SVG}rect") if r.get("class") == "plate-map-hit"]
            close([float(rings[0].get(a)) for a in ("x", "y", "width", "height")], want["hit"])


def _key_texts(root):
    key = next(g for g in root.iter(f"{SVG}g") if g.get("class") == "plate-map-key")
    return ["".join(t.itertext()) for t in key.iter(f"{SVG}text")]


def test_snapshot_escapes_labels():
    plate = CASES["labels"]["plate"]
    raw = render_snapshot_svg(copy.deepcopy(plate))
    root = ET.fromstring(raw)  # unescaped markup would not parse, or would add elements
    tags = {el.tag.removeprefix(SVG) for el in root.iter()}
    assert tags <= {"svg", "defs", "pattern", "path", "circle", "rect", "g", "polygon", "text", "title"}
    assert "<script" not in raw and "<img" not in raw
    titles = ["".join(t.itertext()) for t in root.iter(f"{SVG}title")]
    labels = [o["label"] for o in plate["objects"]]
    assert sorted(titles) == sorted(labels)
    assert sorted(p.get("data-label") for p in object_polygons(root)) == sorted(labels)
    keys = _key_texts(root)
    for i, label in enumerate(labels):
        assert f"{i + 1}. {truncate_label(label)}" in keys


def test_snapshot_states_differ_by_pattern_not_only_colour():
    root = snapshot(CASES["six-states"]["plate"])
    overlays = [p.get("fill") for p in root.iter(f"{SVG}polygon") if p.get("class") == "plate-map-pattern"]
    assert len(overlays) == 5 and len(set(overlays)) == 5  # 'solid' needs no overlay; the other five are all different
    legend = next(g for g in root.iter(f"{SVG}g") if g.get("class") == "plate-map-legend")
    assert ["".join(t.itertext()) for t in legend.iter(f"{SVG}text")] == [s["name"] for s in CASES["six-states"]["plate"]["states"]]
    swatches = [r.get("data-pattern") for r in legend.iter(f"{SVG}rect") if r.get("data-pattern")]
    assert swatches == list(PATTERNS)


def test_snapshot_never_emits_a_bad_colour():
    raw = render_snapshot_svg(copy.deepcopy(CASES["six-states"]["plate"]))
    assert "alert(1)" not in raw
    assert raw.count(plate_map.NEUTRAL_FILL) >= 2  # the S6 object and its legend swatch


def test_snapshot_flags_off_bed_and_unselectable_and_unmapped_in_the_key():
    keys = _key_texts(snapshot(CASES["off-bed"]["plate"]))
    assert "1. Half off (off bed)" in keys and "2. Beyond (off bed)" in keys and "3. On" in keys
    root = snapshot(CASES["mixed-selectable"]["plate"])
    assert "2. Locked (not selectable)" in _key_texts(root)
    locked = next(p for p in object_polygons(root) if p.get("data-label") == "Locked")
    assert locked.get("stroke-dasharray") and locked.get("data-selectable") == "false"
    assert "2. Unmapped (not on map)" in _key_texts(snapshot(CASES["missing-footprint"]["plate"]))


def test_snapshot_front_label_is_below_the_bed():
    root = snapshot(CASES["square-front-left"]["plate"])
    front = next(t for t in root.iter(f"{SVG}text") if t.get("class") == "plate-map-front")
    assert front.text == "Front" and float(front.get("y")) > 256


def test_snapshot_without_geometry_has_legend_and_key_only():
    root = snapshot(CASES["no-geometry"]["plate"])
    assert object_polygons(root) == []
    assert not [r for r in root.iter(f"{SVG}rect") if r.get("class") == "plate-map-bed"]
    assert _key_texts(root) == ["1. One (not on map)", "2. Two (not on map)"]


def test_snapshot_tiny_object_ring_and_markers():
    root = snapshot(CASES["tiny-object"]["plate"])
    rings = [r for r in root.iter(f"{SVG}rect") if r.get("class") == "plate-map-hit"]
    assert len(rings) == 1
    markers = ["".join(t.itertext()) for t in root.iter(f"{SVG}text") if t.get("class") == "plate-map-marker"]
    assert markers == ["1", "2"]


def test_snapshot_rejects_markup_keys():
    """The renderer takes data only: a caller cannot hand it SVG to embed."""
    plate = copy.deepcopy(CASES["square-front-left"]["plate"])
    plate["objects"][0]["svg"] = "<script>alert(1)</script>"
    with pytest.raises(ValueError, match="unknown key svg"):
        render_snapshot_svg(plate)


def test_truncate_counts_code_points():
    assert truncate_label("\U0001F9E9" * 41) == "\U0001F9E9" * 40 + "…"
    assert truncate_label("x" * 40) == "x" * 40


def test_module_imports_only_stdlib():
    tree = ast.parse(Path(plate_map.__file__).read_text(encoding="utf-8"))
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= {"__future__", "math", "re", "typing", "xml"}, mods
