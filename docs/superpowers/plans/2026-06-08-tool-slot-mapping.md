# Single-Filament Tool Selection (Snapmaker U1) — Implementation Plan (Project 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let a job pick which physical tool/extruder (T0–T3) a single-material print runs on, on multi-tool printers like the Snapmaker U1, by binding the slice's filament to the chosen extruder via OrcaSlicer `filament_map`.

**Architecture:** A new nullable `tool_index` on `JobPrinterConfig` flows New Job → create-route → queue → `SliceRequest` → the **3MF prep** (`mesh_3mf_builder`), which sets the sliced object's `<metadata key="extruder" value="{tool_index+1}"/>` in `model_settings.config` so OrcaSlicer emits the chosen tool's gcode. New Job shows a tool picker for any printer with ≥2 loaded slots; the picked slot supplies the filament profile/colour and the queue resolves `loaded_filaments[tool_index]` directly. **The verification spike (Task 1) is already done — it proved `filament_map` does NOT route the tool; the per-object `extruder` metadata does (Approach C).** `build_project_config` is unchanged (its existing filament-array padding to `n_extruders` is the load-bearing dependency).

**Tech Stack:** Python/FastAPI/SQLAlchemy/aiosqlite, OrcaSlicer CLI, React/Vite/TS, pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-06-08-tool-slot-mapping-design.md`. **Branch:** `tool-slot-mapping` (worktree at `C:\Users\mgome\Documents\projects\themis-tool-mapping`).

## Conventions (give to every subagent)
- This is a **git worktree**; run everything from `C:\Users\mgome\Documents\projects\themis-tool-mapping`. Do NOT cd to the other checkout.
- Backend tests: `cd backend && backend\.venv\Scripts\python.exe -m pytest <path> -v`. **The worktree has no `.venv` yet** — Task 1 creates it (`python -m venv .venv` from the **python.org** interpreter, then `pip install -e ".[dev]"`). Subsequent tasks reuse it.
- Frontend: `cd frontend && npm install` (first time), `npm run build` (tsc -b — the real typecheck), `npx vitest run <file>`.
- Commit after each task. Do NOT push.
- `tool_index` is **0-based** (T0–T3, = loaded-slot index). OrcaSlicer `filament_map` is **1-based** → `filament_map = [str(tool_index + 1)]`.

## Model tuning
**Tasks 1, 2, 6, 7, 8** are **Sonnet** (spike done; T2 = 3MF/XML judgment; integration/UI/docs). **Tasks 3, 4, 5** are **Haiku** (mechanical, complete code).

## Status
**Task 1 (spike) is COMPLETE** (commits `ceb3f48`, `263c773`, `4a479ba`): it disproved `filament_map` (Approach A/B) and proved **Approach C — per-object `extruder` metadata in `model_settings.config`**. The plan below is the Approach-C revision. Start execution at **Task 2**.

## File structure
- Spike: `backend/scripts/spike_filament_map.py` (Task 1 — DONE; kept for re-runs).
- `backend/app/services/mesh_3mf_builder.py` — object `extruder` metadata injection (Task 2).
- `backend/app/services/slicer_service.py` — `SliceRequest.tool_index` + forward to the 3MF prep (Task 3).
- `backend/app/models.py` + `backend/app/database.py` — column + migration (Task 4).
- `backend/app/api/routes/jobs.py` — `PrinterConfigInput.tool_index` + persist (Task 5).
- `backend/app/services/queue_engine.py` — slot-by-index + eligibility + pass through (Task 6).
- `frontend/src/api/queue.ts` + `frontend/src/screens/NewJobScreen.tsx` — tool picker + payload (Task 7).
- Docs (Task 8).

---

## Task 1: Verification spike — does `filament_map` route a single filament?

**Model: Sonnet.** Exploratory (not TDD). Gates the mechanism (Approach A vs B).

**Goal:** Prove that setting `filament_map=["3"]` in the project config makes OrcaSlicer emit gcode that prints on extruder index 3 (tool T2) for the Snapmaker U1, vs `["1"]` → extruder 1 (T0).

**Files:** Create `backend/scripts/spike_filament_map.py`.

- [ ] **Step 1: Set up the worktree backend env**

```
cd backend
python -m venv .venv
backend\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```
(Use the python.org interpreter — `py -0` lists them — not the Microsoft Store build; see CLAUDE.md.)

- [ ] **Step 2: Get the U1's real preset names**

The script needs a machine preset (the U1's `current_orca_printer_profile`), a process preset, and a filament preset that exist in the user's OrcaSlicer config. With the backend running (other checkout is fine, or start this one), fetch them:

```
# the U1 is printer id 3; adjust if different
curl -s http://127.0.0.1:8001/api/v1/printers/3 | python -c "import sys,json;d=json.load(sys.stdin);print(d['current_orca_printer_profile'])"
curl -s http://127.0.0.1:8001/api/v1/printers/3/profiles | python -m json.tool
```
Record one machine preset, one `print_profiles[*]`, one `filament_profiles[*]`.

- [ ] **Step 3: Write the spike script**

```python
# backend/scripts/spike_filament_map.py
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


