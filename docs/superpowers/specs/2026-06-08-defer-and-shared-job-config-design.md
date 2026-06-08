# Defer Filament + Shared Per-Printer Job Config — Design Spec (Sub-project A)

**Date:** 2026-06-08
**Status:** Approved (pending spec review)
**Branch:** `tool-slot-mapping`

## Goal & scope

Two job-flow improvements, plus the refactor that makes them clean:

1. **Defer filament ("use whatever's loaded").** A per-printer job config can decline to constrain the
   filament — print with whatever the printer currently has loaded. This becomes the **default**.
2. **Edit-Job parity.** The single-tool picker built in Project 2 (`tool_index`) currently exists only in
   New Job. Edit Job must offer the same tool/defer controls.
3. **Consolidate** the duplicated New-vs-Edit per-printer config into one shared component (the duplication
   is *why* Edit Job missed the tool picker).

**In scope:** a shared `PerPrinterConfig` component used by both screens; a unified filament/tool control
(defer / pick tool / require filament); the small backend additions so Edit Job round-trips `tool_index`.

**Out of scope (Sub-project B, separate spec):** mapping each **filament defined in the 3MF model** to a
printer tool (multi-material). That needs new 3MF filament parsing + a mapping UI + slice remap and is its
own project. This spec deliberately keeps the per-config model single-tool (`tool_index`), which Sub-project
B will generalize.

**Builds on:** Project 2 (`tool_index` on `JobPrinterConfig`, the queue's `_slot_for_config`, the New Job
tool picker). No schema change here.

## Key insight: "defer" needs no new persisted state

The queue already treats a config with an empty filament ask (`filament_type`/`filament_color` blank) and
`tool_index = None` as "match the first loaded slot," and `_run_slice_and_print` slices with **that slot's**
`filament_profile`. So **defer == `tool_index = None` + empty ask**. The slice/queue need no changes; "defer"
is surfaced as an explicit, default UI choice over behavior that already exists. `mode` (below) is local UI
state, never persisted.

## Architecture & data flow

### 1. Shared component — `frontend/src/components/PerPrinterConfig.tsx` (new)
Exports:
- `interface PerPrinterCfg { printProfile: string | null; filamentProfile: string | null; filamentId: number | null; filamentType: string | null; filamentColor: string | null; toolIndex: number | null; }`
- `function defaultPerPrinterCfg(): PerPrinterCfg` → all fields `null` (i.e. defer).
- `function PerPrinterConfig({ printerId, printers, config, onChange }: {...})` — the per-printer config block:
  print-profile select + the unified filament/tool control. It owns the hooks the two old copies duplicated
  (`usePrinterProfiles`, `useSpoolmanConfig`/`useFilaments`).

`NewJobScreen.tsx` and `EditJobScreen.tsx` import these, **delete** their local `PerPrinterCfg` interfaces
and `PerPrinterConfig`/`PerPrinterConfigEditor` components, and use the shared ones. Each screen keeps its
own plate/printer-selection logic and payload assembly; only the per-printer config block is shared.

### 2. Unified filament/tool control (inside `PerPrinterConfig`)
Driven by the selected printer's loaded-slot count (`printer.loaded_filaments.length`), no vendor names:

