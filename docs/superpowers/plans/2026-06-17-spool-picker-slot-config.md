# Spool Picker for Printer Slot Configuration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ad-hoc per-slot filament fields in both printer slot editors with a shared `SlotSpoolPicker` component that lets operators pick a specific Spoolman spool (or fall back to free-form Custom entry).

**Architecture:** UI-only change. `LoadedFilament` in `src/api/printers.ts` already has all needed fields (`type`, `color`, `filament_profile`, `spoolman_spool_id`). A new `SlotSpoolPicker` component owns the combobox + mode logic; `FleetScreen` FilamentPicker and `PrintersScreen` slot editor both consume it. The backend slicer reads `filament_profile` from the slot record at slice time — no backend changes.

**Tech Stack:** React/TypeScript, Vitest + Testing Library, existing `useSpools`/`useFilaments`/`parseOrcaProfiles` from `src/api/spoolman.ts`, Themis CSS tokens.

---

## File map

| File | Change |
|---|---|
| `src/components/SlotSpoolPicker.tsx` | **Create** — new shared component |
| `src/components/SlotSpoolPicker.test.tsx` | **Create** — unit tests |
| `src/screens/FleetScreen.tsx` | **Modify** — wire SlotSpoolPicker, drop getMappedProfiles/pickSpool |
| `src/screens/PrintersScreen.tsx` | **Modify** — wire SlotSpoolPicker, drop old pickSpool, add spoolman hooks |

---

## Key types (read before coding)

**`LoadedFilament`** — `src/api/printers.ts` lines 29-37:
```typescript
interface LoadedFilament {
  slot: number;
  filament_id: string | null;       // Bambu AMS code, unrelated to Spoolman
  name: string;
  type: string;
  color: string;
  filament_profile?: string | null;
  spoolman_spool_id?: string | null; // "2", "7", etc. — stringified Spoolman spool id
}
```

**`ApiSpool`** — `src/api/spoolman.ts` lines 49-60:
```typescript
interface ApiSpool {
  id: number;
  filament: { id: number; vendor?: { name: string }; name: string; material: string; color_hex?: string; };
  remaining_weight: number;
  used_weight: number;
}
```

**`ApiFilament`** — `src/api/spoolman.ts` lines 3-12 — note `extra` field that holds `orca_profiles`. `ApiSpool.filament` is a **subset** and does NOT have `extra`, so `parseOrcaProfiles` must be called on a full `ApiFilament` looked up by `spool.filament.id`.

**`parseOrcaProfiles(f: ApiFilament): Record<string, string[]>`** — returns a map of printer-preset-name → list of filament profile names. Returns `{}` on parse error.

---

## Task 1: `SlotSpoolPicker` — tests and component

**Files:**
- Create: `frontend/src/components/SlotSpoolPicker.test.tsx`
- Create: `frontend/src/components/SlotSpoolPicker.tsx`

---

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/SlotSpoolPicker.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SlotSpoolPicker } from './SlotSpoolPicker';
import type { LoadedFilament } from '../api/printers';
import type { ApiSpool, ApiFilament } from '../api/spoolman';

const baseSlot: LoadedFilament = {
  slot: 0, filament_id: null, name: 'Slot 1',
  type: '', color: '', filament_profile: null, spoolman_spool_id: null,
};

const spool2: ApiSpool = {
  id: 2, remaining_weight: 324, used_weight: 76,
  filament: { id: 10, vendor: { name: 'ELEGOO' }, name: 'Sky Blue PLA', material: 'PLA', color_hex: '87CEEB' },
};

const spool5: ApiSpool = {
  id: 5, remaining_weight: 980, used_weight: 20,
  filament: { id: 11, vendor: { name: 'Bambu' }, name: 'Basic Black PETG', material: 'PETG', color_hex: '111111' },
};

