# Spool Picker for Printer Slot Configuration — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the ad-hoc per-slot filament fields in both printer slot editors with a unified `SlotSpoolPicker` component that lets operators pick a specific Spoolman spool (or fall back to free-form Custom entry) as the primary way to configure what's loaded in a printer slot.

**Architecture:** UI-only change. The data model (`LoadedFilament` with `spoolman_spool_id`, `type`, `color`, `filament_profile`) already supports everything needed. A new shared component handles the spool combobox + Custom mode toggle; both `FleetScreen` FilamentPicker and the `PrintersScreen` slot editor consume it. The backend slicer already reads `filament_profile` from the slot at slice time — no backend change needed.

**Tech Stack:** React/TypeScript, existing `useSpools` hook, `parseOrcaProfiles` utility, Themis CSS design tokens.

---

## System context

Jobs specify filament requirements (type, color, filament profile for slicing). Printers own which specific spool is loaded per slot. At slice time the backend reads `loaded_filaments[slot].filament_profile` from the matched printer — so writing the resolved profile into the slot at configuration time is sufficient; no runtime Spoolman lookup is needed during slicing.

Future work: Themis will track remaining spool capacity and deduct usage after jobs complete. The `spoolman_spool_id` stored on the slot is the binding that enables this — it must uniquely identify the physical spool, not just a filament type.

---

## Scope

Two editors that manage `loaded_filaments`:

| Editor | Location | Use case |
|---|---|---|
| `FilamentPicker` modal | `src/screens/FleetScreen.tsx` | Day-to-day operational slot changes |
| Inline slot editor | `src/screens/PrintersScreen.tsx` | Initial printer setup |

Both are updated to use `SlotSpoolPicker`. No other files render slot filament fields.

---

## New component: `SlotSpoolPicker`

**File:** `src/components/SlotSpoolPicker.tsx`

### Props

```typescript
interface SlotSpoolPickerProps {
  slot: LoadedFilament;                  // current slot state
  printerPreset: string | null;         // current_orca_printer_profile for profile resolution
  spools: ApiSpool[];                   // pre-fetched by parent; empty when Spoolman off
  filamentProfiles: string[];           // full filament profile list from printer (fallback)
  onChange: (patch: Partial<LoadedFilament>) => void;
}
```

### Modes

**Custom mode** (default; also active when Spoolman is disabled or `spoolman_spool_id` is null):
- Shows free-form fields: type (text input), color (hex color picker), filament profile (dropdown of the printer's full filament profile list passed via `filamentProfiles` prop).
- Blank type/color = no filament constraint for queue matching.

**Spool mode** (active when `spoolman_spool_id` is set and the spool exists in the list):
- Type and color are read-only chips, auto-populated from `spool.filament.material` and `spool.filament.color_hex`.
- Filament profile is a `<select>` dropdown. Options are sourced from `parseOrcaProfiles(spool.filament)[printerPreset]` if that key exists and is non-empty; otherwise falls back to the full `filamentProfiles` list with a note "No mapped profiles — select manually".
- Selected state header shows: `[color swatch] #<id> <display name> — <remaining_weight>g remaining`.

**Degraded mode** (spool ID set but spool not found in list — e.g. deleted in Spoolman):
- Falls back to Custom mode using the last-saved type/color/profile values.
- Shows a small warning badge: "Spool #N not found in Spoolman".
- Operator can re-pick a spool or continue with the manual values.

### Combobox behavior

Matches the pattern from `SpoolmanMappingsPage`:
- Plain `<input className="input">` showing the selected spool's display name, or placeholder "Search spools…" when empty.
- Absolute-positioned dropdown below the input, `z-index: 50`, `background: var(--bg-2)`, `border: 1px solid var(--border-2)`, shadow, rounded corners.
- Filtered in real time as the user types against spool ID, filament name, vendor name, and material (case-insensitive).
- Each row: `[color swatch 12px] #<id> <vendor> <name> <material>` — e.g. `● #2 ELEGOO Sky Blue PLA`.
- Row padding `9px 14px`, hover `background: var(--bg-3)`.
- `onMouseDown` to commit selection (prevents blur firing before click registers).
- A "Custom" row is pinned at the top of the list, always visible, to allow explicit fallback.
- Rows sorted alphabetically by vendor+name; no weight shown in the list.

A small `×` button beside the selected spool name clears the selection and returns to Custom mode. Last-set type/color/profile values remain as editable starting points.

### `onChange` contract

| Action | Patch emitted |
|---|---|
| Pick spool | `{ spoolman_spool_id: id, type: spool.filament.material, color: '#' + spool.filament.color_hex, filament_profile: resolved_or_null }` |
| Clear to Custom | `{ spoolman_spool_id: null }` (type/color/profile unchanged) |
| Edit Custom field | `{ [field]: value }` |

---

## Parent editor changes

### FleetScreen `FilamentPicker`

- Add `useSpools(spoolmanEnabled)` call at the `FilamentPicker` level.
- Replace the current per-slot type/color/profile/spool-id fields with `<SlotSpoolPicker ... />` for each slot.
- Pass `printer.current_orca_printer_profile` as `printerPreset`.
- Pass the fetched spools list and the printer's filament profiles.

### PrintersScreen slot editor

- Same changes as FleetScreen: add `useSpools`, replace inline fields with `<SlotSpoolPicker ... />`.
- The save path (`updatePrinter`) is unchanged; it already persists the full `LoadedFilament` shape.

---

## When Spoolman is disabled

`useSpools(false)` returns `[]`. `SlotSpoolPicker` receives an empty list and renders Custom fields only — the combobox is not shown. Behavior is identical to current.

---

## Testing

### `SlotSpoolPicker` unit tests (`src/components/SlotSpoolPicker.test.tsx`)

- Custom mode renders type/color/profile fields when no spool selected.
- Typing in the combobox filters the spool list by name/vendor/material.
- Selecting a spool: `onChange` called with correct `spoolman_spool_id`, `type`, `color`, `filament_profile`.
- Selected state shows spool display name with remaining weight.
- Clearing spool: `onChange` called with `spoolman_spool_id: null`; Custom fields show previous values.
- Degraded mode: renders Custom fields + warning badge when spool ID not in list.
- Fallback profile list shown when no `orca_profiles` match for the printer preset.
- Empty spools list: combobox not rendered; Custom fields shown.

### FleetScreen integration tests

- Saving a slot with a selected spool includes correct `spoolman_spool_id`, `type`, `color`, `filament_profile` in the `updatePrinter` payload.

### PrintersScreen integration tests

- Same payload verification for the inline slot editor.
