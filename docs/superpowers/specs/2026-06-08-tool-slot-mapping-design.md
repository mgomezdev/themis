# Single-Filament Tool Selection (Snapmaker U1) — Design Spec (Project 2)

**Date:** 2026-06-08
**Status:** Approved (pending spec review)
**Branch:** `tool-slot-mapping`

## Goal & scope

Let a job explicitly choose **which physical tool/extruder (T0–T3)** a single-material print runs on,
for multi-tool printers like the Snapmaker U1. Today the slice/queue pipeline is single-filament and the
emitted gcode targets whatever extruder OrcaSlicer assigns by default (T0); there is no way to say "print
this on tool 2." This closes that gap.

**In scope:** per-job, per-printer **explicit tool pick** (the user always picks the tool); the slice
binds the single filament to the chosen extruder so the gcode's tool-change commands target it; New Job
shows a tool picker for any printer with ≥2 loaded slots; the chosen tool's loaded slot supplies the
filament profile/colour.

**Out of scope (Project 2b, separate design):** multi-material model-filament → printer-tool mapping
(N filaments, the model's own part→filament assignment). Deferred until the single-tool mechanism is
verified on real hardware. This design stays forward-compatible: `filament_map` generalizes from a
1-element list to N entries with no rework.

**Builds on:** the merged multi-slot filament editor (`6924f10`) — a printer can now hold up to 4
manually-defined slots, which are the tools the picker chooses among. And `42e9d82`, which made
`project_config_builder` multi-extruder-aware (`printer_extruder_id`, `filament_self_index`,
per-extruder array expansion) and added the `snapmaker blank single/2 plates.3mf` references.

## Mechanism: OrcaSlicer `filament_map`

OrcaSlicer's `filament_map` is an array with one entry per filament; each value is the **1-based
extruder index** that filament prints on. For a single-filament print on a multi-extruder machine,
`filament_map=[k]` binds the filament to extruder `k`, and OrcaSlicer bakes the corresponding tool-change
gcode. `project_config_builder` already lists `filament_map` among known keys but never sets it, so it
currently carries the reference default.

`tool_index` is **0-based** (T0–T3, matching the Klipper extruder/extruder1/extruder2/extruder3 and the
loaded-slot index); `filament_map` is **1-based**, so `filament_map = [tool_index + 1]`.

### Verification spike (FIRST task, no printer needed)
Before any model/UI work, **slice locally** to confirm the mechanism:
1. Slice a **small model that actually extrudes** (e.g. a calibration cube — not the blank-plate
   reference, which emits no tool change) with the U1 machine profile + one filament, once per
   `filament_map` value `[1]` and `[3]`.
2. Grep the emitted gcode for the tool-activation it produces (expect a `T2` /
   `ACTIVATE_EXTRUDER EXTRUDER=extruder2` for `[3]`, and the extruder0 path for `[1]`), confirming the
   chosen extruder actually changes.
- **If confirmed:** proceed with `filament_map` (Approach A below).
- **If `filament_map` does not route a single filament:** fall back to connector gcode tool-activation
  (Approach B) — slice as default, then `SnapmakerExtendedClient` prepends `ACTIVATE_EXTRUDER
  EXTRUDER=extruder{n}` (and matching temp set) before the print. Document the spike result either way.

The remaining sections assume Approach A; the only change for B is *where* the tool routing is applied
(connector instead of slice config) — the data model, UI, and gating are identical.

## Architecture & data flow

### 1. Model — `backend/app/models.py` + `database.py`
Add to `JobPrinterConfig`:
```python
tool_index: Mapped[Optional[int]] = mapped_column(nullable=True)  # 0-based extruder/slot; None = default tool
```
`_migrate()` gets an idempotent `ALTER TABLE job_printer_configs ADD COLUMN tool_index INTEGER` guard
(try/except like the other added columns). `None` preserves today's behavior (single-tool printers, and
existing rows).

### 2. Slice config — `project_config_builder.build_project_config`
Add a `tool_index: int | None = None` parameter. After the `n_extruders > 1` block, when `tool_index is
not None and n_extruders > 1`:
```python
config["filament_map"] = [str(tool_index + 1)]
```
For `n_extruders <= 1` or `tool_index is None`, leave `filament_map` at its reference default (no
behavior change for existing printers). `project_config_json` forwards the new param.