const filament10: ApiFilament = {
  id: 10, name: 'Sky Blue PLA', vendor: { id: 1, name: 'ELEGOO' }, material: 'PLA', color_hex: '87CEEB',
  extra: { orca_profiles: JSON.stringify(JSON.stringify({ 'Bambu X1': ['ELEGOO PLA @BBL X1C'] })) },
};

const noSpools: ApiSpool[] = [];
const spools = [spool2, spool5];
const filaments = [filament10];
const filamentProfiles = ['Generic PLA', 'Bambu PLA Basic @BBL X1C', 'ELEGOO PLA @BBL X1C'];

describe('SlotSpoolPicker', () => {
  it('shows Custom fields when no spools provided (Spoolman off)', () => {
    const onChange = vi.fn();
    render(
      <SlotSpoolPicker slot={baseSlot} printerPreset={null} spools={noSpools}
        filaments={[]} filamentProfiles={filamentProfiles} onChange={onChange} />
    );
    expect(screen.getByPlaceholderText('Type (e.g. PLA)')).toBeTruthy();
    expect(screen.getByPlaceholderText('Color (#hex)')).toBeTruthy();
    expect(screen.queryByPlaceholderText('Search spools…')).toBeNull();
  });

  it('shows spool combobox when spools are available', () => {
    render(
      <SlotSpoolPicker slot={baseSlot} printerPreset={null} spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={vi.fn()} />
    );
    expect(screen.getByPlaceholderText('Search spools…')).toBeTruthy();
  });

  it('filters spools by name/vendor/material as user types', async () => {
    const user = userEvent.setup();
    render(
      <SlotSpoolPicker slot={baseSlot} printerPreset={null} spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={vi.fn()} />
    );
    await user.click(screen.getByPlaceholderText('Search spools…'));
    await user.type(screen.getByPlaceholderText('Search spools…'), 'ELEGOO');
    expect(screen.getByText('#2 ELEGOO Sky Blue PLA PLA')).toBeTruthy();
    expect(screen.queryByText(/Bambu/)).toBeNull();
  });

  it('calls onChange with spool fields when a spool is selected', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <SlotSpoolPicker slot={baseSlot} printerPreset={null} spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={onChange} />
    );
    await user.click(screen.getByPlaceholderText('Search spools…'));
    fireEvent.mouseDown(screen.getByText('#2 ELEGOO Sky Blue PLA PLA'));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      spoolman_spool_id: '2',
      type: 'PLA',
      color: '#87CEEB',
    }));
  });

  it('shows selected spool with remaining weight after picking', () => {
    const slotWithSpool: LoadedFilament = { ...baseSlot, spoolman_spool_id: '2', type: 'PLA', color: '#87CEEB' };
    render(
      <SlotSpoolPicker slot={slotWithSpool} printerPreset={null} spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={vi.fn()} />
    );
    expect(screen.getByText(/324g remaining/)).toBeTruthy();
    expect(screen.getByText(/#2 ELEGOO Sky Blue PLA PLA/)).toBeTruthy();
  });

  it('calls onChange with spoolman_spool_id: null when cleared', async () => {
    const onChange = vi.fn();
    const slotWithSpool: LoadedFilament = { ...baseSlot, spoolman_spool_id: '2', type: 'PLA', color: '#87CEEB' };
    render(
      <SlotSpoolPicker slot={slotWithSpool} printerPreset={null} spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={onChange} />
    );
    await userEvent.setup().click(screen.getByLabelText('Clear spool selection'));
    expect(onChange).toHaveBeenCalledWith({ spoolman_spool_id: null });
  });

  it('shows warning badge and Custom fields in degraded mode (spool not in list)', () => {
    const slotWithMissingSpool: LoadedFilament = { ...baseSlot, spoolman_spool_id: '99', type: 'PLA', color: '#ff0000' };
    render(
      <SlotSpoolPicker slot={slotWithMissingSpool} printerPreset={null} spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={vi.fn()} />
    );
    expect(screen.getByText(/Spool #99 not found in Spoolman/)).toBeTruthy();
    expect(screen.getByPlaceholderText('Type (e.g. PLA)')).toBeTruthy();
  });

  it('shows resolved orca profiles dropdown when printerPreset matches', () => {
    const slotWithSpool: LoadedFilament = { ...baseSlot, spoolman_spool_id: '2', type: 'PLA', color: '#87CEEB' };
    render(
      <SlotSpoolPicker slot={slotWithSpool} printerPreset="Bambu X1" spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={vi.fn()} />
    );
    expect(screen.getByRole('option', { name: 'ELEGOO PLA @BBL X1C' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: 'Generic PLA' })).toBeNull();
  });

  it('falls back to full filamentProfiles when no orca_profiles match', () => {
    const slotWithSpool: LoadedFilament = { ...baseSlot, spoolman_spool_id: '5', type: 'PETG', color: '#111111' };
    render(
      <SlotSpoolPicker slot={slotWithSpool} printerPreset="Bambu X1" spools={spools}
        filaments={filaments} filamentProfiles={filamentProfiles} onChange={vi.fn()} />
    );
    expect(screen.getByText(/No mapped profiles/)).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Generic PLA' })).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run tests — verify they fail with "SlotSpoolPicker not found"**

```bash
cd frontend
npx vitest run src/components/SlotSpoolPicker.test.tsx
```

Expected: all 8 tests FAIL — component doesn't exist yet.

- [ ] **Step 3: Create `src/components/SlotSpoolPicker.tsx`**

```tsx
import { useState, useMemo } from 'react';
import type { LoadedFilament } from '../api/printers';
import type { ApiSpool, ApiFilament } from '../api/spoolman';
import { parseOrcaProfiles } from '../api/spoolman';

export interface SlotSpoolPickerProps {
  slot: LoadedFilament;
  printerPreset: string | null;
  spools: ApiSpool[];
  filaments: ApiFilament[];
  filamentProfiles: string[];
  onChange: (patch: Partial<LoadedFilament>) => void;
}

function spoolColor(spool: ApiSpool): string {
  return spool.filament.color_hex ? `#${spool.filament.color_hex}` : '#94a3b8';
}

function spoolRowLabel(spool: ApiSpool): string {
  const vendor = spool.filament.vendor?.name;
  return `#${spool.id} ${vendor ? `${vendor} ` : ''}${spool.filament.name} ${spool.filament.material}`;
}

export function SlotSpoolPicker({
  slot, printerPreset, spools, filaments, filamentProfiles, onChange,
}: SlotSpoolPickerProps) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);

  const selectedSpool = useMemo(
    () => spools.find(s => String(s.id) === slot.spoolman_spool_id) ?? null,
    [spools, slot.spoolman_spool_id],
  );

  const isCustom = !slot.spoolman_spool_id;
  const isDegraded = !isCustom && !selectedSpool;
  const showCombobox = spools.length > 0;

  const resolvedProfiles = useMemo(() => {
    if (!selectedSpool || !printerPreset) return null;
    const full = filaments.find(f => f.id === selectedSpool.filament.id);
    if (!full) return null;
    const profiles = parseOrcaProfiles(full)[printerPreset];
    return profiles && profiles.length > 0 ? profiles : null;
  }, [selectedSpool, printerPreset, filaments]);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return spools
      .filter(s => {
        if (!q) return true;
        const vendor = s.filament.vendor?.name ?? '';
        return (
          String(s.id).includes(q) ||
          s.filament.name.toLowerCase().includes(q) ||
          vendor.toLowerCase().includes(q) ||
          s.filament.material.toLowerCase().includes(q)
        );
      })
      .sort((a, b) => {
        const aKey = `${a.filament.vendor?.name ?? ''} ${a.filament.name}`.toLowerCase();
        const bKey = `${b.filament.vendor?.name ?? ''} ${b.filament.name}`.toLowerCase();
        return aKey.localeCompare(bKey);
      });
  }, [spools, query]);

  function pickSpool(spool: ApiSpool) {
    const full = filaments.find(f => f.id === spool.filament.id);
    const profiles = full && printerPreset ? (parseOrcaProfiles(full)[printerPreset] ?? null) : null;
    onChange({
      spoolman_spool_id: String(spool.id),
      type: spool.filament.material,
      color: spool.filament.color_hex ? `#${spool.filament.color_hex}` : '',
      filament_profile: profiles?.length === 1 ? profiles[0] : null,
    });
    setQuery('');
    setOpen(false);
  }

  function clearSpool() {
    onChange({ spoolman_spool_id: null });
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {isDegraded && (
        <div style={{
          fontSize: 12, color: 'var(--warn)', padding: '4px 8px',
          background: 'rgba(234,179,8,0.10)', border: '1px solid rgba(234,179,8,0.3)', borderRadius: 6,
        }}>
          Spool #{slot.spoolman_spool_id} not found in Spoolman
        </div>
      )}

      {showCombobox && (
        <div style={{ position: 'relative' }}>
          {selectedSpool ? (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '7px 10px', background: 'var(--bg-1)',
              border: '1px solid var(--border-1)', borderRadius: 8,
            }}>
              <span style={{ width: 12, height: 12, borderRadius: '50%', background: spoolColor(selectedSpool), flexShrink: 0 }} />
              <span style={{ flex: 1, fontSize: 13, color: 'var(--text-1)' }}>
                {spoolRowLabel(selectedSpool)} — {selectedSpool.remaining_weight}g remaining
              </span>
              <button
                onClick={clearSpool}
                aria-label="Clear spool selection"
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-3)', fontSize: 16, padding: 0, lineHeight: 1 }}
              >×</button>
            </div>
          ) : (
            <input
              className="input"
              placeholder="Search spools…"
              value={query}
              onChange={e => setQuery(e.target.value)}
              onFocus={() => setOpen(true)}
              onBlur={() => setTimeout(() => setOpen(false), 150)}
            />
          )}

          {open && !selectedSpool && (
            <div style={{
              position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
              background: 'var(--bg-2)', border: '1px solid var(--border-2)',
              borderRadius: 8, marginTop: 4,
              boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
              maxHeight: 240, overflowY: 'auto',
            }}>
              <div
                onMouseDown={clearSpool}
                style={{ padding: '9px 14px', cursor: 'pointer', fontSize: 13, color: 'var(--text-3)', borderBottom: '1px solid var(--border-1)' }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-3)')}
                onMouseLeave={e => (e.currentTarget.style.background = '')}
              >
                Custom
              </div>
              {filtered.length === 0 && (
                <div style={{ padding: '9px 14px', fontSize: 13, color: 'var(--text-3)' }}>No spools match</div>
              )}
              {filtered.map(spool => (
                <div
                  key={spool.id}
                  onMouseDown={() => pickSpool(spool)}
                  style={{ padding: '9px 14px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-3)')}
                  onMouseLeave={e => (e.currentTarget.style.background = '')}
                >
                  <span style={{ width: 12, height: 12, borderRadius: '50%', background: spoolColor(spool), flexShrink: 0 }} />
                  <span>{spoolRowLabel(spool)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {(isCustom || isDegraded) && (
        <>
          <input
            className="input"
            placeholder="Type (e.g. PLA)"
            value={slot.type}
            onChange={e => onChange({ type: e.target.value })}
          />
          <input
            className="input"
            placeholder="Color (#hex)"
            value={slot.color}
            onChange={e => onChange({ color: e.target.value })}
          />
          <select
            className="select"
            value={slot.filament_profile ?? ''}
            onChange={e => onChange({ filament_profile: e.target.value || null })}
          >
            <option value="">— no filament profile —</option>
            {filamentProfiles.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </>
      )}

      {!isCustom && !isDegraded && selectedSpool && (
        <>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <div style={{
              flex: 1, padding: '7px 10px', background: 'var(--bg-1)',
              border: '1px solid var(--border-1)', borderRadius: 8, fontSize: 13, color: 'var(--text-2)',
            }}>
              {selectedSpool.filament.material}
            </div>
            <div style={{
              width: 34, height: 34, borderRadius: 8, flexShrink: 0,
              background: spoolColor(selectedSpool), border: '1px solid var(--border-1)',
            }} />
          </div>
          <select
            className="select"
            value={slot.filament_profile ?? ''}
            onChange={e => onChange({ filament_profile: e.target.value || null })}
          >
            <option value="">— select filament profile —</option>
            {(resolvedProfiles ?? filamentProfiles).map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          {!resolvedProfiles && (
            <div style={{ fontSize: 11, color: 'var(--text-3)' }}>No mapped profiles — select manually</div>
          )}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests — verify all 8 pass**

```bash
npx vitest run src/components/SlotSpoolPicker.test.tsx
```

Expected: `Tests 8 passed (8)`

- [ ] **Step 5: Commit**

```bash
git add src/components/SlotSpoolPicker.tsx src/components/SlotSpoolPicker.test.tsx
git commit -m "feat(components): add SlotSpoolPicker for per-slot spool/filament config"
```

---

## Task 2: Wire `SlotSpoolPicker` into `FleetScreen` FilamentPicker

**Files:**
- Modify: `frontend/src/screens/FleetScreen.tsx`

**Context:** The `FilamentPicker` component (starting around line 259) is a self-contained component that manages slot state for a specific printer. It currently has:
- `getMappedProfiles(i)` — resolves orca profiles for slot i's spool → **delete** (logic moves into SlotSpoolPicker)
- `pickSpool(i, spoolId)` — auto-populates slot from spool → **delete** (replaced by SlotSpoolPicker's onChange)
- `updateSlot(i, patch)` — patches a single slot → **keep**
- A slot card render loop with inline color/type/name/profile/spool fields → **replace** slot filament fields with `<SlotSpoolPicker>`
- `useSpoolmanConfig()` → already present, keep it
- `useFilaments(spoolmanEnabled)` → already present, keep it
- `useSpools(spoolmanEnabled)` → **add** this hook

---

- [ ] **Step 1: Add `useSpools` import and hook call inside `FilamentPicker`**

At the top of the file, `useSpools` is already imported from `'../api/spoolman'` (it's used elsewhere). If not, add it to the existing import line:

```typescript
import { useSpoolmanConfig, useSpools, useFilaments, /* other existing imports */ } from '../api/spoolman';
```

Inside the `FilamentPicker` component body, find where `useFilaments` is called (e.g. `const filaments = useFilaments(spoolmanEnabled)`) and add `useSpools` immediately after:

```typescript
const filaments = useFilaments(spoolmanEnabled);
const spools = useSpools(spoolmanEnabled);
```

- [ ] **Step 2: Add `SlotSpoolPicker` import**

```typescript
import { SlotSpoolPicker } from '../components/SlotSpoolPicker';
```

- [ ] **Step 3: Delete `getMappedProfiles` and `pickSpool` from `FilamentPicker`**

Remove the entire `getMappedProfiles` function (it resolves orca profiles by looking up a spool's filament — now handled inside `SlotSpoolPicker`) and the entire `pickSpool` function (auto-populates slot from spool selection — now handled by `SlotSpoolPicker`'s `onChange`).

- [ ] **Step 4: Replace the slot filament field JSX with `SlotSpoolPicker`**

Inside the slot card render loop, find the per-slot fields for color, type, name, filament profile, and the spoolman spool selector. Replace those fields with:

```tsx
<SlotSpoolPicker
  slot={slot}
  printerPreset={machinePreset}
  spools={spools}
  filaments={filaments}
  filamentProfiles={filamentProfiles}
  onChange={patch => updateSlot(i, patch)}
/>
```

Keep the slot header row (slot label, remove button) and the `name` input (the free-text slot label) — only replace the filament-specific fields (type, color, filament_profile, the spoolman spool dropdown).

- [ ] **Step 5: Run the app and manually verify**

```bash
# Backend and frontend dev servers should already be running.
# Navigate to Fleet, click on a printer, open the filament editor.
# Confirm:
# - If Spoolman is disabled: only Custom fields show.
# - If Spoolman is enabled and has spools: combobox appears.
# - Picking a spool populates type/color and shows profile dropdown.
# - Clearing returns to Custom fields.
# - Save writes the correct payload.
```

- [ ] **Step 6: Run unit tests to confirm nothing regressed**

```bash
npx vitest run src/screens/FleetScreen.test.tsx 2>/dev/null || echo "no fleet test file"
npx vitest run src/components/SlotSpoolPicker.test.tsx
```

Expected: SlotSpoolPicker tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/screens/FleetScreen.tsx
git commit -m "feat(fleet): replace slot filament fields with SlotSpoolPicker"
```

---

## Task 3: Wire `SlotSpoolPicker` into `PrintersScreen` slot editor

**Files:**
- Modify: `frontend/src/screens/PrintersScreen.tsx`

**Context:** The `EditForm` component (starting around line 61) manages the inline slot editor. It currently has:
- `pickSpool(i, spoolId)` — auto-populates slot from spool, but does NOT set `filament_profile` → **delete** (replaced by SlotSpoolPicker)
- `updateSlot(i, patch)` — patches a single slot → **keep**
- Inline slot fields for color, type, name, filament profile, and a spool selector → **replace** filament fields with `<SlotSpoolPicker>`
- May already have `useSpoolmanConfig()` and/or `useFilaments()` — check and add what's missing.

---

- [ ] **Step 1: Add missing Spoolman hooks inside `EditForm`**

Check what's currently imported/used. Add whichever are missing:

```typescript
import { useSpoolmanConfig, useSpools, useFilaments } from '../api/spoolman';
```

Inside the `EditForm` component, add after the existing state declarations:

```typescript
const { config: spoolmanCfg } = useSpoolmanConfig();
const spoolmanEnabled = !!(spoolmanCfg?.enabled && spoolmanCfg?.url);
const spools = useSpools(spoolmanEnabled);
const filaments = useFilaments(spoolmanEnabled);
```

If `useSpoolmanConfig` or `useFilaments` is already called in `EditForm`, skip that line and reuse the existing variable.

- [ ] **Step 2: Add `SlotSpoolPicker` import**

```typescript
import { SlotSpoolPicker } from '../components/SlotSpoolPicker';
```

- [ ] **Step 3: Delete `pickSpool` from `EditForm`**

Remove the entire `pickSpool(i, spoolId)` function — its logic is now inside `SlotSpoolPicker`.

- [ ] **Step 4: Replace the slot filament field JSX with `SlotSpoolPicker`**

Inside the slot card render loop, replace the per-slot color picker, type select/input, filament profile select, and spoolman spool selector with:

```tsx
<SlotSpoolPicker
  slot={slot}
  printerPreset={preset}
  spools={spools}
  filaments={filaments}
  filamentProfiles={filamentProfiles}
  onChange={patch => updateSlot(i, patch)}
/>
```

`preset` is the `current_orca_printer_profile` state variable already in `EditForm`. Keep the slot header (slot label, remove button) and the `name` input — only replace the filament-specific fields.

- [ ] **Step 5: Run the app and manually verify**

```bash
# Navigate to Printers, click Edit on a printer.
# Confirm slot fields behave the same as FleetScreen:
# - Custom fields when Spoolman off.
# - Spool combobox when Spoolman on.
# - Save still calls updatePrinter with correct payload shape.
```

- [ ] **Step 6: Run unit tests**

```bash
npx vitest run src/components/SlotSpoolPicker.test.tsx
```

Expected: all 8 pass.

- [ ] **Step 7: Commit**

```bash
git add src/screens/PrintersScreen.tsx
git commit -m "feat(printers): replace slot filament fields with SlotSpoolPicker"
```

---

## Task 4: Integration tests for FleetScreen and PrintersScreen

**Files:**
- Modify: `frontend/src/screens/FleetScreen.test.tsx` (create if it doesn't exist)
- Modify: `frontend/src/screens/PrintersScreen.test.tsx` (create if it doesn't exist)

**Goal:** Verify that selecting a spool in each editor produces the correct `updatePrinter` payload — i.e., the spool-sourced `spoolman_spool_id`, `type`, `color`, and `filament_profile` are all written to `loaded_filaments` on save.

---

- [ ] **Step 1: Check which test files exist**

```bash
ls src/screens/FleetScreen.test.tsx src/screens/PrintersScreen.test.tsx 2>/dev/null
```

If a file already exists, add the new test cases to it. If it doesn't exist, create it as shown below.

- [ ] **Step 2: Write the FleetScreen slot integration test**

In `src/screens/FleetScreen.test.tsx` — add or create with this test:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { FleetScreen } from './FleetScreen';

const wrapper = ({ children }: { children: ReactNode }) => (
  <MemoryRouter initialEntries={['/fleet']}><Routes><Route path="/fleet" element={<>{children}</>} /></Routes></MemoryRouter>
);

const mockPrinter = {
  id: 1, name: 'Printer A', printer_type: 'bambu_x1', enabled: true,
  queue_on: false, awaiting_plate_clear: false, is_idle: true,
  current_orca_printer_profile: 'Bambu X1 Carbon 0.4 nozzle',
  orca_printer_profiles: ['Bambu X1 Carbon 0.4 nozzle'],
  connection_config: { host: '192.168.1.1', access_code: 'abc', serial: '123' },
  loaded_filaments: [{ slot: 0, filament_id: null, name: 'Slot 1', type: '', color: '', filament_profile: null, spoolman_spool_id: null }],
};

const mockSpool = {
  id: 3, remaining_weight: 500, used_weight: 0,
  filament: { id: 20, vendor: { name: 'ELEGOO' }, name: 'Space Grey PLA', material: 'PLA', color_hex: 'AAAAAA' },
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/api/v1/fleet')) return new Response(JSON.stringify([mockPrinter]), { status: 200 });
    if (url.includes('/api/v1/settings/spoolman')) return new Response(JSON.stringify({ enabled: true, url: 'http://spoolman.local', api_key: null }), { status: 200 });
    if (url.includes('/api/v1/spoolman/spools')) return new Response(JSON.stringify([mockSpool]), { status: 200 });
    if (url.includes('/api/v1/spoolman/filaments')) return new Response(JSON.stringify([]), { status: 200 });
    if (url.includes('/api/v1/printers/') && init?.method === 'PATCH') return new Response(JSON.stringify(mockPrinter), { status: 200 });
    return new Response('{}', { status: 200 });
  }));
});

describe('FleetScreen FilamentPicker + SlotSpoolPicker integration', () => {
  it('saves correct spool fields when a spool is selected and saved', async () => {
    const user = userEvent.setup();
    render(<FleetScreen />, { wrapper });

    // Open the filament editor modal for Printer A
    await user.click(await screen.findByTitle(/edit filament/i));

    // Pick a spool from the combobox
    const input = await screen.findByPlaceholderText('Search spools…');
    await user.click(input);
    fireEvent.mouseDown(screen.getByText(/#3 ELEGOO Space Grey PLA PLA/));

    // Save
    await user.click(screen.getByRole('button', { name: /save/i }));

    const putCalls = (fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([url, init]: [string, RequestInit]) => url.includes('/api/v1/printers/') && init?.method === 'PATCH'
    );
    expect(putCalls.length).toBeGreaterThan(0);
    const body = JSON.parse(putCalls[0][1].body as string);
    const slot = body.loaded_filaments[0];
    expect(slot.spoolman_spool_id).toBe('3');
    expect(slot.type).toBe('PLA');
    expect(slot.color).toBe('#AAAAAA');
  });
});
```

**Note:** The selector `screen.getByTitle(/edit filament/i)` may need to match the actual button label in `FleetScreen`. Read the FleetScreen JSX to find the exact button that opens `FilamentPicker` and use the correct `getByRole` / `getByTitle` / `getByText` matcher.

- [ ] **Step 3: Run the FleetScreen integration test**

```bash
npx vitest run src/screens/FleetScreen.test.tsx
```

Expected: PASS.

- [ ] **Step 4: Write the PrintersScreen slot integration test**

In `src/screens/PrintersScreen.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { PrintersScreen } from './PrintersScreen';

const wrapper = ({ children }: { children: ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;

const mockPrinter = {
  id: 1, name: 'Printer A', printer_type: 'bambu_x1', enabled: true,
  queue_on: false, awaiting_plate_clear: false, is_idle: true,
  current_orca_printer_profile: 'Bambu X1 Carbon 0.4 nozzle',
  orca_printer_profiles: ['Bambu X1 Carbon 0.4 nozzle'],
  connection_config: { host: '192.168.1.1', access_code: 'abc', serial: '123' },
  loaded_filaments: [{ slot: 0, filament_id: null, name: 'Slot 1', type: '', color: '', filament_profile: null, spoolman_spool_id: null }],
};

const mockSpool = {
  id: 7, remaining_weight: 800, used_weight: 0,
  filament: { id: 30, vendor: { name: 'Bambu' }, name: 'Basic White PLA', material: 'PLA', color_hex: 'FFFFFF' },
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/api/v1/printers') && !init?.method) return new Response(JSON.stringify([mockPrinter]), { status: 200 });
    if (url.includes('/api/v1/settings/spoolman')) return new Response(JSON.stringify({ enabled: true, url: 'http://spoolman.local', api_key: null }), { status: 200 });
    if (url.includes('/api/v1/spoolman/spools')) return new Response(JSON.stringify([mockSpool]), { status: 200 });
    if (url.includes('/api/v1/spoolman/filaments')) return new Response(JSON.stringify([]), { status: 200 });
    if (url.includes('/api/v1/printers/') && init?.method === 'PATCH') return new Response(JSON.stringify(mockPrinter), { status: 200 });
    if (url.includes('/api/v1/printers/profiles')) return new Response(JSON.stringify({ machine_presets: [], filament_presets: [] }), { status: 200 });
    return new Response('{}', { status: 200 });
  }));
});

describe('PrintersScreen slot editor + SlotSpoolPicker integration', () => {
  it('saves correct spool fields when a spool is selected and saved', async () => {
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });

    // Open the edit form for Printer A
    await user.click(await screen.findByRole('button', { name: /edit/i }));

    // Pick a spool
    const input = await screen.findByPlaceholderText('Search spools…');
    await user.click(input);
    fireEvent.mouseDown(screen.getByText(/#7 Bambu Basic White PLA PLA/));

    // Save
    await user.click(screen.getByRole('button', { name: /save/i }));

    const patchCalls = (fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([url, init]: [string, RequestInit]) => url.includes('/api/v1/printers/') && init?.method === 'PATCH'
    );
    expect(patchCalls.length).toBeGreaterThan(0);
    const body = JSON.parse(patchCalls[0][1].body as string);
    const slot = body.loaded_filaments[0];
    expect(slot.spoolman_spool_id).toBe('7');
    expect(slot.type).toBe('PLA');
    expect(slot.color).toBe('#FFFFFF');
  });
});
```

- [ ] **Step 5: Run the PrintersScreen integration test**

```bash
npx vitest run src/screens/PrintersScreen.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Run the full unit test suite to confirm no regressions**

```bash
npx vitest run src/
```

Expected: same baseline pass/fail count as before this plan (2 pre-existing spoolman WIP failures in `NewJobScreen.test.tsx` are expected and unrelated).

- [ ] **Step 7: Run typecheck**

```bash
npx tsc -b
```

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add src/screens/FleetScreen.test.tsx src/screens/PrintersScreen.test.tsx
git commit -m "test(fleet,printers): integration tests for SlotSpoolPicker spool selection payload"
```