- **Multi-tool (≥2 loaded slots):** a **Tool** `<select data-testid="tool-select">` whose options are:
  - `"Any / default tool"` → value `""` → `onChange({ toolIndex: null, filamentProfile: null, filamentId: null, filamentType: null, filamentColor: null })` (defer; prints on the default tool with whatever's loaded).
  - one per slot: `"T{i} · {type}{ (name)}"` → value `i` → `onChange({ toolIndex: i, filamentProfile: slot.filament_profile, filamentId: null, filamentType: slot.type, filamentColor: slot.color })`.
- **Single-tool (<2 loaded slots):** a 2-option mode toggle (`data-testid="filament-mode"`):
  - **"Use loaded filament"** (defer; **default**) → clears the ask (`filamentProfile/Id/Type/Color = null`, `toolIndex = null`).
  - **"Require specific filament"** → reveals the existing catalog/manual type+color ask (unchanged JSX from the current component).

`mode` for the single-tool toggle is local state initialized from the config: `mode = (config.filamentType || config.filamentProfile) ? 'require' : 'defer'`. Switching to "Use loaded" clears the ask; switching to "Require" reveals the (empty) inputs for the user to fill.

### 3. Default = defer
`defaultPerPrinterCfg()` is all-null, so a freshly-added printer config defers (single-tool → "Use loaded
filament"; multi-tool → "Any / default tool"). This matches today's behavior where the filament ask was
already optional/empty by default.

### 4. Backend — Edit-Job round-trip of `tool_index`
- `backend/app/api/routes/jobs.py` `get_job_details`: add `"tool_index": cfg.tool_index` to each
  `printer_configs` entry (so Edit Job can pre-fill the tool).
- `update_job_configs` (PATCH `/{job_id}/configs`): when it rebuilds the `JobPrinterConfig` rows from
  `body.printer_configs`, include `tool_index=cfg.tool_index` (mirror `create_job`, which already does).
  `PrinterConfigInput` already carries `tool_index` (Project 2). Verify the rebuild path and add the field
  if missing.
- `frontend/src/api/queue.ts`: add `tool_index: number | null` to the `ApiJobPrinterConfig` interface.

### 5. Edit Job pre-fill
`EditJobScreen` builds `perPrinter[sid]` from `job.printer_configs`; include
`toolIndex: c.tool_index ?? null` so the shared control restores the saved tool/defer state. Its
`updateJobConfigs` payload already maps the config fields; add `tool_index: cfg.toolIndex ?? null` (mirroring
New Job's `createJob`).

## Error handling / edge cases
- **Defer on a printer with nothing loaded:** `_slot_for_config` → no slot → `filament_profile = None` →
  OrcaSlicer slices with its default. Acceptable (nothing to "use"); not blocked.
- **"Require" mode with empty inputs:** persists as an empty ask, i.e. behaves as defer (match-first). No
  hard validation added; the print-profile remains the only required field (unchanged).
- **Stale `toolIndex` after slots change:** unchanged from Project 2 — `_filament_mismatch` blocks the job
  (transient) if the chosen tool slot isn't loaded.

## Testing
**Frontend (vitest):**
- `PerPrinterConfig` renders the **tool select** (with an "Any / default tool" first option) when the printer
  has ≥2 loaded slots; selecting "Any / default" writes `toolIndex=null` + cleared ask; selecting `T2` writes
  `toolIndex=2` + copies that slot's `filament_profile`/`type`/`color`.
- `PerPrinterConfig` renders the **mode toggle** when <2 slots, defaults to "Use loaded filament" (ask hidden),
  and "Require specific filament" reveals the type/color inputs.
- `defaultPerPrinterCfg()` is all-null (defer).
- New Job and Edit Job both render the shared component and include `tool_index` in their payloads (extend the
  existing `NewJobScreen.test.tsx`; add an `EditJobScreen` test).

**Backend (pytest):**
- `get_job_details` returns `tool_index` in each `printer_configs` entry.
- `update_job_configs` persists `tool_index` on the rebuilt rows (round-trip via the route).

## File structure
**Create:** `frontend/src/components/PerPrinterConfig.tsx` (+ `PerPrinterConfig.test.tsx`).
**Modify:** `frontend/src/screens/NewJobScreen.tsx`, `frontend/src/screens/EditJobScreen.tsx` (use shared
component; delete local copies), `frontend/src/api/queue.ts` (`ApiJobPrinterConfig.tool_index`),
`backend/app/api/routes/jobs.py` (`get_job_details` + `update_job_configs`).
**Docs:** `themis-docs-sync` after (frontend.md screens/components; backend.md route note).

## Sequencing
Sub-project A (this spec) → then Sub-project B (multi-material 3MF-filament → tool mapping) as a separate
spec, which generalizes the single `toolIndex` into a per-model-filament → tool map and reuses this shared
component as its host.
