# Spoolman `orca_profiles` — Filament Profile Auto-Mapping Design

## Goal

Allow operators to configure, per Spoolman filament × OrcaSlicer machine preset, which OrcaSlicer filament preset(s) to use. When a filament is selected for a job or loaded into a printer slot, the appropriate filament preset is auto-applied or restricted — eliminating manual hunting through hundreds of profile names.

---

## Data Model

### Spoolman custom field: `orca_profiles`

Stored on each Spoolman filament object in the `extra` (custom fields) bag, keyed `"orca_profiles"`. Value is a JSON-encoded object:

```json
{
  "Bambu Lab P1S 0.4 nozzle": ["Bambu PLA Basic @BBL P1S", "Generic PLA @BBL P1S"],
  "Elegoo UltraCraft Reflex @ECC 0.4": ["Generic PLA @ECC 0.4"]
}
```

- **Keys**: full OrcaSlicer machine preset name — the value of `printer.current_orca_printer_profile` for registered Themis printers. This is the exact string used by `ProfileIndex.compatible_profiles()`.
- **Values**: array of OrcaSlicer filament preset name strings — the same names returned by `GET /api/v1/printers/{id}/profiles` → `filament_profiles`.
- An empty array for a key means the mapping is cleared for that machine preset.

**Read path**: The existing `GET /api/v1/spoolman/filaments` endpoint is a raw passthrough that already includes Spoolman's `extra` field. No backend changes needed for reads. Spoolman stores the custom field as a JSON-encoded string (confirmed default value is `"{}"`), so the frontend must parse it: `JSON.parse(filament.extra?.orca_profiles ?? '{}')`. Malformed values are treated as empty mapping.

**Write path**: A new backend proxy endpoint (see Backend section) routes writes through the Themis server to avoid exposing the Spoolman API key to the browser.

**No new Themis DB table.** The mapping lives entirely in Spoolman.

---

## Two-Level Filament Profile Resolution

Filament profile selection operates at two levels, applied in order:

1. **Job-level** (`job_printer_configs.filament_profile`) — explicit override set at job creation/edit time. Used if non-null.
2. **Printer slot-level** (`loaded_filaments[i].filament_profile`) — the standing default set when a filament is loaded into a tool slot on the printer. Used if job-level is null.
3. **Fallback** — omit the filament preset; OrcaSlicer uses its internal default.

The `SliceRequest` construction in `jobs.py` must be updated to follow this order. If `job_printer_configs` does not already have a `filament_profile` column, one is added via `_migrate()`.

---

## Backend

### New endpoint: `PATCH /api/v1/spoolman/filaments/{filament_id}`

**Request body:**
```json
{ "orca_profiles": { "Bambu Lab P1S 0.4 nozzle": ["Profile A", "Profile B"] } }
```

