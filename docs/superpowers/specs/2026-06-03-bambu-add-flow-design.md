# Bambu Add-Flow + AMS/Profile Mapping — Design Spec

**Date:** 2026-06-03
**Status:** Approved (pending spec review)
**Branch:** `bambu-printer`

## Goal

Extend the add-printer flow so a Bambu printer (via `BambuMQTTClient`) can be fully set up for
queue/slice use:
1. Pick **make/model → OrcaSlicer printer profile** (`current_orca_printer_profile`) — for *all*
   printer types.
2. Per **AMS-reported tray**: define an **OrcaSlicer filament profile** (so slicing works) and
   **optionally map it to a Spoolman spool**.

Decided during brainstorming:
- **Create-first, map-after**: the wizard creates the printer (with make/model); per-tray mapping is
  done afterward in the printer's Edit surface, once it has connected and the AMS auto-synced.
- **AMS re-sync preserves mappings by slot** (always), fixing today's wipe-on-sync bug.
- **Dedicated `spoolman_spool_id` field** (stop overloading `filament_id`).
- **Make/model step for all printer types**; the per-tray AMS mapping is shown only when the printer
  has loaded filaments (Bambu/AMS).

## Background (current state — verified)

- `printers.loaded_filaments` is a **JSON** column (list of slot dicts) — adding a field needs no DB
  migration. Slot shape today: `{slot, filament_id, name, type, color, filament_profile?}`.
- `BambuMQTTClient.get_loaded_filaments()` → trays `{slot, ams_tray_id, ams_unit, filament_id (Bambu
  code e.g. 'GFL99'), name, type, color}` — **no `filament_profile`**.
- `PrinterManager.on_ams_change(printer_id, trays)` currently does
  `printer.loaded_filaments = trays` — **overwrites**, discarding any user-set `filament_profile`.
- `current_orca_printer_profile` (machine preset name) and `orca_printer_profiles` persist via
  `POST/PATCH /api/v1/printers`. `GET /api/v1/printers/{id}/profiles` →
  `{print_profiles, filament_profiles}` (filament presets compatible with the printer's current
  machine preset). `GET /api/v1/printers/orca-machine-catalog` → `MachinePreset[]`
  (`vendor/printer_model/nozzle/name/source`).
- Slicing: `queue_engine._matching_loaded_filament` → the matched tray's `filament_profile` →
  `SliceRequest.filament_presets` (empty ⇒ OrcaSlicer default, no hard fail).
- `filament_id` is **overloaded** today: Bambu AMS code on Bambu; `String(spool.id)` when the Fleet
  `FilamentPicker` maps a Spoolman spool. We are de-conflating it.
- A make→model→nozzle cascade picker already exists inline in `FleetScreen.EditPrinterModal`
  (lines ~112–267) — to be extracted and reused.

## Data model

Loaded-filament slot dict gains one optional field:
```
{ slot, filament_id, name, type, color, filament_profile?, spoolman_spool_id? }
```
- `filament_id`: **Bambu AMS code** only (or null). No longer used for Spoolman.
- `spoolman_spool_id: str | null`: the mapped Spoolman spool id (optional).
- `filament_profile: str | null`: OrcaSlicer filament preset name used to slice with this tray.

Frontend `LoadedFilament` type (`frontend/src/api/printers.ts`) adds
`spoolman_spool_id?: string | null`.

## Backend

### `PrinterManager.on_ams_change` — merge instead of overwrite
`backend/app/services/printer_manager.py` (~line 178). New behavior:
- Build a lookup of the **existing** `printer.loaded_filaments` by `slot`.
- For each incoming AMS tray, if a stored slot with the same `slot` exists, carry over its
  `filament_profile` and `spoolman_spool_id` onto the incoming tray (always preserve by slot).
- Slots no longer reported by the AMS drop (with their mappings) — acceptable.
- Persist the merged list.

No new endpoints. No migration (JSON column). `loaded_filaments` round-trips as opaque JSON through the
printers routes and the `printer_manager` serializer, so `spoolman_spool_id` is carried automatically.

## Frontend

### Shared `MachinePicker` component
Extract the make→model→nozzle cascade from `FleetScreen.EditPrinterModal` into
`frontend/src/components/MachinePicker.tsx`:
- Props: `catalog: MachinePreset[]`, `value: string` (current machine-preset name), `onChange(name)`.
- Renders three `<select>`s (Make / Model / Nozzle); resolving nozzle sets the machine-preset name
  (prefer `source === 'system'`). Initializes its three selects from `value` when provided.
- `FleetScreen.EditPrinterModal` is refactored to consume this component (no behavior change there).

### Add-printer wizard (`PrintersScreen.PrinterAddForm`)
Add a **"Printer profile"** step (applies to every type), between Connect and Review:
- Fetch `fetchMachineCatalog()` on mount; render `<MachinePicker value={machinePreset} onChange=…/>`.
- Carry `machinePreset` in wizard state; **not required** to finish (can be set later in Edit), but
  surfaced in Review.
