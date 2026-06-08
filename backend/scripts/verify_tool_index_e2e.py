#!/usr/bin/env python3
"""End-to-end check: a real SliceRequest(tool_index=N) through SlicerService.slice
produces gcode targeting the chosen tool — exercises the WHOLE pipeline
(SliceRequest.tool_index -> slice() -> stl_to_3mf(tool_index) -> per-object extruder
metadata -> OrcaSlicer), not just the spike's manual injection. No printer needed.

Run: backend/.venv/Scripts/python.exe scripts/verify_tool_index_e2e.py
"""
from __future__ import annotations
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/ on path

from app.services.slicer_service import SlicerService, SliceRequest

MACHINE = "Snapmaker U1 (0.4 nozzle)"
PROCESS = "0.08 Extra Fine @Snapmaker U1 (0.4 nozzle)"
FILAMENT = "AliZ PA-CF @System"

# A small 10mm cube (12 triangles) so the slice actually extrudes and emits a tool.
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

TOOL_RE = re.compile(r"^(T\d|M10[49]\b.*\bT\d.*)", re.MULTILINE)


def slice_tool(tool_index: int) -> str:
    td = Path(tempfile.mkdtemp())
    stl = td / "cube.stl"
    stl.write_text(CUBE_STL)
    svc = SlicerService()
    req = SliceRequest(
        job_id=990000 + tool_index,
        source_3mf=str(stl),
        plate_number=0,
        machine_preset=MACHINE,
        process_preset=PROCESS,
        filament_presets=[FILAMENT],
        tool_index=tool_index,
    )
    gcode_path = svc.slice(req)
    text = Path(gcode_path).read_text(errors="ignore")
    hits = TOOL_RE.findall(text)
    return hits[0] if hits else "(no tool-select line)"


def main() -> int:
    results = {ti: slice_tool(ti) for ti in (0, 2)}
    for ti, line in results.items():
        print(f"tool_index={ti} -> {line!r}")
    # T0 expected for tool 0; T2 (extruder index 3, 1-based) for tool 2.
    ok = ("T2" in results[2]) and (results[0] != results[2])
    print("E2E PASS — pipeline routes by tool" if ok else "E2E FAIL — tool not routed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