def slice_with_map(orca, machine, process, filament, fmap, workdir: Path) -> str:
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
    subprocess.run([orca, "--slice", "0", "--outputdir", str(out), str(prepared)],
                   check=True, capture_output=True, text=True)
    gcodes = list(out.glob("*.gcode"))
    if not gcodes:
        return "(no gcode produced)"
    text = gcodes[0].read_text(errors="ignore")
    hits = TOOL_RE.findall(text)
    return hits[0] if hits else "(no tool-select line found)"


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: spike_filament_map.py <machine> <process> <filament>")
        return 2
    machine, process, filament = sys.argv[1:4]
    orca = get_orca_executable()
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
```

- [ ] **Step 4: Run the spike + record the result**

Run (substitute the preset names from Step 2):
```
cd backend && backend\.venv\Scripts\python.exe scripts\spike_filament_map.py "<machine>" "<process>" "<filament>"
```
Append the two printed lines + the verdict to the spec's "Verification status / sequencing" section (`docs/superpowers/specs/2026-06-08-tool-slot-mapping-design.md`).
- **If `ROUTES BY TOOL`:** Approach A confirmed — proceed to Task 2 as written.
- **If `NO DIFFERENCE`:** STOP and report. Tasks 2 and 6 must be re-planned for Approach B (the `SnapmakerExtendedClient` prepends `ACTIVATE_EXTRUDER EXTRUDER=extruder{tool_index}` before the print and the slice leaves `filament_map` alone). Do not guess — surface this to the controller.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/spike_filament_map.py docs/superpowers/specs/2026-06-08-tool-slot-mapping-design.md
git commit -m "spike(tool-mapping): verify filament_map routes a single filament to a chosen extruder"
```

---

## Task 2: Inject object `extruder` metadata into `model_settings.config`

**Model: Sonnet.** (3MF/XML judgment.) **This is the verified Approach-C mechanism.** `build_project_config` is NOT touched (its filament padding to `n_extruders` already satisfies the dependency).

**Files:**
- Modify: `backend/app/services/mesh_3mf_builder.py`
- Test: `backend/tests/services/test_mesh_3mf_builder.py` (extend)