- `handleFinish` → `createPrinter({ …, current_orca_printer_profile: machinePreset || null,
  orca_printer_profiles: machinePreset ? [machinePreset] : [] })`.
- Step indicators become `Type · Connect · Profile · Review`.

### Per-tray mapping in `PrintersScreen.EditForm`
`EditForm` gains:
1. **Make/model**: `<MachinePicker value={currentPreset} onChange=…/>`; saved as
   `current_orca_printer_profile` (+ `orca_printer_profiles:[preset]`).
2. On preset change (and on mount with a preset), fetch `GET /printers/{id}/profiles` →
   `filament_profiles`.
3. **Per loaded-filament slot**, in addition to the existing color/type/name controls:
   - **Filament profile** `<select>` populated from `filament_profiles` (option “— none —” allowed);
     writes `slot.filament_profile`. A subtle "no profile → slicer default" cue when empty.
   - **Spoolman spool** picker (only when Spoolman is connected): a `<select>`/search over
     `useSpools()`; choosing a spool sets `slot.spoolman_spool_id = String(spool.id)` and shows
     `spoolDisplayName`. A "clear" option unsets it. When Spoolman is off, show a hint and skip.
4. Save → `updatePrinter(id, { name, connection_config, current_orca_printer_profile,
   orca_printer_profiles, loaded_filaments })`.
- For **Bambu/AMS** printers the slots come from the auto-synced `loaded_filaments` (type/color/name are
  AMS-detected; still editable but expected to be driven by AMS). The existing **add/remove slot**
  controls remain for manual (non-AMS) printers.

### Fleet `FilamentPicker` de-overload
`FleetScreen.tsx` `FilamentPicker.selectSpool` writes `spoolman_spool_id: String(spool.id)` instead of
`filament_id: String(spool.id)` (leave `filament_id` as-is / null). Display reads `spoolman_spool_id`.

## Edge cases / error handling
- Empty `filament_profile` on a tray → slicing proceeds on OrcaSlicer's default (no hard fail); the UI
  shows a per-tray cue so it's visible.
- AMS reports fewer trays than stored → orphaned slots (and their mappings) drop. Documented behavior.
- Spoolman disabled/unreachable → spool picker hidden behind a "Connect Spoolman in Settings" hint;
  filament-profile mapping still works.
- `current_orca_printer_profile` not set → `/profiles` returns empty `filament_profiles`; the per-tray
  profile dropdown shows only “— none —” with a hint to pick make/model first.
- Machine catalog empty (no OrcaSlicer presets found) → MachinePicker shows an empty state + a pointer
  to Settings → "rescan profiles".

## Testing

**Backend (pytest):**
- `on_ams_change` merge: pre-seed `loaded_filaments` with `filament_profile`+`spoolman_spool_id` on slot
  0/1; feed a new AMS tray list (same slots, different colors) → merged result keeps the mappings by
  slot; a brand-new slot has neither; a dropped slot is gone.
- Create/patch printer round-trips `loaded_filaments` carrying `spoolman_spool_id` unchanged.

**Frontend (Vitest):**
- `MachinePicker`: selecting make→model→nozzle calls `onChange` with the resolved system preset name.
- Wizard: completing the Profile step and finishing calls `createPrinter` with
  `current_orca_printer_profile` set.
- `EditForm`: renders a slot's filament-profile `<select>` from a stubbed `/profiles`, and a Spoolman
  spool picker from stubbed `useSpools`; saving PATCHes `loaded_filaments` with `filament_profile` and
  `spoolman_spool_id` populated.
- Fleet `FilamentPicker`: selecting a spool PATCHes `spoolman_spool_id` (not `filament_id`).

## Out of scope
- Live AMS "probe" during the wizard (we chose create-first-map-after).
- Auto-suggesting a filament profile from the AMS-detected material (manual pick for now).
- Spoolman usage deduction wiring changes (the mapping is informational here).
- Resetting a tray's mapping when the physical filament changes (we chose always-preserve-by-slot).

## File structure (created / modified)
**Backend modify:** `app/services/printer_manager.py` (`on_ams_change` merge);
`tests/services/test_printer_manager.py` (or a new `test_ams_merge.py`).
**Frontend create:** `src/components/MachinePicker.tsx` (+ test).
**Frontend modify:** `src/api/printers.ts` (`LoadedFilament.spoolman_spool_id`);
`src/screens/PrintersScreen.tsx` (wizard Profile step + EditForm per-tray mapping);
`src/screens/FleetScreen.tsx` (consume `MachinePicker`; `FilamentPicker` → `spoolman_spool_id`).
**Docs:** update `docs/agent/*` via `themis-docs-sync` after implementation.