### 3. Slice request — `slicer_service.SliceRequest` + `SlicerService.slice`
`SliceRequest` gains `tool_index: int | None = None`. Wherever `slice()` calls `build_project_config`
(via `_build_config`), forward `tool_index`. No other slice logic changes — still one filament in,
raw gcode out.

### 4. Queue — `queue_engine._run_slice_and_print`
When `config.tool_index is not None`:
- Resolve the slot **by index**: `slot = loaded[config.tool_index] if config.tool_index < len(loaded)
  else None` (bypass `_matching_loaded_filament`).
- `filament_profile`, `filament_color`, `filament_type` come from that slot.
- Pass `tool_index=config.tool_index` into `SliceRequest`.

When `tool_index is None`, the existing `_matching_loaded_filament` path is unchanged.

### 5. Eligibility gating — `queue_engine` (`_eligible` / `_matching_loaded_filament` caller, ~line 56)
A config with `tool_index` set is eligible for its printer when **slot `tool_index` is loaded**
(`tool_index < len(loaded_filaments)` and that slot has a filament). Configs with `tool_index is None`
keep the current filament-ask match. (Per-printer config already scopes this — `JobPrinterConfig.printer_id`.)

### 6. New Job UI — `frontend/src/screens/NewJobScreen.tsx`
- `PerPrinterCfg` gains `toolIndex: number | null`.
- In `PerPrinterConfig`, fetch the selected printer's `loaded_filaments`. If it has **≥2 slots**, render
  a **tool picker** (a select/segmented list of the slots, each showing `T{i}` + material name + colour
  swatch) instead of the free filament ask. Selecting a slot sets `toolIndex` and copies that slot's
  `filament_profile`/`type`/`color` into the config fields (so the created `JobPrinterConfig` carries
  both `tool_index` and the resolved filament identity).
- Printers with `<2` slots are unchanged (existing filament ask; `toolIndex` stays `null`).
- `createJob` includes `tool_index` per printer config.

### 7. Create-job route — `backend/app/api/routes/jobs.py`
The job-create payload's per-printer config accepts an optional `tool_index`; it's persisted onto the
`JobPrinterConfig` row. Default `None`.

## Error handling
- `tool_index` out of range (slot removed after job creation) → treat as no matching slot → job
  **blocked** (transient), same path as a filament mismatch today. Surface a clear `slice_error` if it
  reaches the slice.
- `filament_map` only set for multi-extruder profiles; a single-extruder printer with a stray
  `tool_index` ignores it (no `filament_map` written) and slices normally.
- Spike-fallback (Approach B) failures (bad extruder activation) surface through the existing
  start/slice failure handling.

## Testing
**Backend (pytest):**
- `build_project_config(..., tool_index=2)` on a multi-extruder machine sets `filament_map == ["3"]`;
  with `tool_index=None` or a single-extruder machine, `filament_map` is left at the reference default.
- `SliceRequest`/`SlicerService` forwards `tool_index` into `build_project_config` (assert via a spy/mock).
- Queue: a config with `tool_index=2` resolves the slot as `loaded_filaments[2]` and passes
  `tool_index=2` to the `SliceRequest`; `tool_index=None` still uses `_matching_loaded_filament`.
- Migration: `_migrate()` adds `tool_index` idempotently (run twice, no error; column present).
- Eligibility: config with `tool_index=3` is eligible only when slot 3 is loaded.

**Frontend (vitest):**
- `PerPrinterConfig` renders the tool picker when the printer has ≥2 loaded slots and writes `toolIndex`
  + copies the slot's filament identity; renders the legacy ask at <2 slots.
- `createJob` payload includes `tool_index`.

**Manual:** the local slice spike (above); a real test print on the U1 picking T2 vs T3 once the printer
is free.

## File structure
**Modify:** `backend/app/models.py`, `backend/app/database.py` (`_migrate`),
`backend/app/services/project_config_builder.py`, `backend/app/services/slicer_service.py`,
`backend/app/services/queue_engine.py`, `backend/app/api/routes/jobs.py`,
`frontend/src/screens/NewJobScreen.tsx` (+ its test).
**Add tests:** `backend/tests/services/test_project_config_builder.py` (extend),
queue + migration tests, `NewJobScreen` test cases.
**Docs:** `themis-docs-sync` after implementation (`printers.md` slicing section, `data-model.md` for the
new column).

