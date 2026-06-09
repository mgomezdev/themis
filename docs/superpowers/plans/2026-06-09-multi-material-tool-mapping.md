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

## Task 5: Paint-bitstream remap (mechanism b — RE-PLANNED per Task 1 verdict)

**Spike verdict (Task 1, commit `60d325e`):** rewriting plate `filament_maps` does **NOT** reroute a painted model — OrcaSlicer ignores it. Tool routing lives in the `paint_color` 3-bit tree in `3D/Objects/*.model`: node values **3–6 = filaments 1–4**, **7 = SPLIT** (4 children follow), **0 = NONE** (inherits the object's base `extruder` metadata), **1/2 = support enforcer/blocker** (preserve). So the remap = **rewrite paint leaf nodes + the object's base `extruder` metadata**. Two tasks (5a codec, 5b integration) because the binary codec must be validated in isolation first.

### Task 5a: `paint_remap.py` — decode/encode/remap codec (round-trip validated)

**Model: Sonnet.**

**Files:** Create `backend/app/services/paint_remap.py`, `backend/tests/services/test_paint_remap.py`.

- [ ] **Step 1: Write the failing tests** (the round-trip on REAL data is the spec for bit-order/padding correctness)

```python
# backend/tests/services/test_paint_remap.py
import zipfile, re
from pathlib import Path
from app.services.paint_remap import decode_nodes, encode_nodes, remap_paint_color

_FIXTURE = Path(r"C:/Users/mgome/Downloads/Hausdeko+#41+-+Welcome+Home+-+Türschild+-+Makerworld.3mf")


def _fixture_paint_colors():
    with zipfile.ZipFile(_FIXTURE) as z:
        raw = next(z.read(n).decode("utf-8", "ignore") for n in z.namelist() if n.endswith(".model") and z.read(n))
    return re.findall(r'paint_color="([^"]+)"', raw)


def test_decode_encode_roundtrip_on_real_paint():
    pcs = [p for p in _fixture_paint_colors() if p]
    assert pcs, "fixture has painted triangles"
    for pc in pcs[:200]:
        assert encode_nodes(decode_nodes(pc)) == pc   # exact inverse — validates bit order + padding


def test_remap_swaps_filament_leaf_nodes():
    # nodes 3..6 == filaments 1..4. Map filament 1 -> tool 2 (extruder 3 == node 5); identity elsewhere.
    # mapping arg is {model_filament(1-based): tool_index(0-based)}.
    nodes = [3, 7, 4, 5, 0, 1, 6]          # leaves 3,4,5,6 + SPLIT(7) + NONE(0) + ENFORCER(1)
    out = decode_nodes(encode_nodes([n for n in nodes]))  # sanity
    assert out == nodes
    hexed = encode_nodes(nodes)
    remapped = decode_nodes(remap_paint_color(hexed, {1: 2}))
    assert remapped == [5, 7, 4, 5, 0, 1, 6]   # node 3 (filament1) -> extruder3 -> node 5; others unchanged


def test_remap_identity_is_noop():
    pc = next(p for p in _fixture_paint_colors() if p)
    assert remap_paint_color(pc, {}) == pc
```

- [ ] **Step 2: Run — confirm FAIL.**

- [ ] **Step 3: Implement** `backend/app/services/paint_remap.py`. Reuse the proven decode from the spike (`backend/scripts/spike_filament_remap.py` `_decode_nodes`) and write the EXACT-inverse encoder; the round-trip test on real data is the correctness gate. Functions:
  - `decode_nodes(hex_str) -> list[int]` — 3-bit nodes; LSB-first within each hex-byte; odd-length strings padded with a trailing `'0'`. (Match the spike's `_decode_nodes` exactly — it is proven against the fixture.)
  - `encode_nodes(nodes) -> str` — inverse: pack 3-bit values LSB-first into bytes, emit hex, applying the same trailing-`'0'` padding rule so `encode(decode(pc)) == pc`.
  - `remap_paint_color(hex_str, mapping) -> str` — `mapping` is `{model_filament(1-based): tool_index(0-based)}`. decode → for each node `v` in `3..6`: `model_filament = v - 2`; if in `mapping`, `v = mapping[model_filament] + 3` (tool_index 0-based → extruder 1-based → node value); clamp to `3..6` — preserve `0,1,2,7` → encode.

- [ ] **Step 4: Run — PASS** (esp. the real-data round-trip). Then `... -m pytest -q` green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/paint_remap.py backend/tests/services/test_paint_remap.py
git commit -m "feat(slice): paint_color bitstream codec + filament remap (round-trip validated)"
```

### Task 5b: Apply the remap in `mesh_3mf_builder` + slice() + end-to-end verify

**Model: Sonnet.**

**Files:** Modify `backend/app/services/mesh_3mf_builder.py`, `backend/app/services/slicer_service.py`. Test: extend `backend/tests/services/test_mesh_3mf_builder.py`; add `backend/tests/services/test_filament_map_e2e.py`.

- [ ] **Step 1: Write the failing tests**

```python
# in test_mesh_3mf_builder.py — paint + object-extruder remap on build
def test_build_sliceable_3mf_remaps_paint_and_object_extruder(tmp_path):
    import zipfile
    from app.services.paint_remap import encode_nodes, decode_nodes
    from app.services.mesh_3mf_builder import build_sliceable_3mf
    painted = encode_nodes([3])                      # one triangle on filament 1
    src = tmp_path / "src.3mf"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        z.writestr("3D/Objects/o.model", f'<model><triangle paint_color="{painted}"/></model>')
        z.writestr("Metadata/project_settings.config", '{"old":1}')
        z.writestr("Metadata/model_settings.config",
                   '<?xml version="1.0"?>\n<config><object id="1">'
                   '<metadata key="extruder" value="1"/></object></config>')
    out = tmp_path / "out.3mf"
    build_sliceable_3mf(str(src), {"new": 1}, out,
                        filament_map=[{"model_filament": 1, "tool_index": 2}])  # filament1 -> tool2 (ext3)
    with zipfile.ZipFile(out) as z:
        obj = z.read("3D/Objects/o.model").decode("utf-8")
        ms = z.read("Metadata/model_settings.config").decode("utf-8")
    import re
    pc = re.search(r'paint_color="([^"]+)"', obj).group(1)
    assert decode_nodes(pc) == [5]                   # filament1 -> extruder3 -> node 5
    assert 'key="extruder" value="3"' in ms          # object base extruder remapped too
```

```python
# backend/tests/services/test_filament_map_e2e.py — the real proof (slices; needs OrcaSlicer)
import zipfile
from pathlib import Path
import pytest
_FIXTURE = Path(r"C:/Users/mgome/Downloads/Hausdeko+#41+-+Welcome+Home+-+Türschild+-+Makerworld.3mf")

@pytest.mark.skipif(not _FIXTURE.exists(), reason="fixture not present")
def test_remap_changes_emitted_tool_usage(tmp_path):
    # Reuse the spike's slice+extract helpers to prove the remap actually reroutes in gcode.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from spike_filament_remap import _build_config, _run_slice, _extract_tool_info  # proven helpers
    from app.services.preset_resolver import PresetResolver
    from app.services.mesh_3mf_builder import build_sliceable_3mf
    from app.config import get_orca_executable
    cfg = _build_config(PresetResolver())
    ident = tmp_path / "id.3mf"; swap = tmp_path / "sw.3mf"
    build_sliceable_3mf(str(_FIXTURE), cfg, ident, filament_map=[])
    build_sliceable_3mf(str(_FIXTURE), cfg, swap,
                        filament_map=[{"model_filament": 1, "tool_index": 1}, {"model_filament": 2, "tool_index": 0}])
    a = _extract_tool_info((_run_slice(get_orca_executable(), ident, tmp_path/"a")[0]).read_text(errors="ignore"))
    b = _extract_tool_info((_run_slice(get_orca_executable(), swap,  tmp_path/"b")[0]).read_text(errors="ignore"))
    assert a != b   # remapping the paint changes which tools the gcode uses
```
(Adapt the imported helper names to what `spike_filament_remap.py` actually defines — read it.)

- [ ] **Step 2: Run — confirm FAIL.**

- [ ] **Step 3: Implement** in `mesh_3mf_builder.py`: add `filament_map: list | None = None` to `build_sliceable_3mf` (and `stl_to_3mf` for parity — no-op there). When `filament_map` is set (and non-empty), in the copy loop, for every entry whose name matches `3D/.*\.model` (the geometry, incl. `3D/Objects/*`), rewrite each `paint_color="..."` via `paint_remap.remap_paint_color(pc, {e["model_filament"]: e["tool_index"] for e in filament_map})` before writing it; and set each `<object>`'s base `extruder` metadata in `model_settings.config` to the remapped value (object extruder `e` 1-based → `mapping.get(e, e-1)+1`), reusing the Project-2 model_settings patching approach but per-object via the map. Leave the plate `filament_maps` untouched. `filament_map` and `tool_index` are mutually exclusive (prefer `filament_map` when set). `filament_map=None` ⇒ byte-identical to today.
  In `slicer_service.py` `slice()`, forward `req.filament_map` to `build_sliceable_3mf` (both call sites).

- [ ] **Step 4: Run — PASS + full suite.** The mesh unit test must pass; run the e2e test (it slices — allow a couple minutes; if OrcaSlicer is unavailable it skips). Existing mesh tests (`filament_map=None`) stay byte-identical.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/mesh_3mf_builder.py backend/app/services/slicer_service.py backend/tests/services/test_mesh_3mf_builder.py backend/tests/services/test_filament_map_e2e.py
git commit -m "feat(slice): apply paint+object-extruder remap in mesh_3mf_builder (e2e verified)"
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
