# Filament Constraint Verification + 3MF Override Confirmation Design

## Overview

Two related features for New Job and Edit Job:

1. **Filament constraint verification** — when a per-printer filament constraint is set, show inline whether that printer's currently-loaded filaments satisfy it (chip badge, no save gate).
2. **3MF override opt-in** — when an uploaded 3MF has embedded print settings, show an inline panel where the user explicitly confirms which settings to keep; confirmed overrides are stored per-job and applied at slice time on top of the chosen profile.

---

## Filament Constraint Modes

`PerPrinterConfig` gains a **three-way selector** (single-filament path only — AMS mapping and multi-tool paths unchanged):

| Mode | Constraint | Fields set |
|---|---|---|
| **Defer** | None — use whatever is loaded | `filament_type = null`, `filament_color = null`, `filament_id = null` |
| **Type only** | Match by material (PLA / PETG / ABS …) | `filament_type = "PLA"`, `filament_color = null` |
| **Type + color** | Match by material and color | `filament_type = "PLA"`, `filament_color = "#4488ff"` |
| **Spoolman filament** | Match by filament definition ID (any spool of that filament) | `filament_id = 5` (Spoolman filament definition ID, not spool ID) |

The Spoolman picker already exists; the three-way selector for type/color replaces the current binary defer/require toggle.

### Verification chip badge

Shown inside `PerPrinterConfig` when any constraint is active. Computed client-side from the already-fetched `printer.loaded_filaments` — no new API call.

- **Green chip:** "✓ Generic PLA · slot 0 🔵" — a matching slot was found
- **Red chip:** "✗ No PETG loaded" — no loaded slot satisfies the constraint
- **Amber chip:** "◉ Any loaded filament" — defer mode (always shown to make state explicit)

Matching rules (same logic already used by the queue engine):
- Type only → match on `filament_type` (case-insensitive), ignore color
- Type + color → match on both `filament_type` and `filament_color`
- Spoolman filament → match on `slot.filament_id == config.filament_id`

Job saves regardless of badge state. Printers with a red badge simply won't claim the job at queue time (queue engine already enforces this via `_filament_mismatch`).

---

## 3MF Override Opt-In

### What is an "embedded setting"

OrcaSlicer 3MF files can embed their own values for print parameters (fill pattern, support type, layer height, etc.) inside `Metadata/project_settings.config`. When slicing with a profile, the profile normally wins. The user can now selectively keep specific 3MF settings so they win instead.

Only **curated keys** are eligible (same list already maintained in `override_inspector.py`):

- `fill_pattern`
- `support_type`
- `layer_height`
- `support_on_build_plate_only`
- `enable_support`
- `sparse_infill_density`
- `top_shell_layers`
- `bottom_shell_layers`

### UI — `OverridePanel` component

Inline section in `PlateConfigPanel`, below printer configs. Rendered only when `embeddedSettings.length > 0`.

```
┌─ 3MF Embedded Settings ───────────────────────────────────┐
│  The file has these settings baked in. Check the ones     │
│  you want to apply — unchecked ones use the profile.      │
│                                                           │
│   ☑  Fill pattern      grid                               │
│   ☐  Support type      tree                               │
│   ☑  Layer height      0.15 mm                            │
└───────────────────────────────────────────────────────────┘
```

State: `confirmedOverrides: Record<string, string>` — only checked entries. Hidden entirely when the file has no embedded curated-key settings.

The existing `OverrideAlertModal` is removed — the inline panel replaces it.

### Data flow

1. File selected → frontend calls `GET /api/v1/files/{id}/embedded-settings` (alongside existing `getModelFilaments`)
2. If settings found → `OverridePanel` appears, all items unchecked by default
3. User checks items → `confirmedOverrides` updated in form state
4. Job saved → `overrides` included in `POST /jobs` or `PATCH /jobs/{id}/configs` payload
5. Stored in `jobs.overrides` (JSON column)
6. At slice time → `_run_slice_and_print` merges `job.overrides` into the slicer's `extra_config`; OrcaSlicer applies them after the profile so they win

---

## Backend Changes

### `database.py`

