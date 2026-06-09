# Multi-Material Model → Tool Mapping — Design Spec (Sub-project B)

**Date:** 2026-06-09
**Status:** Approved (pending spec review)
**Branch:** `worktree-multi-material-tool-mapping`

## Goal & scope

A multi-material 3MF (prepared in OrcaSlicer) declares N filaments and paints/assigns model regions to
them. Let the user, in the New/Edit Job per-printer config, **map each model filament → a physical printer
tool (T0–T3)** — full remap (any model filament → any tool). The slice reroutes each model filament's
regions to its mapped physical tool and slices each tool with that tool's loaded material.

**Builds on:** Project 2 (single `tool_index`, per-object `extruder` injection, verified), Sub-project A
(shared `PerPrinterConfig` + defer). Generalizes the single `tool_index` to an N-entry map.

**In scope:** parse the model's declared filaments (count/colour/type) from the 3MF; a mapping UI (one row
per model filament → tool dropdown) in the shared component; persist the map; a slice remap that reroutes
filament→tool; per-tool filament profiles from the printer's loaded slots; eligibility gating.

**Out of scope:** decoding the per-triangle paint to hide *unused* declared filaments (we list all declared
filaments; unused mappings are harmless); editing the paint itself; non-OrcaSlicer 3MFs.

## The mechanism is uncertain → spike first (Task 1)

A real multi-material file (`Hausdeko #41`) is a **single painted object**: one `<object id="2">` with a
base `extruder=2`, but **9049/12883 triangles carry `paint_color`** (OrcaSlicer MMU painting). So the
per-region tool assignment is in the **mesh paint data** (referencing logical filament indices) plus the
plate-level `<metadata key="filament_maps" value="1 2 3 4"/>` — **not** per-object `extruder` metadata.
This rules out Project 2's per-object-extruder approach for painted models.

**Supplemental (user):** OrcaSlicer's GUI **paint → remap** ("replace filament for a painted region",
e.g. slot1 → slot3) rewrites the **filament reference inside the per-triangle paint data**. That is
OrcaSlicer's own remap method, and a strong hint that `filament_maps` alone may not reroute painted
regions.

**Task 1 spike** slices a small **painted 2-colour** test 3MF (created in OrcaSlicer for the U1) and
compares remap mechanisms, grepping the gcode for which tool each colour region prints on:
- **(a) `filament_maps` rewrite** — reorder the plate's `filament_maps` array (logical filament k → physical
  extruder m). Simplest; one array edit.
- **(b) paint-reference rewrite** — rewrite the logical filament index inside the `paint_color` triangle
  data (replicating OrcaSlicer's remap). More complex (must decode OrcaSlicer's paint encoding); assess
  feasibility as part of the spike.
- Also confirm the **per-tool filament profile arrangement**: the slice's filament arrays (length =
  `n_extruders`) must carry the printer's loaded-slot profile at each physical-tool position.

The spike's verdict (which mechanism routes correctly, and whether (b) is feasible to author) decides the
remap implementation. If only (b) works and it is infeasible to author reliably, STOP and escalate — the
fallback would be requiring the user to do the remap in OrcaSlicer before upload (a much smaller feature),
which we'd re-scope with the user. **No remap code is written before the spike resolves this.**

The sections below assume the spike picks a workable mechanism; only the *remap step* in `mesh_3mf_builder`
differs by mechanism — parsing, data model, UI, and gating are identical either way.

## Architecture & data flow