Add two pure helpers + a `tool_index` param to `stl_to_3mf` and `build_sliceable_3mf` so the sliced object(s) carry `<metadata key="extruder" value="{tool_index+1}"/>` in `Metadata/model_settings.config`. `tool_index=None` ⇒ write nothing new (today's behavior, byte-identical).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/services/test_mesh_3mf_builder.py`:

```python
import zipfile
from app.services.mesh_3mf_builder import (
    _model_settings_with_extruder, _patch_model_settings_extruder,
    _object_ids_from_model, stl_to_3mf,
)

_ONE_TRI_STL = """solid t
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 10 0 0
  vertex 0 10 0
 endloop
endfacet
endsolid t
"""


def test_model_settings_with_extruder_builds_objects():
    xml = _model_settings_with_extruder(["1", "2"], 3).decode("utf-8")
    assert '<object id="1">' in xml and '<object id="2">' in xml
    assert xml.count('key="extruder" value="3"') == 2


def test_patch_overrides_existing_object_extruder_and_preserves_others():
    src = (b'<?xml version="1.0" encoding="UTF-8"?>\n<config>'
           b'<object id="5"><metadata key="name" value="x"/>'
           b'<metadata key="extruder" value="1"/></object></config>')
    out = _patch_model_settings_extruder(src, 4).decode("utf-8")
    assert 'value="4"' in out and 'value="1"' not in out
    assert 'key="name"' in out  # unrelated metadata preserved


def test_patch_adds_extruder_when_absent():
    src = b'<?xml version="1.0"?>\n<config><object id="7"><metadata key="name" value="y"/></object></config>'
    out = _patch_model_settings_extruder(src, 2).decode("utf-8")
    assert 'key="extruder" value="2"' in out


def test_object_ids_from_model():
    model = b'<model><resources><object id="1" type="model"></object><object id="3"></object></resources></model>'
    assert _object_ids_from_model(model) == ["1", "3"]


def test_stl_to_3mf_writes_object_extruder(tmp_path):
    stl = tmp_path / "c.stl"; stl.write_text(_ONE_TRI_STL)
    out = tmp_path / "c.3mf"
    stl_to_3mf(str(stl), {"nozzle_diameter": ["0.4"]}, out, tool_index=2)
    with zipfile.ZipFile(out) as z:
        ms = z.read("Metadata/model_settings.config").decode("utf-8")
    assert 'key="extruder" value="3"' in ms  # tool 2 (0-based) -> extruder 3 (1-based)


def test_stl_to_3mf_omits_model_settings_when_tool_index_none(tmp_path):
    stl = tmp_path / "c.stl"; stl.write_text(_ONE_TRI_STL)
    out = tmp_path / "c.3mf"
    stl_to_3mf(str(stl), {"nozzle_diameter": ["0.4"]}, out)
    with zipfile.ZipFile(out) as z:
        assert "Metadata/model_settings.config" not in z.namelist()
```

- [ ] **Step 2: Run — confirm FAIL**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_mesh_3mf_builder.py -k "extruder or object_ids or model_settings" -v`
Expected: FAIL — the helpers don't exist / `stl_to_3mf` has no `tool_index`.

- [ ] **Step 3: Implement**

In `backend/app/services/mesh_3mf_builder.py`, add `import xml.etree.ElementTree as ET` near the other imports (it already imports `re`, `zipfile`, `json`). Add the helpers (place them above `build_sliceable_3mf`):

```python
def _model_settings_with_extruder(object_ids: list[str], extruder_1based: int) -> bytes:
    """A fresh model_settings.config assigning each object id to the given extruder."""
    objs = "".join(
        f'<object id="{oid}"><metadata key="extruder" value="{extruder_1based}"/></object>'
        for oid in object_ids
    )
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<config>{objs}</config>'.encode("utf-8")


def _object_ids_from_model(model_xml: bytes) -> list[str]:
    """Object ids declared in a 3D/3dmodel.model (resources/object id=...)."""
    return [m.decode() for m in re.findall(rb'<object[^>]*\bid="([^"]+)"', model_xml)]


def _patch_model_settings_extruder(model_settings: bytes, extruder_1based: int) -> bytes:
    """Set/override every <object>'s extruder metadata, preserving all other content."""
    root = ET.fromstring(model_settings)
    for obj in root.findall("object"):
        for md in list(obj.findall("metadata")):
            if md.get("key") == "extruder":
                obj.remove(md)
        md = ET.SubElement(obj, "metadata")
        md.set("key", "extruder")
        md.set("value", str(extruder_1based))
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + body).encode("utf-8")
```

Add `tool_index: int | None = None` to `stl_to_3mf` and write the model_settings inside its `ZipFile` block (after the `project_settings.config` line):

```python
def stl_to_3mf(stl_path: str | Path, project_config: dict, out_path: str | Path,
               tool_index: int | None = None) -> Path:
    ...
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/project_settings.config", json.dumps(project_config))
        if tool_index is not None:
            z.writestr("Metadata/model_settings.config",
                       _model_settings_with_extruder(["1"], tool_index + 1))
    return out_path
