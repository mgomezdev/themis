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