### 1. Parse declared filaments — `three_mf_parser` + `uploaded_files`
Read `project_settings.config` `filament_colour` + `filament_type` (+ `filament_settings_id`) → a list
`[{index: 1, color: "#F78E0E", type: "PLA"}, …]` (1-based, the model's declared filaments). Persist on the
`uploaded_files` row (a new `model_filaments` JSON column via `_migrate`) and expose it through the existing
file/plates API. A file is **multi-material** when it declares > 1 filament. (`slice_info.config` is NOT a
reliable source — it can be header-only; `project_settings.config` always carries the arrays.)

### 2. Data model — `JobPrinterConfig.filament_map`
Add a `filament_map` JSON column: `[{"model_filament": 1, "tool_index": 2}, …]` (model_filament 1-based;
tool_index 0-based physical tool). Empty/null ⇒ not multi-material (Project 2's `tool_index` / defer path is
unchanged). `_migrate` adds the column idempotently.

### 3. UI — shared `PerPrinterConfig`
When the selected file/plate is multi-material (declared filaments > 1), the per-printer control becomes a
**mapping list**: one row per model filament — a colour swatch + "Filament k" + a **tool dropdown** listing
the printer's loaded slots (T0–T3). Default = **identity** (filament k → tool k, clamped to the printer's
slot count). Selecting writes the row into `filament_map`. Single-material files keep the Project-2 single
tool picker / defer control. New + Edit Job get this free (shared component). The component receives the
model's declared filaments (from the file/plate API) to render the rows.

### 4. Slice + queue — `SliceRequest`, `queue_engine`, `mesh_3mf_builder`
- `SliceRequest` gains `filament_map: list[dict] | None` (alongside `tool_index`).
- The queue, for a config with a non-empty `filament_map`, passes (i) the printer's loaded-slot profiles
  ordered by physical tool as the N `filament_presets`, and (ii) the `filament_map` into `SliceRequest`.
- `mesh_3mf_builder` applies the spike-chosen remap (rewrite plate `filament_maps`, and/or paint
  references) when building the sliceable 3MF, keyed off `filament_map`. `tool_index` (single) remains the
  Project-2 path; `filament_map` (multi) is the new path; the two are mutually exclusive per config.

### 5. Eligibility gating — `queue_engine`
A config with a `filament_map` is eligible for its printer only when **every mapped `tool_index` slot is
loaded** (`tool_index < len(loaded_filaments)`); otherwise the job blocks (transient), same pattern as the
single-tool gate (`_filament_mismatch` / `_slot_for_config` extended for the map).

## Error handling / edge cases
- **Map references a tool the printer lacks** (slot removed, or fewer slots than mapped) → job blocked
  (transient) with a clear reason.
- **Unused declared filaments** mapped → harmless (those indices aren't painted; the remap is a no-op for
  them).