```

Add `tool_index: int | None = None` to `build_sliceable_3mf` and manage model_settings when it's set (capture the source's model_settings + model even while dropping them, then re-emit patched/created):

```python
def build_sliceable_3mf(source_3mf, project_config, out_path, geometry_only=False,
                        tool_index=None) -> Path:
    source_3mf, out_path = Path(source_3mf), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    config_bytes = json.dumps(project_config).encode("utf-8")
    drop = set(_DROPPED) | {_REPLACED}
    if geometry_only:
        drop.add(_MODEL)
    if tool_index is not None:
        drop.add(_MODEL)  # we re-emit model_settings ourselves below

    with zipfile.ZipFile(source_3mf) as zin, zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        model_xml = b""
        src_model_settings = b""
        for item in zin.namelist():
            low = item.lower()
            if low == "3d/3dmodel.model":
                model_xml = zin.read(item)
            if low == _MODEL:
                src_model_settings = zin.read(item)
            if low in drop:
                continue
            zout.writestr(item, zin.read(item))
        zout.writestr("Metadata/project_settings.config", config_bytes)
        if tool_index is not None:
            ext = tool_index + 1
            if src_model_settings and not geometry_only:
                ms = _patch_model_settings_extruder(src_model_settings, ext)
            else:
                ms = _model_settings_with_extruder(_object_ids_from_model(model_xml) or ["1"], ext)
            zout.writestr("Metadata/model_settings.config", ms)
    return out_path
```

(`_MODEL` is `"metadata/model_settings.config"` lowercase — the `low in drop` check is case-insensitive; we always WRITE the canonical `"Metadata/model_settings.config"`.)

- [ ] **Step 4: Run — confirm PASS + full suite**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_mesh_3mf_builder.py -v` → PASS. Then `cd backend && backend\.venv\Scripts\python.exe -m pytest -q` → green (the existing `build_sliceable_3mf` tests must still pass — `tool_index=None` is byte-identical to before).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/mesh_3mf_builder.py backend/tests/services/test_mesh_3mf_builder.py
git commit -m "feat(slice): inject per-object extruder metadata for tool selection"
```

---

## Task 3: Thread `tool_index` through `SliceRequest` → the 3MF prep

**Model: Haiku.**

**Files:**
- Modify: `backend/app/services/slicer_service.py`
- Test: `backend/tests/services/test_slicer_service.py` (create if absent, else extend)

`SliceRequest` gains `tool_index`; `slice()` forwards it to `stl_to_3mf` / `build_sliceable_3mf` (the model_settings writer from Task 2). `_build_config` / `build_project_config` are NOT changed.

- [ ] **Step 1: Write the failing test**

Create/extend `backend/tests/services/test_slicer_service.py`:

```python
from unittest.mock import patch
from app.services.slicer_service import SlicerService, SliceRequest


def _req(**kw):
    base = dict(job_id=1, source_3mf="x.stl", plate_number=0, machine_preset="M",
                process_preset="P", filament_presets=["F"])
    base.update(kw)
    return SliceRequest(**base)


def test_slice_request_has_tool_index_default_none():
    assert _req().tool_index is None
    assert _req(tool_index=2).tool_index == 2


def test_slice_forwards_tool_index_to_stl_builder(tmp_path):
    svc = SlicerService.__new__(SlicerService)  # skip __init__
    svc._data_dir = tmp_path
    with patch.object(SlicerService, "_build_config", return_value={"k": "v"}), \
         patch("app.services.slicer_service.stl_to_3mf") as stl, \
         patch.object(SlicerService, "_run", return_value="out.gcode"):
        svc.slice(_req(source_3mf="x.stl", tool_index=2))
        assert stl.call_args.kwargs.get("tool_index") == 2


def test_slice_forwards_tool_index_to_3mf_builder(tmp_path):
    svc = SlicerService.__new__(SlicerService)
    svc._data_dir = tmp_path
    with patch.object(SlicerService, "_build_config", return_value={"k": "v"}), \
         patch("app.services.slicer_service.build_sliceable_3mf") as b3, \
         patch.object(SlicerService, "_run", return_value="out.gcode"):
        svc.slice(_req(source_3mf="x.3mf", tool_index=2))
        assert b3.call_args.kwargs.get("tool_index") == 2
```

- [ ] **Step 2: Run — confirm FAIL**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_slicer_service.py -v`
Expected: FAIL — `SliceRequest` has no `tool_index` / not forwarded to the builders.

- [ ] **Step 3: Implement**

In `backend/app/services/slicer_service.py`, add the field to `SliceRequest` (after `export_args`, ~line 43):

```python
    export_args: list[str] = field(default_factory=list)
    tool_index: int | None = None
```

In `slice()` (~lines 65-78), forward `req.tool_index` to all three 3MF-prep call sites:

```python
        if Path(req.source_3mf).suffix.lower() == ".stl":
            stl_to_3mf(req.source_3mf, config, prepared, tool_index=req.tool_index)
            return self._run(prepared, req, out_dir)

        # Primary: preserve model_settings (per-object overrides / paint).
        build_sliceable_3mf(req.source_3mf, config, prepared, geometry_only=False, tool_index=req.tool_index)
        try:
            return self._run(prepared, req, out_dir)
        except SliceError as primary_err:
            logger.warning("Slice failed for job %s; retrying geometry-only: %s", req.job_id, primary_err)

        # Recovery: drop the file's own settings/overrides, apply ours fresh.
        build_sliceable_3mf(req.source_3mf, config, prepared, geometry_only=True, tool_index=req.tool_index)
        return self._run(prepared, req, out_dir)
```

(Leave `_build_config` and its `build_project_config(...)` call exactly as they are.)

- [ ] **Step 4: Run — confirm PASS + full suite**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_slicer_service.py -v` → PASS. Then `... -m pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/slicer_service.py backend/tests/services/test_slicer_service.py
git commit -m "feat(slice): SliceRequest.tool_index forwarded to the 3MF prep"
```

---

## Task 4: `JobPrinterConfig.tool_index` column + migration

**Model: Haiku.**

**Files:**
- Modify: `backend/app/models.py`, `backend/app/database.py`
- Test: `backend/tests/test_migrations.py` (create) — or extend an existing migration test if present.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_migrations.py`:

```python
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.database import _migrate, Base


@pytest.mark.asyncio
async def test_migrate_adds_tool_index_idempotently():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate(conn)
        await _migrate(conn)  # idempotent — second run must not raise
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(job_printer_configs)"))).fetchall()}
    assert "tool_index" in cols
    await engine.dispose()
```

