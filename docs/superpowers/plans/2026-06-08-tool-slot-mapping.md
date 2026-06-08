# Single-Filament Tool Selection (Snapmaker U1) — Implementation Plan (Project 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let a job pick which physical tool/extruder (T0–T3) a single-material print runs on, on multi-tool printers like the Snapmaker U1, by binding the slice's filament to the chosen extruder via OrcaSlicer `filament_map`.

**Architecture:** A new nullable `tool_index` on `JobPrinterConfig` flows New Job → create-route → queue → `SliceRequest` → `build_project_config`, which sets `filament_map = [tool_index + 1]` for multi-extruder profiles. New Job shows a tool picker for any printer with ≥2 loaded slots; the picked slot supplies the filament profile/colour and the queue resolves `loaded_filaments[tool_index]` directly. A **verification spike** (Task 1) confirms `filament_map` routing before the rest is built.

**Tech Stack:** Python/FastAPI/SQLAlchemy/aiosqlite, OrcaSlicer CLI, React/Vite/TS, pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-06-08-tool-slot-mapping-design.md`. **Branch:** `tool-slot-mapping` (worktree at `C:\Users\mgome\Documents\projects\themis-tool-mapping`).

## Conventions (give to every subagent)
- This is a **git worktree**; run everything from `C:\Users\mgome\Documents\projects\themis-tool-mapping`. Do NOT cd to the other checkout.
- Backend tests: `cd backend && backend\.venv\Scripts\python.exe -m pytest <path> -v`. **The worktree has no `.venv` yet** — Task 1 creates it (`python -m venv .venv` from the **python.org** interpreter, then `pip install -e ".[dev]"`). Subsequent tasks reuse it.
- Frontend: `cd frontend && npm install` (first time), `npm run build` (tsc -b — the real typecheck), `npx vitest run <file>`.
- Commit after each task. Do NOT push.
- `tool_index` is **0-based** (T0–T3, = loaded-slot index). OrcaSlicer `filament_map` is **1-based** → `filament_map = [str(tool_index + 1)]`.

## Model tuning
**Task 1** (spike) and **Tasks 6, 7, 8** are **Sonnet** (judgment/integration/UI/docs). **Tasks 2, 3, 4, 5** are **Haiku** (mechanical, complete code).

## File structure
- Spike: `backend/scripts/spike_filament_map.py` (Task 1, throwaway helper, kept for re-runs).
- `backend/app/services/project_config_builder.py` — `filament_map` (Task 2).
- `backend/app/services/slicer_service.py` — `SliceRequest.tool_index` + forward (Task 3).
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

## Task 2: `build_project_config` sets `filament_map` from `tool_index`

**Model: Haiku.** (Assumes Task 1 = Approach A.)

**Files:**
- Modify: `backend/app/services/project_config_builder.py`
- Test: `backend/tests/services/test_project_config_builder.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/services/test_project_config_builder.py`:

```python
from app.services.project_config_builder import build_project_config


def _multi_extruder_machine():
    # 4-nozzle machine: n_extruders derives from len(nozzle_diameter).
    return {"name": "U1", "printer_model": "U1",
            "nozzle_diameter": ["0.4", "0.4", "0.4", "0.4"]}


def _filament():
    return {"name": "PLA", "filament_type": ["PLA"]}


def test_filament_map_set_for_tool_index_on_multi_extruder():
    cfg = build_project_config(_multi_extruder_machine(), {"name": "proc"},
                               [_filament()], None, plate_count=1, tool_index=2)
    assert cfg["filament_map"] == ["3"]  # 0-based tool 2 -> 1-based extruder 3


def test_filament_map_untouched_when_tool_index_none():
    cfg = build_project_config(_multi_extruder_machine(), {"name": "proc"},
                               [_filament()], None, plate_count=1, tool_index=None)
    # default tool_index leaves whatever the reference default is; not forced to a slot.
    assert cfg.get("filament_map") != ["3"]


def test_filament_map_untouched_on_single_extruder():
    machine = {"name": "Mono", "printer_model": "Mono", "nozzle_diameter": ["0.4"]}
    cfg = build_project_config(machine, {"name": "proc"}, [_filament()], None,
                               plate_count=1, tool_index=2)
    assert cfg.get("filament_map") != ["3"]
```

- [ ] **Step 2: Run — confirm FAIL**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_project_config_builder.py -k filament_map -v`
Expected: FAIL — `build_project_config() got an unexpected keyword argument 'tool_index'`.

- [ ] **Step 3: Implement**

In `backend/app/services/project_config_builder.py`, change the `build_project_config` signature (around line 202) to add the param:

```python
def build_project_config(
    machine: dict,
    process: dict,
    filaments: list[dict],
    filament_colours: list[str] | None = None,
    plate_count: int = 1,
    tool_index: int | None = None,
) -> dict:
```

Then, inside the existing `if n_extruders > 1:` block (after the `printer_extruder_id`/`filament_self_index` lines, ~line 268), add:

```python
        # Route the (single) filament to the chosen physical extruder/tool.
        # tool_index is 0-based (T0-T3); OrcaSlicer filament_map is 1-based.
        if tool_index is not None:
            config["filament_map"] = [str(tool_index + 1)]
```