- **Single-material file** → `filament_map` stays empty; behaves exactly as Sub-project A.
- **Two model filaments mapped to the same tool** → allowed (both regions print with that tool's material).
- **Geometry-only recovery tier** must preserve/re-apply the remap (the mesh paint is part of geometry; the
  remap re-applies on the recovery 3MF too).

## Testing
- **Spike (Task 1):** painted 2-colour fixture; gcode confirms each colour routes to the mapped tool.
- **Parser:** `filament_colour`/`filament_type` → `model_filaments`; >1 ⇒ multi-material; single-material
  file ⇒ length-1.
- **Migration:** `uploaded_files.model_filaments` + `job_printer_configs.filament_map` added idempotently.
- **Slice remap:** unit-test the `mesh_3mf_builder` remap on a painted fixture (the mechanism the spike
  picked) — filament k routes to tool m.
- **Queue:** a config with a `filament_map` passes the loaded-slot profiles + map into `SliceRequest`;
  gating blocks when a mapped tool isn't loaded.
- **UI:** multi-material file renders one mapping row per declared filament with colour swatches; default
  identity; selecting writes `filament_map`; single-material keeps the Project-2 control. New + Edit Job.
- **Live (later):** a painted job mapped to specific U1 tools prints each region on the right tool.

## File structure
**Create:** spike script `backend/scripts/spike_filament_remap.py` (Task 1).
**Modify:** `backend/app/services/three_mf_parser.py` (declared filaments), `backend/app/models.py` +
`database.py` (`uploaded_files.model_filaments`, `job_printer_configs.filament_map`), `files`/plates route
(expose `model_filaments`), `backend/app/services/mesh_3mf_builder.py` (remap),
`backend/app/services/slicer_service.py` (`SliceRequest.filament_map`),
`backend/app/services/queue_engine.py` (pass map + gating),
`frontend/src/components/PerPrinterConfig.tsx` (mapping list) + `frontend/src/api/*` types.

## Sequencing
1. **Spike** → pick the remap mechanism (or escalate). Everything else gates on this.
2. Parser + data model + API.
3. Slice remap + queue + gating.
4. Mapping UI in the shared component (New + Edit).
5. Docs; live verification when the U1 is free.

---

## Spike result (2026-06-09)

**Script:** `backend/scripts/spike_filament_remap.py`
**Fixture:** Hausdeko #41 (single painted object, 4 declared filaments, ~9049 painted triangles)
**Machine/process:** Snapmaker U1 (0.4 nozzle) / 0.08 Extra Fine, 4× Generic PLA High Speed @System

### Per-variant tool-select sequences

All three variants of `filament_maps` produced **identical** gcode tool usage:

| `filament_maps` value | Ordered distinct tools | Extruders with M104/M109 |
|---|---|---|
| `1 2 3 4` (identity) | T1 → T0 → T2 | T0, T1, T2, T3 |
| `2 1 4 3` (swapped)  | T1 → T0 → T2 | T0, T1, T2, T3 |
| `3 4 1 2` (rotated)  | T1 → T0 → T2 | T0, T1, T2, T3 |

The `filament_maps` rewrite is ignored by OrcaSlicer when slicing a painted model.

### Mechanism (a) verdict: FAILS

Rewriting the plate `<metadata key="filament_maps">` array has **no effect** on which physical
tools the painted regions are assigned to. OrcaSlicer routes painted models by the logical
filament index stored in the `paint_color` triangle attribute, not by `filament_maps`.

### paint_color encoding (mechanism b analysis)

The `paint_color` attribute in `3D/Objects/*.model` encodes a recursive **triangle subdivision
tree** as a hex bitstream, 3 bits per node, **LSB-first within bytes** (odd-length hex strings
padded with a trailing `'0'` nibble). Node values (from `EnforcerBlockerType` in
`libslic3r/TriangleSelector.cpp`):

| Node value | Meaning |
|---|---|
| 0 | NONE — inherits the object's base extruder |
| 1 | Support ENFORCER (not a filament) |
| 2 | Support BLOCKER (not a filament) |
| 3 | Extruder1 — logical filament 1 |
| 4 | Extruder2 — logical filament 2 |
| 5 | Extruder3 — logical filament 3 |
| 6 | Extruder4 — logical filament 4 |
| 7 | SPLIT — exactly 4 children follow in the stream |

Fixture node distribution (1,278,676 total nodes): NONE=610348, ENFORCER=157984, BLOCKER=63555,
fil1=120660, fil2=159434, fil3=26712, fil4=128084, SPLIT=11899. All filaments 1–4 are present;
11899 SPLIT nodes confirm real tree depth. Logical filament k (1-based) → node value k+2.

### Mechanism (b) verdict: FEASIBLE

To remap logical filament A → physical tool B:
1. Read the hex bitstream LSB-first in 3-bit chunks.
2. When a leaf node value is in 3–6 (a filament index), apply the remap table: `old_val − 2` → lookup `filament_map` → `new_val + 2`.
3. Structural SPLIT (7) nodes and support ENFORCER/BLOCKER (1, 2) nodes are preserved unchanged.
4. Re-pack the remapped node values back to a hex string with the same total bit count.

This is a single O(N) pass with no third-party libraries. The recursion is deterministic (SPLIT
always has exactly 4 children). Re-encoding is the exact inverse of decoding.

### **VERDICT: a fails, b feasible**

Task 5 (`mesh_3mf_builder` remap) must implement **mechanism (b)**: rewrite the logical filament
references inside the `paint_color` bitstream for each painted triangle in `3D/Objects/*.model`.
The `filament_maps` array in `model_settings.config` is irrelevant to painted-model routing and
should be left at identity (`1 2 3 4`) in the sliceable 3MF.
