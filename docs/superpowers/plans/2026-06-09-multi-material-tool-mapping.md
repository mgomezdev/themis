# Multi-Material Model → Tool Mapping — Implementation Plan (Sub-project B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let a job map each filament declared in a multi-material 3MF to a physical printer tool (T0–T3); the slice reroutes each model filament's regions to its mapped tool and slices each tool with that tool's loaded material.

**Architecture:** Parse the model's declared filaments from `project_settings.config`; store a `filament_map` JSON (`[{model_filament, tool_index}]`) on `JobPrinterConfig` (generalizing Project 2's single `tool_index`); the queue passes the printer's loaded-slot profiles + the map into `SliceRequest`; `mesh_3mf_builder` applies a **spike-chosen remap** (lead candidate: rewrite the plate `filament_maps` array). A mapping list in the shared `PerPrinterConfig` (one row per declared filament → tool) drives it from New + Edit Job.

**Tech Stack:** Python/FastAPI/SQLAlchemy/aiosqlite, OrcaSlicer CLI, React/Vite/TS, pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-06-09-multi-material-tool-mapping-design.md`. **Branch:** `worktree-multi-material-tool-mapping` (worktree `C:\Users\mgome\Documents\projects\themis\.claude\worktrees\multi-material-tool-mapping`).

## Conventions (every subagent)
- Work ONLY in the worktree; absolute paths. **First backend task creates the venv** (`cd backend && python -m venv .venv` from the **python.org** interpreter, then `.venv\Scripts\python.exe -m pip install -e ".[dev]"`); later backend tasks reuse it. **First frontend task runs `npm install`.**
- Backend tests: `cd backend && backend\.venv\Scripts\python.exe -m pytest <path> -v`. Frontend: `cd frontend && npx vitest run <file>`; `npm run build` (tsc -b). `noUnusedLocals` ON.
- Commit after each task; do NOT push. TDD.
- `model_filament` is **1-based** (the 3MF filament index). `tool_index` is **0-based** (physical tool / loaded-slot index).

## STATUS / model tuning
**Task 1 (spike) is the gate** — it decides the remap mechanism; **Task 5 (remap) must not be implemented until Task 1 resolves.** Tasks 1, 5, 6, 7, 8 are **Sonnet** (spike/slice/queue/UI/docs). Tasks 2, 3, 4 are **Haiku** (mechanical). Tasks 2/3/4/7 do NOT depend on the spike outcome and may proceed in parallel with deciding Task 5.

## File structure
- `backend/scripts/spike_filament_remap.py` (Task 1).
- `backend/app/services/three_mf_parser.py` — `parse_model_filaments` (Task 2); `backend/app/api/routes/files.py` — `GET /{id}/model-filaments` (Task 2).
- `backend/app/models.py` + `database.py` — `job_printer_configs.filament_map` (Task 3).
- `backend/app/api/routes/jobs.py` — `PrinterConfigInput.filament_map` + persist + round-trip; `backend/app/services/slicer_service.py` — `SliceRequest.filament_map` (Task 4).
- `backend/app/services/mesh_3mf_builder.py` — the remap (Task 5).
- `backend/app/services/queue_engine.py` — pass profiles + map + gating (Task 6).
- `frontend/src/components/PerPrinterConfig.tsx` + `frontend/src/api/queue.ts` + New/Edit screens (Task 7).

---

## Task 1: Spike — which remap mechanism reroutes a painted model?

**Model: Sonnet.** Exploratory (not TDD). **Gates Task 5.**

**Goal:** For a painted multi-material U1 3MF, prove whether rewriting the plate `filament_maps` array reroutes the painted regions to different physical extruders in the emitted gcode (mechanism a). If not, assess rewriting the per-triangle paint filament references (mechanism b, what OrcaSlicer's paint→remap does).

**Fixture:** the real painted file `C:\Users\mgome\Downloads\Hausdeko+#41+-+Welcome+Home+-+Türschild+-+Makerworld.3mf` (single object, ~9049 painted triangles, declares 4 filaments, plate `filament_maps="1 2 3 4"`).

**Files:** Create `backend/scripts/spike_filament_remap.py`.

- [ ] **Step 1: Create the worktree backend venv** (as in Conventions).

