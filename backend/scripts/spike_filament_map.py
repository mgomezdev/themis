"""Spike: does OrcaSlicer's filament_map route a single filament to a chosen extruder?

Slices a built-in 10mm cube twice for a multi-extruder machine — filament_map=["1"]
and ["3"] — then reports the first tool-select / active-extruder line found in each
gcode. If they differ (e.g. T0 vs T2 / extruder vs extruder2), filament_map routes
the tool and we use Approach A. If identical, fall back to Approach B (connector
gcode activation).

Usage:
  backend\\.venv\\Scripts\\python.exe scripts\\spike_filament_map.py <machine> <process> <filament>
"""
from __future__ import annotations
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/ on path

from app.config import get_orca_executable
from app.services.preset_resolver import PresetResolver
from app.services.project_config_builder import build_project_config
from app.services.mesh_3mf_builder import stl_to_3mf

# Minimal 10mm cube, ASCII STL (12 triangles).
CUBE_STL = """solid cube
facet normal 0 0 -1
 outer loop
  vertex 0 0 0
  vertex 10 10 0
  vertex 10 0 0
 endloop
endfacet
facet normal 0 0 -1
 outer loop
  vertex 0 0 0
  vertex 0 10 0
  vertex 10 10 0
 endloop
endfacet
facet normal 0 0 1
 outer loop
  vertex 0 0 10
  vertex 10 0 10
  vertex 10 10 10
 endloop
endfacet
facet normal 0 0 1
 outer loop
  vertex 0 0 10
  vertex 10 10 10
  vertex 0 10 10
 endloop
endfacet
facet normal 0 -1 0
 outer loop
  vertex 0 0 0
  vertex 10 0 0
  vertex 10 0 10
 endloop
endfacet
facet normal 0 -1 0
 outer loop
  vertex 0 0 0
  vertex 10 0 10
  vertex 0 0 10
 endloop
endfacet
facet normal 1 0 0
 outer loop
  vertex 10 0 0
  vertex 10 10 0
  vertex 10 10 10
 endloop
endfacet
facet normal 1 0 0
 outer loop
  vertex 10 0 0
  vertex 10 10 10
  vertex 10 0 10
 endloop
endfacet
facet normal 0 1 0
 outer loop
  vertex 0 10 0
  vertex 0 10 10
  vertex 10 10 10
 endloop
endfacet
facet normal 0 1 0
 outer loop
  vertex 0 10 0
  vertex 10 10 10
  vertex 10 10 0
 endloop
endfacet
facet normal -1 0 0
 outer loop
  vertex 0 0 0
  vertex 0 0 10
  vertex 0 10 10
 endloop
endfacet
facet normal -1 0 0
 outer loop
  vertex 0 0 0
  vertex 0 10 10
  vertex 0 10 0
 endloop
endfacet
endsolid cube
"""

TOOL_RE = re.compile(r"^(T\d|ACTIVATE_EXTRUDER\b.*|M104\b.*\bT\d.*)", re.MULTILINE)


def _run_slice(orca: str, prepared: Path, out: Path, plate: str) -> list[Path]:
    """Run OrcaSlicer with a given --slice plate number; return list of gcode files."""
    result = subprocess.run(
        [orca, "--slice", plate, "--outputdir", str(out), "--arrange", "1", str(prepared)],
        capture_output=True, text=True,
    )
    gcodes = list(out.glob("*.gcode"))
    if result.returncode != 0 and not gcodes:
        print(f"  [slice plate={plate}] stderr: {result.stderr[-400:]}", file=sys.stderr)
    return gcodes


def slice_with_map(orca: str, machine: str, process: str, filament: str,
                   fmap: list[str], workdir: Path) -> str:
    resolver = PresetResolver()
    m = resolver.resolve(machine, "machine")
    p = resolver.resolve(process, "process")
    f = resolver.resolve(filament, "filament")
    cfg = build_project_config(m, p, [f], None, plate_count=1)
    cfg["filament_map"] = fmap  # <-- the thing under test
    stl = workdir / "cube.stl"
    stl.write_text(CUBE_STL)
    prepared = workdir / f"prepared_{fmap[0]}.3mf"
    stl_to_3mf(str(stl), cfg, prepared)
    out = workdir / f"out_{fmap[0]}"
    out.mkdir(exist_ok=True)

    # Try --slice 0 first (plate numbering can be 0-based or 1-based).
    gcodes = _run_slice(orca, prepared, out, "0")
    if not gcodes:
        print(f"  filament_map={fmap}: --slice 0 produced no gcode; retrying with --slice 1",
              file=sys.stderr)
        gcodes = _run_slice(orca, prepared, out, "1")
        if gcodes:
            print(f"  filament_map={fmap}: --slice 1 worked", file=sys.stderr)
    else:
        print(f"  filament_map={fmap}: --slice 0 worked", file=sys.stderr)

    if not gcodes:
        return "(no gcode produced)"

    text = gcodes[0].read_text(errors="ignore")
    hits = TOOL_RE.findall(text)

    # Also print context around tool-select lines for inspection.
    for hit in hits[:5]:
        print(f"  [map={fmap}] tool line: {hit!r}", file=sys.stderr)

    return hits[0] if hits else "(no tool-select line found)"


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: spike_filament_map.py <machine> <process> <filament>")
        return 2
    machine, process, filament = sys.argv[1:4]
    orca = get_orca_executable()
    print(f"OrcaSlicer: {orca}", file=sys.stderr)
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        a = slice_with_map(orca, machine, process, filament, ["1"], wd)
        b = slice_with_map(orca, machine, process, filament, ["3"], wd)
    print(f"filament_map=[1] -> {a!r}")
    print(f"filament_map=[3] -> {b!r}")
    print("ROUTES BY TOOL" if a != b else "NO DIFFERENCE — use Approach B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