## Verification status / sequencing
1. Local slice spike → confirm `filament_map` routes the tool (decides A vs B).
2. Build the data + slice + queue + UI changes (TDD).
3. Live: print on T2 vs T3 on the U1 when free.
Project 2b (multi-material model→tool mapping) is a separate spec, started after this lands and the
mechanism is hardware-verified.

### Spike result (2026-06-08)

**Presets used:** machine `Snapmaker U1 (0.4 nozzle)`, process `0.08 Extra Fine @Snapmaker U1 (0.4 nozzle)`, filament `AliZ PA-CF @System`.
**`--slice` index that produced gcode:** `0` (both runs).

Tool-select lines observed (first hit, line 32 of gcode):

```
filament_map=[1] -> 'M104 T0 S140'
filament_map=[3] -> 'M104 T0 S140'
```

Full tool-select inventory (identical in both files, 8 lines each):
- L32: `M104 T0 S140`
- L37: `T0`
- L55–L58: `M104 S0 T0/T1/T2/T3 A0` (all-extruder cool-down)
- L59: `M104 T0 S200`
- L62: `T0`

The two gcode files are byte-for-byte identical except for the timestamp comment on line 2. OrcaSlicer's `filament_map` setting does **not** route a single-filament job to a different extruder for the Snapmaker U1 profile.

**Verdict: Approach B confirmed — `filament_map` does NOT route the tool.** Tasks 2 and 6 must be re-planned: the `SnapmakerExtendedClient` (or equivalent connector layer) must prepend `ACTIVATE_EXTRUDER EXTRUDER=extruder{tool_index}` (and a matching temp set) before the print begins; the slice leaves `filament_map` at its default. The data model (`tool_index` column), UI tool picker, queue gating, and `SliceRequest.tool_index` field are all still needed — only the mechanism that applies the routing shifts from the slicer config to the connector's print-start path.

#### `filament_map_mode=Manual` retest (2026-06-08)

Hypothesis: the reference defaults `filament_map_mode = "Auto For Flush"`, and in AUTO modes OrcaSlicer ignores the manual `filament_map` and routes the lone filament to the master extruder (T0). Setting the mode to MANUAL should make `filament_map` take effect. The spike was extended to sweep five config variants ([1] vs [3] each):

| Variant | filament_map=[1] | filament_map=[3] | Result |
|---|---|---|---|
| baseline (no mode override) | `M104 T0 S140` | `M104 T0 S140` | NO DIFFERENCE |
| `filament_map_mode="Manual"` | `M104 T0 S140` | `M104 T0 S140` | NO DIFFERENCE |
| `filament_map_mode="manual"` (lowercase) | `M104 T0 S140` | `M104 T0 S140` | NO DIFFERENCE |
| `filament_map_mode="Auto For Match"` | `M104 T0 S140` | `M104 T0 S140` | NO DIFFERENCE |
| `Manual` + `single_extruder_multi_material="0"` | `M104 T0 S140` | `M104 T0 S140` | NO DIFFERENCE |

Verified the overrides actually reach the slicer: the prepared 3MF's `Metadata/project_settings.config` serializes `filament_map: ["3"]` and `filament_map_mode: "Manual"` correctly (not dropped). A full-file diff of the Manual-mode `[1]` vs `[3]` gcode shows **only the timestamp comment differs** (0 non-timestamp differing lines). Notes: `build_project_config` already yields `single_extruder_multi_material="0"` for the U1 (the machine preset overrides the reference's `"1"`), so the U1 is treated as a true multi-extruder toolchanger, not an AMS-style SEMM; and the slice log emits a non-fatal `could not found extruder_type "Direct Drive"` warning but still produces gcode.

**Conclusion: still Approach B.** Manual map mode does not revive the slice-level approach — for a single-filament print there is only one filament to map, and OrcaSlicer 2.3.2 always emits `T0`. Tool routing must be applied by the connector at print-start (`ACTIVATE_EXTRUDER`), as above.