(If `Base.metadata.create_all` already creates `tool_index` from the model, the migration's guard simply skips it — still idempotent. The test asserts the column exists and the double-run is safe.)

- [ ] **Step 2: Run — confirm FAIL**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/test_migrations.py -v`
Expected: FAIL — `tool_index` not in columns (model lacks it / migration doesn't add it).

- [ ] **Step 3: Implement**

In `backend/app/models.py`, add to `JobPrinterConfig` (after `filament_color`, ~line 100):

```python
    tool_index: Mapped[Optional[int]] = mapped_column(nullable=True)
```

In `backend/app/database.py`, inside `_migrate`, in the `if jpc_cols:` block (after the `filament_color` guard, ~line 51):

```python
        if "tool_index" not in jpc_cols:
            await conn.execute(text("ALTER TABLE job_printer_configs ADD COLUMN tool_index INTEGER"))
```

- [ ] **Step 4: Run — confirm PASS + full suite**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/test_migrations.py -v` → PASS. Then `... -m pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/app/database.py backend/tests/test_migrations.py
git commit -m "feat(model): JobPrinterConfig.tool_index column + idempotent migration"
```

---

## Task 5: Accept + persist `tool_index` in the create-job route

**Model: Haiku.**

**Files:**
- Modify: `backend/app/api/routes/jobs.py`
- Test: `backend/tests/api/test_jobs_tool_index.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/api/test_jobs_tool_index.py`:

```python
from app.api.routes.jobs import PrinterConfigInput


def test_printer_config_input_accepts_tool_index():
    c = PrinterConfigInput(printer_id=1, print_profile="p", tool_index=2)
    assert c.tool_index == 2
    # default is None (single-tool / legacy)
    assert PrinterConfigInput(printer_id=1, print_profile="p").tool_index is None
```

- [ ] **Step 2: Run — confirm FAIL**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/api/test_jobs_tool_index.py -v`
Expected: FAIL — `PrinterConfigInput` has no `tool_index`.

- [ ] **Step 3: Implement**

In `backend/app/api/routes/jobs.py`, add to `PrinterConfigInput` (after `filament_color`, ~line 33):

```python
    tool_index: int | None = None
```

And in the `JobPrinterConfig(...)` construction (~line 132), add the field:

```python
        config = JobPrinterConfig(
            job_id=job.id,
            printer_id=cfg.printer_id,
            print_profile=cfg.print_profile,
            filament_profile=cfg.filament_profile,
            filament_id=cfg.filament_id,
            filament_type=cfg.filament_type,
            filament_color=cfg.filament_color,
            tool_index=cfg.tool_index,
        )
```

- [ ] **Step 4: Run — confirm PASS + full suite**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/api/test_jobs_tool_index.py -v` → PASS. Then `... -m pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/jobs.py backend/tests/api/test_jobs_tool_index.py
git commit -m "feat(jobs): accept + persist tool_index on per-printer config"
```

---

## Task 6: Queue resolves the slot by `tool_index` + gates on it

**Model: Sonnet.** Integration with the live claim/slice path.

**Files:**
- Modify: `backend/app/services/queue_engine.py`
- Test: `backend/tests/services/test_queue_tool_index.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/services/test_queue_tool_index.py`:

```python
from types import SimpleNamespace
from app.services.queue_engine import _slot_for_config, _filament_mismatch


def _cfg(**kw):
    base = dict(filament_type=None, filament_color=None, tool_index=None)
    base.update(kw)
    return SimpleNamespace(**base)


LOADED = [
    {"slot": 0, "type": "PLA", "color": "#fff", "filament_profile": "PLA @U1"},
    {"slot": 1, "type": "PETG", "color": "#000", "filament_profile": "PETG @U1"},
    {"slot": 2, "type": "TPU", "color": "#0f0", "filament_profile": "TPU @U1"},
]


def test_slot_for_config_uses_tool_index_directly():
    slot = _slot_for_config(_cfg(tool_index=2), LOADED)
    assert slot["filament_profile"] == "TPU @U1"


def test_slot_for_config_tool_index_out_of_range_returns_none():
    assert _slot_for_config(_cfg(tool_index=9), LOADED) is None


def test_slot_for_config_falls_back_to_ask_match_when_no_tool_index():
    # type+color ask still matches a slot when tool_index is None
    slot = _slot_for_config(_cfg(filament_type="PETG", filament_color="#000"), LOADED)
    assert slot["filament_profile"] == "PETG @U1"


def test_mismatch_blocks_when_tool_index_slot_missing():
    assert _filament_mismatch(_cfg(tool_index=9), LOADED) is not None
    assert _filament_mismatch(_cfg(tool_index=1), LOADED) is None
```

- [ ] **Step 2: Run — confirm FAIL**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_queue_tool_index.py -v`
Expected: FAIL — `_slot_for_config` does not exist.

- [ ] **Step 3: Implement**

In `backend/app/services/queue_engine.py`, add a helper just below `_matching_loaded_filament` (~after line 45):

```python
def _slot_for_config(config, loaded: list) -> dict | None:
    """The loaded slot this config should print with: the explicit tool_index slot
    if set (multi-tool printers), else the type/color ask match."""
    ti = getattr(config, "tool_index", None)
    if ti is not None:
        loaded = loaded or []
        return loaded[ti] if 0 <= ti < len(loaded) else None
    return _matching_loaded_filament(config, loaded)
```

Update `_filament_mismatch` (~line 48) to gate on `tool_index` when present:

```python
def _filament_mismatch(config: JobPrinterConfig, loaded: list) -> str | None:
    """Return a reason string if the config can't be satisfied by the printer's
    loaded filaments, else None."""
    if getattr(config, "tool_index", None) is not None:
        if _slot_for_config(config, loaded) is None:
            return f"tool T{config.tool_index} has no loaded filament"
        return None
    req_type = (config.filament_type or "").strip().lower()
    req_color = _norm_color(config.filament_color)
    if not req_type and not req_color:
        return None
    if _matching_loaded_filament(config, loaded) is not None:
        return None
    return (f"loaded filament doesn't match required "
            f"{config.filament_type or '?'} {config.filament_color or '?'}")
```

In `_run_slice_and_print` (~line 206), replace the slot resolution + add `tool_index` to the `SliceRequest`. Change:

```python
            slot = _matching_loaded_filament(config, loaded) if config else None
```
to:
```python
            slot = _slot_for_config(config, loaded) if config else None
            cfg_tool_index = config.tool_index if config else None
```

And in the `SliceRequest(...)` construction (~line 231), add the field:

```python
        req = SliceRequest(
            job_id=job_id,
            source_3mf=stored_path,
            plate_number=plate_number,
            machine_preset=machine_preset,
            process_preset=print_profile,
            filament_presets=[filament_profile] if filament_profile else [],
            filament_colours=[filament_color] if filament_color else [],
            export_args=export_args,
            tool_index=cfg_tool_index,
        )
```

- [ ] **Step 4: Run — confirm PASS + full suite**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_queue_tool_index.py -v` → PASS. Then `... -m pytest -q` → green (watch for regressions in existing queue tests — the `tool_index is None` path must behave exactly as before).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/queue_engine.py backend/tests/services/test_queue_tool_index.py
git commit -m "feat(queue): resolve slot by tool_index + gate eligibility; pass to slice"
```

---

## Task 7: New Job tool picker (≥2-slot printers)

**Model: Sonnet.** UI + payload wiring.

**Files:**
- Modify: `frontend/src/api/queue.ts` (`PrinterConfigInput`)
- Modify: `frontend/src/screens/NewJobScreen.tsx`
- Test: `frontend/src/screens/NewJobScreen.test.tsx` (extend)

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/screens/NewJobScreen.test.tsx` a test that renders `PerPrinterConfig` for a printer with ≥2 loaded slots and asserts a tool picker appears and writes `toolIndex`. Use the file's existing render helpers/mocks; the assertion shape:

```tsx
// Printer with 2+ loaded slots shows a tool picker (testid "tool-select"),
// and selecting tool index 2 calls onChange with toolIndex: 2 and copies the slot's filament.
it('shows a tool picker for multi-slot printers and writes toolIndex', async () => {
  const onChange = vi.fn();
  const printer = {
    id: 3, name: 'U1', printer_type: 'snapmaker_extended',
    current_orca_printer_profile: 'U1', loaded_filaments: [
      { slot: 0, type: 'PLA', color: '#fff', name: 'PLA', filament_profile: 'PLA @U1' },
      { slot: 1, type: 'PETG', color: '#000', name: 'PETG', filament_profile: 'PETG @U1' },
      { slot: 2, type: 'TPU', color: '#0f0', name: 'TPU', filament_profile: 'TPU @U1' },
    ],
  };
  render(<PerPrinterConfig printerId="3" printers={[printer as any]}
            config={{ printProfile: 'p', filamentProfile: null, filamentId: null, filamentType: null, filamentColor: null, toolIndex: null }}
            onChange={onChange} />);
  const sel = await screen.findByTestId('tool-select');
  fireEvent.change(sel, { target: { value: '2' } });
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ toolIndex: 2, filamentProfile: 'TPU @U1', filamentType: 'TPU' }));
});
```

(Export `PerPrinterConfig` from `NewJobScreen.tsx` if it isn't already, so the test can import it — add `export` to the `function PerPrinterConfig` declaration.)

- [ ] **Step 2: Run — confirm FAIL**

Run: `cd frontend && npx vitest run src/screens/NewJobScreen.test.tsx`
Expected: FAIL — no `tool-select` element / `toolIndex` not in type.

- [ ] **Step 3: Implement**

**a.** In `frontend/src/api/queue.ts`, add to `PrinterConfigInput` (interface ~line 22):

```typescript
  tool_index?: number | null;
```

**b.** In `frontend/src/screens/NewJobScreen.tsx`, add `toolIndex` to `PerPrinterCfg` (interface ~line 26):

```typescript
  toolIndex: number | null;
```

Initialize `toolIndex: null` wherever a `PerPrinterCfg` default object is created (search for the other defaults like `filamentProfile: null` and add `toolIndex: null` alongside).

**c.** Export the component and render the tool picker. Change `function PerPrinterConfig(` to `export function PerPrinterConfig(`. Then, inside the 2-column grid (~line 475), make the **Filament** column conditional: when the printer has ≥2 loaded slots, render a tool picker instead of the filament ask. Replace the `<div>` holding the `<label className="label">Filament</label>` block with:

```tsx
        {(printer.loaded_filaments?.length ?? 0) >= 2 ? (
          <div>
            <label className="label">Tool</label>
            <select
              data-testid="tool-select"
              className="select"
              value={config.toolIndex ?? ''}
              onChange={e => {
                const v = e.target.value;
                if (v === '') { onChange({ toolIndex: null }); return; }
                const ti = Number(v);
                const s = printer.loaded_filaments[ti];
                onChange({
                  toolIndex: ti,
                  filamentProfile: s?.filament_profile ?? null,
                  filamentId: null,
                  filamentType: s?.type ?? null,
                  filamentColor: s?.color ?? null,
                });
              }}>
              <option value="">— select tool —</option>
              {printer.loaded_filaments.map((s, i) => (
                <option key={i} value={i}>
                  T{i} · {s.type || '—'}{s.name ? ` (${s.name})` : ''}
                </option>
              ))}
            </select>
            <div className="tiny muted" style={{ marginTop: 4 }}>
              Prints on this physical tool; its loaded filament profile is used to slice.
            </div>
          </div>
        ) : (
          <div>
            <label className="label">Filament</label>
            {/* ...existing Spoolman/manual filament ask block, unchanged... */}
          </div>
        )}
```

Keep the entire existing Filament-ask JSX inside the `else` branch unchanged.

**d.** In the `createJob` payload assembly (~line 1173), add `tool_index` to each per-printer config:

```tsx
          printer_configs: cfg.selectedPrinters.map(pid => ({
            printer_id: Number(pid),
            print_profile: cfg.perPrinter[pid].printProfile!,
            filament_profile: cfg.perPrinter[pid].filamentProfile,
            filament_id: cfg.perPrinter[pid].filamentId,
            filament_type: cfg.perPrinter[pid].filamentType,
            filament_color: cfg.perPrinter[pid].filamentColor,
            tool_index: cfg.perPrinter[pid].toolIndex ?? null,
          })),
```

(Match the existing keys already present in that object; add only the `tool_index` line. The exact sibling keys are at `NewJobScreen.tsx:1174-1178`.)

- [ ] **Step 4: Run — confirm PASS + build**

Run: `cd frontend && npx vitest run src/screens/NewJobScreen.test.tsx` → PASS.
Then `cd frontend && npm run build` → clean (tsc -b + vite). Then run the full frontend suite: `npx vitest run` → green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/queue.ts frontend/src/screens/NewJobScreen.tsx frontend/src/screens/NewJobScreen.test.tsx
git commit -m "feat(newjob): tool picker for multi-slot printers; send tool_index"
```

---

## Task 8: Docs sync

**Model: Sonnet** (skill-driven).

Run `themis-docs-sync` against this branch's diff. Update:
- `docs/agent/printers.md` — slicing section: single-filament tool selection routes via **per-object `extruder` metadata** in `model_settings.config` (`extruder = tool_index+1`, 1-based), injected by `mesh_3mf_builder` (`stl_to_3mf`/`build_sliceable_3mf`); NOT `filament_map` (spike-disproven). The queue resolves the slot by `tool_index`. The Snapmaker vendor note's "Project 2" line → now partially delivered (single-filament tool pick), multi-material model→tool mapping still Project 2b.
- `docs/agent/data-model.md` — `job_printer_configs.tool_index` (nullable int, 0-based tool/slot; `None` = default/legacy) + the new `_migrate` guard.

Commit `docs(agent): sync for single-filament tool selection`.

---

## Final verification
After all tasks: `cd backend && backend\.venv\Scripts\python.exe -m pytest -q` (green) and `cd frontend && npm run build` + `npx vitest run` (green). Then, when the U1 is free: queue a single-material job picking T2 vs T3 and confirm the print runs on the chosen tool. Multi-material model→tool mapping is Project 2b (separate spec).

## Self-review notes (author) — revised for Approach C
- **Spec coverage:** spike DONE → Approach C (T1); object-`extruder` injection in `mesh_3mf_builder` (T2); `SliceRequest.tool_index` → 3MF prep (T3); model+migration (T4); create-route (T5); queue slot-by-index + gating + pass-through (T6); New Job ≥2-slot tool picker + payload (T7); docs (T8). All spec sections mapped.
- **Type/name consistency:** `tool_index` (snake, backend: model/SliceRequest/PrinterConfigInput) vs `toolIndex` (camel, frontend `PerPrinterCfg`) — deliberate per-layer; the API boundary key is `tool_index`. 0-based everywhere; `extruder = tool_index+1` the only 1-based conversion (T2, in `model_settings.config`). `_slot_for_config` used in both T6 helper + `_filament_mismatch`.
- **Mechanism:** verified per-object `extruder` metadata (not `filament_map`). `build_project_config` is untouched — its existing filament-array padding to `n_extruders` is the load-bearing dependency (don't remove it).
- **Haiku-safety:** T3–T5 are mechanical with complete code + exact line anchors. T1 (done), T2 (XML/3MF), T6 (queue), T7 (UI), T8 (docs) are Sonnet.
- **Backward compatibility:** every change keys off `tool_index is not None`; `None` (all existing rows, single-tool printers) → byte-identical slice output and current queue behavior — re-asserted by T2/T3/T6 tests.