Update `project_config_json` (the wrapper just below `build_project_config`) to forward the param:

```python
def project_config_json(machine, process, filaments, filament_colours=None, plate_count=1, tool_index=None) -> str:
    return json.dumps(build_project_config(machine, process, filaments, filament_colours, plate_count, tool_index))
```

- [ ] **Step 4: Run — confirm PASS + full suite**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_project_config_builder.py -v` → PASS.
Then `cd backend && backend\.venv\Scripts\python.exe -m pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/project_config_builder.py backend/tests/services/test_project_config_builder.py
git commit -m "feat(slice): build_project_config sets filament_map from tool_index (multi-extruder)"
```

---

## Task 3: Thread `tool_index` through `SliceRequest`

**Model: Haiku.**

**Files:**
- Modify: `backend/app/services/slicer_service.py`
- Test: `backend/tests/services/test_slicer_service.py` (create if absent, else extend)

- [ ] **Step 1: Write the failing test**

Create/extend `backend/tests/services/test_slicer_service.py`:

```python
from unittest.mock import patch
from app.services.slicer_service import SlicerService, SliceRequest


def _req(**kw):
    base = dict(job_id=1, source_3mf="x.3mf", plate_number=0, machine_preset="M",
                process_preset="P", filament_presets=["F"])
    base.update(kw)
    return SliceRequest(**base)


def test_slice_request_has_tool_index_default_none():
    assert _req().tool_index is None
    assert _req(tool_index=2).tool_index == 2


def test_build_config_forwards_tool_index():
    svc = SlicerService.__new__(SlicerService)  # skip __init__ (no orca needed)
    svc._resolver = type("R", (), {"resolve": staticmethod(lambda name, kind: {"name": name})})()
    svc._data_dir = None
    with patch("app.services.slicer_service.build_project_config") as bpc:
        svc._build_config(_req(source_3mf="x.obj", tool_index=2))
        assert bpc.call_args.kwargs.get("tool_index") == 2 or bpc.call_args.args[-1] == 2
```

- [ ] **Step 2: Run — confirm FAIL**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_slicer_service.py -v`
Expected: FAIL — `SliceRequest` has no `tool_index` / not forwarded.

- [ ] **Step 3: Implement**

In `backend/app/services/slicer_service.py`, add the field to `SliceRequest` (after `export_args`, ~line 43):

```python
    export_args: list[str] = field(default_factory=list)
    tool_index: int | None = None
```

In `_build_config` (the final return, ~line 103), forward it:

```python
        return build_project_config(machine, process, filaments, req.filament_colours or None,
                                    plate_count=plate_count, tool_index=req.tool_index)
```

- [ ] **Step 4: Run — confirm PASS + full suite**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_slicer_service.py -v` → PASS. Then `... -m pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/slicer_service.py backend/tests/services/test_slicer_service.py
git commit -m "feat(slice): SliceRequest.tool_index forwarded to build_project_config"
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
- `docs/agent/printers.md` — slicing section: `filament_map = [tool_index+1]` for single-filament tool selection on multi-extruder profiles; the queue resolves the slot by `tool_index`; the Snapmaker vendor note's "Project 2" line → now partially delivered (single-filament tool pick), multi-material model→tool mapping still Project 2b.
- `docs/agent/data-model.md` — `job_printer_configs.tool_index` (nullable int, 0-based tool/slot; `None` = default/legacy) + the new `_migrate` guard.

Commit `docs(agent): sync for single-filament tool selection`.

---

## Final verification
After all tasks: `cd backend && backend\.venv\Scripts\python.exe -m pytest -q` (green) and `cd frontend && npm run build` + `npx vitest run` (green). Then, when the U1 is free: queue a single-material job picking T2 vs T3 and confirm the print runs on the chosen tool. Multi-material model→tool mapping is Project 2b (separate spec).

## Self-review notes (author)
- **Spec coverage:** mechanism/spike (T1), `filament_map` (T2), `SliceRequest.tool_index` (T3), model+migration (T4), create-route (T5), queue slot-by-index + gating + pass-through (T6), New Job ≥2-slot tool picker + payload (T7), docs (T8). All spec sections mapped. The Approach-B fallback is explicit in T1 step 4.
- **Type/name consistency:** `tool_index` (snake, backend: model/SliceRequest/PrinterConfigInput/build_project_config) vs `toolIndex` (camel, frontend `PerPrinterCfg`) — deliberate per-layer naming; the API boundary key is `tool_index` (queue.ts `PrinterConfigInput` + payload). 0-based everywhere; `filament_map=[tool_index+1]` the only 1-based conversion (T2). `_slot_for_config` used in both T6 helper + `_filament_mismatch`.
- **Haiku-safety:** T2–T5 are mechanical with complete code + exact line anchors. T1/T6/T7/T8 are Sonnet (slicer/queue/UI/docs judgment).
- **Backward compatibility:** every change keys off `tool_index is not None`; `None` (all existing rows, single-tool printers) preserves current behavior exactly — explicitly re-asserted by T2/T6 tests.