New migration adds `overrides JSON` to `jobs`:
```python
if "overrides" not in job_cols:
    await conn.execute(text("ALTER TABLE jobs ADD COLUMN overrides JSON"))
```

### New endpoint: `GET /api/v1/files/{file_id}/embedded-settings`

In `files.py`. Delegates to `parse_embedded_settings(path)` in `three_mf_parser.py`.

Returns:
```json
[
  {"key": "fill_pattern", "label": "Fill pattern", "value": "grid"},
  {"key": "layer_height",  "label": "Layer height",  "value": "0.15"}
]
```

Empty array if the file is not a 3MF, has no embedded settings, or no curated keys are present.

### `three_mf_parser.py`

New function `parse_embedded_settings(file_path: str) -> list[dict]` — reads `Metadata/project_settings.config` from the 3MF zip, filters to the curated key list, returns `{key, label, value}` records.

### `POST /api/v1/jobs` and `PATCH /api/v1/jobs/{id}/configs`

Both accept `overrides: dict | None = None` in the request body (via `JobCreate` / `JobConfigsUpdate` Pydantic schemas). Stored in `job.overrides`.

### `queue_engine.py` — `_run_slice_and_print`

After building the slicer config, merge `job.overrides` into `extra_config`:
```python
if job.overrides:
    extra_config.update(job.overrides)
```
Overrides applied last so they win over profile values.

### `queue_engine.py` — `_matching_loaded_filament`

Spoolman filament constraint matching uses type+color derived from the Spoolman filament definition (stored in `filament_type` / `filament_color` at job-creation time — see frontend note below). The queue engine does not require `filament_id` to be present in loaded slots; matching falls through to the existing type+color logic. `filament_id` on `job_printer_configs` is stored for display and future Spoolman spool-level integration, but is not used for queue matching in this version.

---

## Frontend Changes

### `PerPrinterConfig.tsx`

- Replace binary defer/require toggle with three-way selector: **Defer / Type only / Type + color** (Spoolman picker path unchanged)
- When a Spoolman filament is selected, also populate `filamentType` and `filamentColor` from the Spoolman filament's `material` and `color_hex` fields — so queue matching uses type+color and `filament_id` is stored for reference
- Add chip badge below the constraint inputs, computed from `printer.loaded_filaments`
- Multi-filament AMS mapping and multi-tool paths: unchanged

### New: `OverridePanel.tsx`

- Props: `settings: EmbeddedSetting[]`, `value: Record<string, string>`, `onChange`
- Renders a checkbox list; `onChange` emits only the checked entries
- Hidden when `settings` is empty

### `NewJobScreen.tsx`

- Call `getEmbeddedSettings(fileId)` alongside `getModelFilaments(fileId)` when file loads
- Pass result to `PlateConfigPanel` → `OverridePanel`
- Include `confirmedOverrides` in the `printer_configs` payload (job-level, not per-printer)
- Remove `OverrideAlertModal`

### `EditJobScreen.tsx`

- Call `getEmbeddedSettings` for the job's file on load
- Pre-populate `confirmedOverrides` from `job.overrides`
- Include in update payload
- Remove `OverrideAlertModal` (same as NewJobScreen)

---

## Edge Cases

- **No embedded settings:** `OverridePanel` hidden entirely; no behavior change
- **Override key not supported by OrcaSlicer `--overwrite`:** Silently ignored; profile value stands
- **Spoolman offline:** Filament chip badge shows "unknown" state; job saves and queues normally
- **No loaded slots match constraint:** Red chip badge; job saves and queues; that printer is skipped at claim time by existing `_filament_mismatch` logic
- **File replaced after job created:** Files are immutable once uploaded; not applicable

---

## What Is Not Changing

- Multi-tool (`tool_index`) and AMS filament mapping (`filament_map`) paths in `PerPrinterConfig` — unchanged
- `override_inspector.py` and its `inspect_overrides()` function — kept as-is (used in verify-slice debug endpoint)
- Spoolman spool picker in `PerPrinterConfig` — now represents filament definition, not spool (existing `filament_id` column, no schema change)
- Queue engine filament mismatch enforcement — behavior unchanged, just extended for `filament_id` matching