**Behavior:**
- Returns `400` if Spoolman is not configured (no URL).
- Fetches the current filament from Spoolman to merge (don't overwrite other `extra` keys).
- Proxies `PATCH /api/v1/filament/{filament_id}` to the configured Spoolman instance with `{"extra": {"orca_profiles": ...}}` merged into existing `extra`.
- Returns the updated filament JSON on success; forwards Spoolman error status on failure.

No other backend endpoints are needed for this feature.

---

## Settings: Spoolman Mappings Page

A new section/tab under Settings, rendered only when Spoolman is enabled and reachable.

### Filament list with typeahead add

The page does not show all Spoolman filaments by default. Instead:

- Filaments that already have an `orca_profiles` entry are shown as expanded-by-default cards.
- A **typeahead combobox** at the top ("Search filaments to configure…") filters the full Spoolman filament list by name, material, or brand. Selecting a result adds it as a card in the editing area.
- Removing all profiles from a filament and saving clears its `orca_profiles` entry and removes the card from the page.

### Filament card

Each card shows:
- Color swatch, filament display name, material type
- Collapsible (default: expanded when newly added; collapsed when loaded from existing mappings)
- One row per unique `current_orca_printer_profile` across all registered Themis printers (de-duplicated)

### Machine preset row

Each row shows:
- Machine preset name (e.g., "Bambu Lab P1S 0.4 nozzle")
- **Typeahead multi-select with chips** — filters the list of compatible OrcaSlicer filament presets for that machine preset. Type to search, click to add as a chip, click chip × to remove. Compatible presets are loaded via `GET /api/v1/printers/{id}/profiles` → `filament_profiles` using one registered printer that shares that machine preset.
- Current selection pre-populated from `filament.extra?.orca_profiles?.[preset] ?? []`.

**Orphaned preset rows**: if a filament's `orca_profiles` key refers to a machine preset not used by any registered Themis printer, that row is shown read-only with a "no matching printer registered" note. Its value is preserved on save so data is not silently lost.

### Per-filament Save button

- Appears at the bottom of each card.
- Disabled until any change is made to that card's rows.
- On click: calls `PATCH /api/v1/spoolman/filaments/{id}` with the full updated `orca_profiles` object for that filament.
- Shows inline success ("Saved") or error message on the card.
- Saving does not affect other filament cards.

---

## PerPrinterConfig: Job-Level Filament Profile

When Spoolman is active and a filament is selected from the catalog (`filamentId != null`), a **"Filament profile" dropdown** appears in `PerPrinterConfig` (below the print profile selector or in the filament section).

### Dropdown content

The dropdown always has a first option: **"— use printer default —"** (maps to `filamentProfile: null`).

Below that separator:

- **If `orca_profiles[printer.current_orca_printer_profile]` exists**: show only the mapped profiles (filtered list). Label the section "From Spoolman mapping".
- **If no mapping for this printer**: show all compatible filament presets from `filamentProfiles` (same list as today, if surfaced). Label the section "All compatible profiles".

### Auto-select rule

- If the mapping returns **exactly one** profile: auto-set `filamentProfile` to that profile and show a muted read-only note instead of the full dropdown: *"Filament profile: [name] (from Spoolman mapping)"*. Operator can click to expand and override.
- If the mapping returns **multiple** profiles: show the restricted dropdown; operator picks one.
- If **no mapping**: show full dropdown defaulting to "use printer default".

### Reset

When the filament is deselected or changed, any auto-applied profile is cleared and the dropdown resets to "use printer default".

### Scope

The mapping only fires when `filamentId != null` (Spoolman catalog mode). Manual type-entry mode and tool-slot selection are unaffected.

---

## Fleet Screen: Slot-Level Filament Profile (Mapping-Aware)

When an operator assigns or edits the filament profile for a printer tool slot, the profile picker becomes mapping-aware:

- If the slot has a Spoolman filament assigned (`filamentId != null`) and that filament has `orca_profiles[printer.current_orca_printer_profile]`, the picker is filtered to mapped profiles (with typeahead if the list would be long).
- If no mapping, all compatible presets are available (current behavior).
- Same auto-select rule applies: exactly one mapped profile → auto-set silently; multiple → restricted dropdown.

This sets `loaded_filaments[i].filament_profile` on the printer, which becomes the standing default used by the slicer when the job defers (job-level `filamentProfile` is null).

---

## Error Handling

- **Spoolman unreachable at save time**: show an inline error on the filament card. Do not clear the local edits.
- **Spoolman returns non-2xx**: display the error message from Spoolman if available, otherwise a generic "Save failed" message.
- **Filament has no compatible presets for a machine preset**: the typeahead shows an empty state "No compatible filament presets found for this printer model."
- **`orca_profiles` value is malformed** (e.g., not a JSON object): treat as empty mapping; do not crash. Log a console warning.

---

## Out of Scope

- Syncing `orca_profiles` back to Spoolman automatically when printer profiles change.
- Bulk-editing multiple filaments in one save action.
- Per-nozzle-size overrides beyond what `current_orca_printer_profile` already encodes.
- Displaying or editing other Spoolman custom fields.