- [ ] **Step 2: Write the spike script**

The script must, for each `filament_maps` variant, produce a sliceable 3MF and slice it, then report which extruders the gcode uses. Approach:
1. Resolve the U1 machine/process presets (`Snapmaker U1 (0.4 nozzle)`, `0.08 Extra Fine @Snapmaker U1 (0.4 nozzle)`) and 4 filament presets (e.g. `Generic PLA High Speed @System` ×4) via `PresetResolver`; build a config with `build_project_config(machine, process, [f1,f2,f3,f4], colours, plate_count=1)`.
2. Copy the Hausdeko 3MF, **replacing only `Metadata/model_settings.config`** so its plate `filament_maps` is the variant under test (parse the existing model_settings XML, set the `<metadata key="filament_maps" value="...">`), and replacing `Metadata/project_settings.config` with the built config. (Mirror `build_sliceable_3mf`'s copy loop, but also rewrite the filament_maps metadata.)
3. Slice with the OrcaSlicer CLI (mirror `slicer_service._run`: `[orca, "--slice", "0", "--outputdir", out, "--arrange", "1", prepared]`).
4. Grep the gcode: count occurrences of `T0`/`T1`/`T2`/`T3` and the set of extruders that heat (`M104 T<n>`/`M109 T<n>`). Report per variant.

Variants to slice: `filament_maps="1 2 3 4"` (identity) and `filament_maps="2 1 4 3"` (swapped). If the **set/order of tools used changes** between the two → mechanism (a) reroutes (filament_maps works). If identical → mechanism (a) does NOT reroute painted regions.

If (a) fails: inspect the `paint_color` encoding in `3D/Objects/*.model` and assess whether the logical filament index can be rewritten in the paint data (mechanism b) — document the encoding findings and feasibility; do NOT implement it in the spike.

- [ ] **Step 3: Run + record the verdict**

Run the script. Append a "Spike result (2026-06-09)" section to the spec (`docs/superpowers/specs/2026-06-09-multi-material-tool-mapping-design.md`): the per-variant tool usage, and the **VERDICT**:
- **(a) works** → Task 5 implements `filament_maps` rewrite. Proceed.
- **(a) fails, (b) feasible** → Task 5 implements paint-reference rewrite; note the encoding. Re-plan Task 5 with the controller before implementing.
- **(a) fails, (b) infeasible** → STOP. Escalate to the controller/user to re-scope (e.g. require the remap in OrcaSlicer before upload).

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/spike_filament_remap.py docs/superpowers/specs/2026-06-09-multi-material-tool-mapping-design.md
git commit -m "spike(multi-material): determine the filament->tool remap mechanism"
```

---

## Task 2: Parse the model's declared filaments + expose via API

**Model: Haiku.** (Independent of the spike.)

**Files:**
- Modify: `backend/app/services/three_mf_parser.py`, `backend/app/api/routes/files.py`
- Test: `backend/tests/services/test_model_filaments.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_model_filaments.py
import io, json, zipfile
from app.services.three_mf_parser import parse_model_filaments


def _mk_3mf(tmp_path, project_settings: dict):
    p = tmp_path / "m.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        z.writestr("Metadata/project_settings.config", json.dumps(project_settings))
    return str(p)


def test_parse_model_filaments_multi(tmp_path):
    f = _mk_3mf(tmp_path, {
        "filament_colour": ["#FFFFFF", "#F78E0E", "#003776"],
        "filament_type": ["PLA", "PLA", "PETG"],
    })
    out = parse_model_filaments(f)
    assert out == [
        {"index": 1, "color": "#FFFFFF", "type": "PLA"},
        {"index": 2, "color": "#F78E0E", "type": "PLA"},
        {"index": 3, "color": "#003776", "type": "PETG"},
    ]


def test_parse_model_filaments_single(tmp_path):
    f = _mk_3mf(tmp_path, {"filament_colour": ["#888888"], "filament_type": ["PLA"]})
    assert len(parse_model_filaments(f)) == 1


def test_parse_model_filaments_none_when_absent(tmp_path):
    f = _mk_3mf(tmp_path, {})
    assert parse_model_filaments(f) == []
```

- [ ] **Step 2: Run — confirm FAIL**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_model_filaments.py -v` → FAIL (no `parse_model_filaments`).

- [ ] **Step 3: Implement**

In `backend/app/services/three_mf_parser.py`, add (it already imports `zipfile`, `json`):

```python
def parse_model_filaments(file_path: str) -> list[dict]:
    """The filaments a 3MF declares: [{index(1-based), color, type}], from
    project_settings.config (filament_colour / filament_type). [] if none/not a 3MF."""
    try:
        with zipfile.ZipFile(file_path) as zf:
            if "Metadata/project_settings.config" not in zf.namelist():
                return []
            ps = json.loads(zf.read("Metadata/project_settings.config"))
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, OSError):
        return []
    colours = ps.get("filament_colour") or []
    types = ps.get("filament_type") or []
    out = []
    for i, colour in enumerate(colours):
        out.append({"index": i + 1, "color": colour,
                    "type": types[i] if i < len(types) else ""})
    return out
```

In `backend/app/api/routes/files.py`, add after `get_plates` (~line 343). The `UploadedFile` row has `stored_path` (the on-disk path; confirm the attribute name by reading the model — it's used elsewhere in this file as the absolute path):

```python
@router.get("/{file_id}/model-filaments")
async def get_model_filaments(file_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    from ...services.three_mf_parser import parse_model_filaments
    record = await session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File {file_id} not found")
    return parse_model_filaments(record.stored_path)
```

(Read `files.py` to confirm how other handlers resolve the absolute path from an `UploadedFile` — use the same attribute, e.g. `record.stored_path`.)

- [ ] **Step 4: Run — confirm PASS + full suite**

Run the new test → PASS. Then `... -m pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/three_mf_parser.py backend/app/api/routes/files.py backend/tests/services/test_model_filaments.py
git commit -m "feat(files): parse declared model filaments + GET /files/{id}/model-filaments"
```

---

## Task 3: `JobPrinterConfig.filament_map` column + migration

**Model: Haiku.**

**Files:** Modify `backend/app/models.py`, `backend/app/database.py`. Test: `backend/tests/test_filament_map_migration.py` (create).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_filament_map_migration.py
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.database import _migrate, Base


@pytest.mark.asyncio
async def test_migrate_adds_filament_map_idempotently():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate(conn)
        await _migrate(conn)
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(job_printer_configs)"))).fetchall()}
    assert "filament_map" in cols
    await engine.dispose()
```

- [ ] **Step 2: Run — confirm FAIL.** `... -m pytest tests/test_filament_map_migration.py -v`.

- [ ] **Step 3: Implement**

`models.py` — in `JobPrinterConfig`, after `tool_index`:
```python
    filament_map: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
```
(Confirm `JSON` is imported in `models.py` — `loaded_filaments` uses it; reuse that import.)

`database.py` — in `_migrate`, in the `if jpc_cols:` block, after the `tool_index` guard:
```python
        if "filament_map" not in jpc_cols:
            await conn.execute(text("ALTER TABLE job_printer_configs ADD COLUMN filament_map JSON"))
```

- [ ] **Step 4: Run — PASS + full suite.**

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/app/database.py backend/tests/test_filament_map_migration.py
git commit -m "feat(model): JobPrinterConfig.filament_map column + idempotent migration"
```

---

## Task 4: Plumb `filament_map` — `SliceRequest` + create/edit job routes

**Model: Haiku.**

**Files:** Modify `backend/app/services/slicer_service.py`, `backend/app/api/routes/jobs.py`. Test: `backend/tests/api/test_filament_map_plumbing.py` (create).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_filament_map_plumbing.py
import inspect
from app.services.slicer_service import SliceRequest
from app.api.routes import jobs
from app.api.routes.jobs import PrinterConfigInput


def test_slice_request_has_filament_map_default_none():
    req = SliceRequest(job_id=1, source_3mf="x", plate_number=0, machine_preset="M",
                       process_preset="P", filament_presets=["F"])
    assert req.filament_map is None
    assert SliceRequest(job_id=1, source_3mf="x", plate_number=0, machine_preset="M",
                        process_preset="P", filament_presets=["F"],
                        filament_map=[{"model_filament": 1, "tool_index": 2}]).filament_map is not None


def test_printer_config_input_accepts_filament_map():
    c = PrinterConfigInput(printer_id=1, print_profile="p",
                           filament_map=[{"model_filament": 1, "tool_index": 2}])
    assert c.filament_map[0]["tool_index"] == 2
    assert PrinterConfigInput(printer_id=1, print_profile="p").filament_map is None


def test_job_routes_round_trip_filament_map():
    assert "filament_map=cfg.filament_map" in inspect.getsource(jobs.create_job)
    assert "filament_map=cfg.filament_map" in inspect.getsource(jobs.update_job_configs)
    assert '"filament_map"' in inspect.getsource(jobs.get_job_details)
```

- [ ] **Step 2: Run — confirm FAIL.**

- [ ] **Step 3: Implement**

`slicer_service.py` — add to `SliceRequest` (after `tool_index`):
```python
    filament_map: list | None = None
```

`jobs.py`:
- `PrinterConfigInput` — after `tool_index`: `filament_map: list | None = None`.
- `create_job` and `update_job_configs` `JobPrinterConfig(...)` constructors — add `filament_map=cfg.filament_map,`.
- `get_job_details` `printer_configs.append({...})` — add `"filament_map": cfg.filament_map,`.

- [ ] **Step 4: Run — PASS + full suite.**

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/slicer_service.py backend/app/api/routes/jobs.py backend/tests/api/test_filament_map_plumbing.py
git commit -m "feat(jobs): plumb filament_map through SliceRequest + create/edit routes"
```

---

## Task 5: Slice remap in `mesh_3mf_builder` (GATED on Task 1)

**Model: Sonnet.** **Do not start until Task 1's verdict is recorded.** This task is written for **mechanism (a): rewrite the plate `filament_maps`.** If Task 1 chose mechanism (b), STOP and have the controller re-plan this task for the paint-reference rewrite (the data model, queue, and UI tasks are unaffected).

**Files:** Modify `backend/app/services/mesh_3mf_builder.py`, `backend/app/services/slicer_service.py` (forward the map). Test: extend `backend/tests/services/test_mesh_3mf_builder.py`.

- [ ] **Step 1: Write the failing test**

```python
def test_filament_maps_rewritten_from_filament_map(tmp_path):
    # source 3MF with a plate filament_maps="1 2 3 4"; remap model filament 2 -> tool 3 (1-based extruder 4)
    import zipfile
    src = tmp_path / "src.3mf"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        z.writestr("Metadata/project_settings.config", '{"old":1}')
        z.writestr("Metadata/model_settings.config",
                   '<?xml version="1.0"?>\n<config><plate>'
                   '<metadata key="filament_maps" value="1 2 3 4"/></plate></config>')
    out = tmp_path / "out.3mf"
    from app.services.mesh_3mf_builder import build_sliceable_3mf
    build_sliceable_3mf(str(src), {"new": 1}, out,
                        filament_map=[{"model_filament": 2, "tool_index": 3}])
    with zipfile.ZipFile(out) as z:
        ms = z.read("Metadata/model_settings.config").decode("utf-8")
    # position 2 (model filament 2, 1-based) becomes 4 (tool_index 3 + 1)
    assert 'value="1 4 3 4"' in ms
```

- [ ] **Step 2: Run — confirm FAIL.**

- [ ] **Step 3: Implement**

In `mesh_3mf_builder.py`, add a helper that rewrites the plate `filament_maps` per the map (default identity for unmapped positions), preserving the count:

```python
def _remap_filament_maps(model_settings: bytes, filament_map: list, n: int) -> bytes:
    """Rewrite each plate's <metadata key="filament_maps"> so model filament k
    (1-based) routes to tool_index m (0-based) -> extruder m+1. Unmapped stay identity."""
    import xml.etree.ElementTree as ET
    mapping = {e["model_filament"]: e["tool_index"] + 1 for e in (filament_map or [])}
    root = ET.fromstring(model_settings)
    for plate in root.findall("plate"):
        for md in plate.findall("metadata"):
            if md.get("key") == "filament_maps":
                count = len(md.get("value", "").split()) or n
                md.set("value", " ".join(str(mapping.get(i + 1, i + 1)) for i in range(count)))
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + body).encode("utf-8")
```

Add `filament_map: list | None = None` to `build_sliceable_3mf` (and `stl_to_3mf` for signature parity — STL has no plate, so it's a no-op there). In `build_sliceable_3mf`, when `filament_map` is set, capture the source `model_settings.config` (as the `tool_index` path already does) and write `_remap_filament_maps(src_model_settings, filament_map, n_filaments)` instead of (or alongside) the single-tool patch. `tool_index` (single) and `filament_map` (multi) are mutually exclusive — if `filament_map` is set, use the remap path; else the existing `tool_index` path. Derive `n_filaments` from the source's filament count if needed, else the max model_filament in the map.

In `slicer_service.py` `slice()`, forward `req.filament_map` to `build_sliceable_3mf` (both call sites) and `stl_to_3mf`.

- [ ] **Step 4: Run — PASS + full suite** (the `tool_index=None`/`filament_map=None` paths must stay byte-identical — existing mesh tests green).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/mesh_3mf_builder.py backend/app/services/slicer_service.py backend/tests/services/test_mesh_3mf_builder.py
git commit -m "feat(slice): remap plate filament_maps from a model-filament->tool map"
```

---

## Task 6: Queue — pass loaded-slot profiles + map; gate on mapped tools

**Model: Sonnet.**

**Files:** Modify `backend/app/services/queue_engine.py`. Test: `backend/tests/services/test_queue_filament_map.py` (create).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_queue_filament_map.py
from types import SimpleNamespace
from app.services.queue_engine import _filament_mismatch, _mapped_tools_loaded


LOADED = [{"slot": i, "type": "PLA", "color": "#fff", "filament_profile": f"P{i}"} for i in range(3)]


def _cfg(**kw):
    base = dict(filament_type=None, filament_color=None, tool_index=None, filament_map=None)
    base.update(kw); return SimpleNamespace(**base)


def test_mapped_tools_loaded():
    assert _mapped_tools_loaded([{"model_filament": 1, "tool_index": 2}], LOADED) is True
    assert _mapped_tools_loaded([{"model_filament": 1, "tool_index": 9}], LOADED) is False


def test_filament_map_gates_on_mapped_tools():
    assert _filament_mismatch(_cfg(filament_map=[{"model_filament": 1, "tool_index": 2}]), LOADED) is None
    assert _filament_mismatch(_cfg(filament_map=[{"model_filament": 1, "tool_index": 9}]), LOADED) is not None
```

- [ ] **Step 2: Run — confirm FAIL.**

- [ ] **Step 3: Implement**

In `queue_engine.py`, add:
```python
def _mapped_tools_loaded(filament_map: list, loaded: list) -> bool:
    loaded = loaded or []
    return all(0 <= e["tool_index"] < len(loaded) for e in (filament_map or []))
```
Extend `_filament_mismatch` — at the top, before the `tool_index` branch:
```python
    fmap = getattr(config, "filament_map", None)
    if fmap:
        return None if _mapped_tools_loaded(fmap, loaded) else "a mapped tool has no loaded filament"
```
In `_run_slice_and_print`: when `config.filament_map` is set, build `filament_presets` from the printer's loaded slots ordered by physical tool index (`[s.get("filament_profile") for s in sorted(loaded, key=lambda s: s["slot"])]`), and pass `filament_map=config.filament_map` into the `SliceRequest`. (When `filament_map` is None, the existing single-filament/`tool_index` path is unchanged.)

- [ ] **Step 4: Run — PASS + full suite** (existing queue tests green; `filament_map=None` path unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/queue_engine.py backend/tests/services/test_queue_filament_map.py
git commit -m "feat(queue): pass loaded-slot profiles + filament_map to slice; gate on mapped tools"
```

---

## Task 7: Mapping UI in the shared `PerPrinterConfig`

**Model: Sonnet.**

**Files:** Modify `frontend/src/components/PerPrinterConfig.tsx`, `frontend/src/api/queue.ts`, `frontend/src/screens/NewJobScreen.tsx`, `frontend/src/screens/EditJobScreen.tsx`. Test: `frontend/src/components/PerPrinterConfig.test.tsx` (extend).

- [ ] **Step 1: API + types**

In `queue.ts`: add `export interface ModelFilament { index: number; color: string; type: string }` and `export async function getModelFilaments(fileId: number): Promise<ModelFilament[]> { return request(\`/api/v1/files/${fileId}/model-filaments\`); }`. Add `filament_map?: {model_filament:number; tool_index:number}[] | null` to `PrinterConfigInput` and `ApiJobPrinterConfig`.

- [ ] **Step 2: Component — accept model filaments + render mapping rows**

Extend `PerPrinterCfg` with `filamentMap: {model_filament:number; tool_index:number}[] | null` (default `null` in `defaultPerPrinterCfg`). Add a `modelFilaments?: ModelFilament[]` prop to `PerPrinterConfig`. When `modelFilaments` has > 1 entry AND the printer has loaded slots, render a **mapping list** (one row per model filament: a colour swatch + `Filament {index}` + a tool `<select data-testid={`map-tool-${index}`}>` of the printer's slots, default identity `tool_index = index-1` clamped to slot count) instead of the single tool/defer control; each change updates `filamentMap`. Otherwise render the existing single-tool/defer control (Sub-project A).

Add a vitest case: a printer with ≥2 slots + `modelFilaments` of length 3 renders three `map-tool-*` selects; changing `map-tool-2` writes `filamentMap` with `{model_filament:2, tool_index:<chosen>}`.

- [ ] **Step 3: Wire New + Edit Job**

Both screens: fetch `getModelFilaments(uploadedFileId)` when a file is selected, hold it in state, pass `modelFilaments={...}` to `PerPrinterConfig`. Include `filament_map: cfg.filamentMap ?? null` in their `createJob`/`updateJobConfigs` payloads. Edit Job pre-fills `filamentMap: c.filament_map ?? null`. Initialize `filamentMap: null` in default literals (use `defaultPerPrinterCfg()`).

- [ ] **Step 4: Build + tests**

`cd frontend && npm run build` → clean. `npx vitest run` → green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/PerPrinterConfig.tsx frontend/src/api/queue.ts frontend/src/screens/NewJobScreen.tsx frontend/src/screens/EditJobScreen.tsx frontend/src/components/PerPrinterConfig.test.tsx
git commit -m "feat(jobs): multi-material filament->tool mapping UI in shared PerPrinterConfig"
```

---

## Task 8: Docs sync

**Model: Sonnet.** Run `themis-docs-sync`. Update `docs/agent/printers.md` (slicing: `filament_map` remaps plate `filament_maps`; queue passes loaded-slot profiles), `docs/agent/data-model.md` (`job_printer_configs.filament_map`), `docs/agent/backend.md` (`GET /files/{id}/model-filaments`; `parse_model_filaments`), `docs/agent/frontend.md` (`PerPrinterConfig` mapping list; `getModelFilaments`). Commit `docs(agent): sync for multi-material tool mapping`.

---

## Final verification
`cd backend && backend\.venv\Scripts\python.exe -m pytest -q` green; `cd frontend && npm run build` + `npx vitest run` green. Then live (when U1 free): a painted job mapped to specific tools prints each region on the right tool.

## Self-review notes (author)
- **Spec coverage:** spike/mechanism (T1); parse declared filaments + API (T2); `filament_map` column (T3); plumbing through SliceRequest + routes (T4); slice remap (T5, gated); queue profiles + gating (T6); mapping UI in shared component, New+Edit (T7); docs (T8). All spec sections mapped. **Deviation from spec:** model filaments are served **on-demand** via `GET /files/{id}/model-filaments` (parse at request time) rather than persisted on `uploaded_files.model_filaments` — simpler (no uploaded_files migration / scan changes), same UI capability. Noted intentionally.
- **Type/name consistency:** `filament_map` = `[{model_filament(1-based), tool_index(0-based)}]` everywhere (column, SliceRequest, PrinterConfigInput, queue, builder); frontend `filamentMap` (camel) ↔ `filament_map` (snake API). `tool_index` (single) and `filament_map` (multi) mutually exclusive per config — re-asserted in T5/T6.
- **Gating:** T1 gates T5 only; T2/T3/T4/T6/T7 are mechanism-independent. T5 is explicitly conditional on the spike verdict with a re-plan/escalate path.
- **Backward-compat:** every path keys off `filament_map` being non-empty; null preserves Sub-project A + Project 2 behavior.
