# Maintenance Tracking Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine the printer maintenance tracking feature (built on `feat/maintenance-tracking`) per user review feedback: surface the Settings→Maintenance link in the main app sidebar, restrict model-specific scope choices to vendor/models actually present in the fleet, let users add a maintenance item directly from a Fleet printer card, show a 👷 emoji + tooltip on a printer's card when maintenance is due (replacing the plain count badge), and let the printer's expanded card list + acknowledge due items.

**Architecture:** Entirely frontend — no backend/schema changes are needed. `GET /api/v1/maintenance/status` and `POST /api/v1/maintenance/printers/{id}/items/{id}/complete` (both already shipped) already provide everything required, including the "reset resets all causes" behavior (`mark_done` already resets `last_done_at` + both baselines together in one call — this plan only adds a verification test for it, no new code). The shared `MaintenanceItemForm`/`TriggerRow` (currently inline in `SettingsScreen.tsx`) get extracted into `frontend/src/components/` so both the Settings page and the new Fleet quick-add modal can reuse them without duplication.

**Tech Stack:** React 19 / TypeScript / Vite frontend, Vitest + Testing Library. No backend changes in this plan.

---

## Design notes (read before starting)

- **Fleet-scoped model list, not the full catalog.** `GET /api/v1/printers` (`fetchPrinters()` in `printers.ts`) already returns each printer's `current_orca_printer_profile`. Combined with the existing `fetchMachineCatalog()`, resolving to `(vendor, printer_model)` client-side needs no new backend endpoint — this mirrors the backend's own `maintenance_service.resolve_vendor_model` matching logic, reimplemented once client-side as `resolveVendorModelForProfile`/`resolveFleetVendorModels` in `frontend/src/api/maintenance.ts`.
- **This restriction applies everywhere the vendor/model picker for maintenance items appears** — both the Settings → Maintenance page and the new Fleet-card quick-add modal — since the user's request ("model-specific choices should be limited to the models currently in the fleet") is a general statement about the picker, not one specific surface.
- **The 👷 emoji REPLACES the existing `MaintenanceDueBadge` pill on Fleet cards.** Having both a "N due" pill and a hat emoji signaling the same state is redundant clutter; the emoji+tooltip is the richer, more specific replacement the user described. `MaintenanceDueBadge.tsx` and its test become dead code and are deleted in this plan (no other consumer exists — it was added in the prior plan specifically for this exact use).
- **Requirement "resetting resets all causes" is already satisfied.** `POST /api/v1/maintenance/printers/{id}/items/{id}/complete` → `maintenance_service.mark_done` resets `last_done_at`, `baseline_job_count`, AND `baseline_print_seconds` together in one call — there is no way to reset only the trigger that fired. This plan adds one verification test confirming this (already covered indirectly by an existing backend test, but this plan adds an explicit frontend-facing confirmation) and otherwise just calls the existing `completeMaintenanceItem()` client function for the new "Acknowledge" button.
- **The quick-add button lives on the expanded card only** (not the compact tile/row cards), since that's the natural "detailed" action area already hosting the Edit/Snapshot buttons — a deliberate scope choice to avoid cluttering the compact card densities, not an oversight.
- **Tooltip interaction**: the hat's wrapping `<span>` carries a native `title` attribute (desktop hover) AND a click handler that toggles a small custom popover (mobile tap — `title` doesn't reliably show on touch). The click handler calls `stopPropagation()` since the hat sits inside an already-`onClick`-wired card (tapping it must not also expand/collapse the card).
- **Prop rename**: Task 9 of the original plan introduced `dueCountByPrinter: Record<string, number>` threaded through `FleetGrid`/`FleetRows`/`PrinterTile`/`PrinterRow`/`PrinterExpandedCard`. This plan renames it to `dueRowsByPrinter: Record<string, MaintenanceStatusRow[]>` (full rows, not just a count) everywhere it appears, since the hat tooltip needs item names and the new expanded-card list needs item IDs — one shared structure serves both instead of three separate ones.

---

### Task 1: Sidebar nav — add Maintenance link

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`
- Test: `frontend/src/components/Sidebar.test.tsx`

- [ ] **Step 1: Write the failing test**

In `frontend/src/components/Sidebar.test.tsx`, extend the existing `'shows settings sub-items when on /settings/* and not collapsed'` test (around line 189-195):

```typescript
  it('shows settings sub-items when on /settings/* and not collapsed', () => {
    renderOnSettings(false);
    expect(screen.getByText('Tags')).toBeTruthy();
    expect(screen.getByText('Print defaults')).toBeTruthy();
    expect(screen.getByText('Maintenance')).toBeTruthy();
    expect(screen.getByText('Webhooks')).toBeTruthy();
    expect(screen.queryByText('Filament Mappings')).toBeNull(); // spoolman disabled
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/Sidebar.test.tsx`
Expected: FAIL — `screen.getByText('Maintenance')` throws (not found).

- [ ] **Step 3: Add the nav entry**

In `frontend/src/components/Sidebar.tsx`, the `settingsSubItems` array currently reads (lines 60-68):

```typescript
  const settingsSubItems = [
    { to: '/settings/tags',             label: 'Tags' },
    { to: '/settings/print',            label: 'Print defaults' },
    { to: '/settings/spoolman',         label: 'Spoolman' },
    ...(spoolmanEnabled ? [{ to: '/settings/spoolman-mappings', label: 'Filament Mappings' }] : []),
    { to: '/settings/webhook',          label: 'Webhooks' },
    { to: '/settings/fleet-backup',     label: 'Fleet backup' },
    { to: '/settings/about',            label: 'About' },
  ];
```

Add the Maintenance entry right after "Print defaults" (matching the same grouping order as the Settings screen's own "Workshop" section):

```typescript
  const settingsSubItems = [
    { to: '/settings/tags',             label: 'Tags' },
    { to: '/settings/print',            label: 'Print defaults' },
    { to: '/settings/maintenance',       label: 'Maintenance' },
    { to: '/settings/spoolman',         label: 'Spoolman' },
    ...(spoolmanEnabled ? [{ to: '/settings/spoolman-mappings', label: 'Filament Mappings' }] : []),
    { to: '/settings/webhook',          label: 'Webhooks' },
    { to: '/settings/fleet-backup',     label: 'Fleet backup' },
    { to: '/settings/about',            label: 'About' },
  ];
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/Sidebar.test.tsx`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/components/Sidebar.test.tsx
git commit -m "feat(maintenance): add Maintenance link to the main sidebar's settings sub-nav"
```

---

### Task 2: Extract MaintenanceItemForm into a shared component

**Files:**
- Create: `frontend/src/components/MaintenanceItemForm.tsx`
- Modify: `frontend/src/screens/SettingsScreen.tsx`
- Test: `frontend/src/screens/SettingsScreen.maintenance.test.tsx` (should need NO changes — this is a pure refactor with no behavior change; the existing tests are your regression guard)

This is a pure extraction — moving code, not changing behavior. There is no new test to write; the existing `SettingsScreen.maintenance.test.tsx` (2 tests) must still pass unchanged afterward, proving nothing broke.

- [ ] **Step 1: Confirm the current baseline passes**

Run: `cd frontend && npx vitest run src/screens/SettingsScreen.maintenance.test.tsx`
Expected: PASS (2 tests) — this is your before-snapshot.

- [ ] **Step 2: Create the shared component file**

Create `frontend/src/components/MaintenanceItemForm.tsx`:

```tsx
// frontend/src/components/MaintenanceItemForm.tsx
import { Icons } from './icons';
import type { MaintenanceTrigger, MaintenanceTemplate } from '../api/maintenance';
import type { FleetVendorModel } from '../api/maintenance';

export const TRIGGER_LABEL: Record<MaintenanceTrigger['trigger_type'], string> = {
  calendar: 'Calendar', job_time: 'Print time', job_count: 'Job count',
};

export function triggerChipText(t: MaintenanceTrigger): string {
  if (t.trigger_type === 'job_count') return `${t.amount} jobs`;
  if (t.trigger_type === 'job_time') return `${t.amount}h operating`;
  return `${t.amount} ${t.unit ?? 'months'}`;
}

export interface ItemDraft {
  name: string;
  scope: 'general' | 'model';
  machine_vendor: string;
  machine_model: string;
  triggers: MaintenanceTrigger[];
}

export function emptyDraft(): ItemDraft {
  return { name: '', scope: 'general', machine_vendor: '', machine_model: '', triggers: [] };
}

export function draftFromTemplate(t: MaintenanceTemplate): ItemDraft {
  return { name: t.name, scope: 'general', machine_vendor: '', machine_model: '', triggers: t.triggers };
}

export function TriggerRow({ trigger, onChange, onRemove }: {
  trigger: MaintenanceTrigger; onChange: (t: MaintenanceTrigger) => void; onRemove: () => void;
}) {
  return (
    <div className="row gap-2" style={{ alignItems: 'center' }}>
      <select className="select sm" value={trigger.trigger_type}
              onChange={e => onChange({ ...trigger, trigger_type: e.target.value as MaintenanceTrigger['trigger_type'], unit: e.target.value === 'calendar' ? 'months' : null })}>
        <option value="calendar">Calendar</option>
        <option value="job_time">Print time (hours)</option>
        <option value="job_count">Job count</option>
      </select>
      <input type="number" min={0} className="input sm" style={{ width: 80 }}
             value={trigger.amount}
             onChange={e => onChange({ ...trigger, amount: Number(e.target.value) })} />
      {trigger.trigger_type === 'calendar' && (
        <select className="select sm" value={trigger.unit ?? 'months'}
                onChange={e => onChange({ ...trigger, unit: e.target.value as MaintenanceTrigger['unit'] })}>
          <option value="hours">hours</option>
          <option value="days">days</option>
          <option value="weeks">weeks</option>
          <option value="months">months</option>
        </select>
      )}
      <button type="button" className="btn ghost icon sm" onClick={onRemove}>{Icons.x}</button>
    </div>
  );
}

export function MaintenanceItemForm({ draft, catalog, onChange, onSave, onCancel }: {
  draft: ItemDraft; catalog: FleetVendorModel[];
  onChange: (d: ItemDraft) => void; onSave: () => void; onCancel: () => void;
}) {
  const vendors = Array.from(new Set(catalog.map(c => c.vendor))).sort();
  const models = Array.from(new Set(catalog.filter(c => c.vendor === draft.machine_vendor).map(c => c.printer_model))).sort();

  return (
    <div className="col gap-2" style={{ padding: 16, border: '1px solid var(--border-2)', borderRadius: 8, background: 'var(--bg-2)' }}>
      <input className="input" placeholder="Maintenance item name" value={draft.name}
             onChange={e => onChange({ ...draft, name: e.target.value })} />

      <div className="row gap-2">
        <label className="row gap-1" style={{ alignItems: 'center' }}>
          <input type="radio" checked={draft.scope === 'general'}
                 onChange={() => onChange({ ...draft, scope: 'general' })} />
          General (any printer)
        </label>
        <label className="row gap-1" style={{ alignItems: 'center' }}>
          <input type="radio" checked={draft.scope === 'model'}
                 onChange={() => onChange({ ...draft, scope: 'model' })} />
          Model-specific
        </label>
      </div>

      {draft.scope === 'model' && (
        <div className="col gap-1">
          <div className="row gap-2">
            <select className="select" value={draft.machine_vendor}
                    onChange={e => onChange({ ...draft, machine_vendor: e.target.value, machine_model: '' })}>
              <option value="">Vendor…</option>
              {vendors.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
            <select className="select" value={draft.machine_model} disabled={!draft.machine_vendor}
                    onChange={e => onChange({ ...draft, machine_model: e.target.value })}>
              <option value="">Model…</option>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          {catalog.length === 0 && (
            <div className="tiny muted">No printers in your fleet have a resolved machine profile yet.</div>
          )}
        </div>
      )}

      <div className="col gap-1">
        <div className="tiny muted">Triggers (due on whichever fires first)</div>
        {draft.triggers.map((t, i) => (
          <TriggerRow key={i} trigger={t}
                      onChange={next => onChange({ ...draft, triggers: draft.triggers.map((x, j) => j === i ? next : x) })}
                      onRemove={() => onChange({ ...draft, triggers: draft.triggers.filter((_, j) => j !== i) })} />
        ))}
        <button type="button" className="btn ghost sm" style={{ alignSelf: 'flex-start' }}
                onClick={() => onChange({ ...draft, triggers: [...draft.triggers, { trigger_type: 'job_count', amount: 10, unit: null }] })}>
          {Icons.plus} Add trigger
        </button>
      </div>

      <div className="row gap-2">
        <button className="btn primary sm" onClick={onSave}>Save</button>
        <button className="btn sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
```

Note: this is byte-for-byte the same as the code currently in `SettingsScreen.tsx`, with two deliberate, minimal changes: (1) the `catalog` prop type narrows from `MachinePreset[]` to `FleetVendorModel[]` (a type defined in Task 3 — for now this import will not resolve; that's expected and fixed by Task 3, which must run before this task's tests can pass end-to-end — see the note in Step 4 below), and (2) a small "no printers in your fleet have a resolved machine profile yet" empty-state hint was added under the vendor/model selects, since Task 3's fleet-restricted catalog can legitimately be empty for a farm with no Laminus catalog resolution yet, unlike the old full-catalog version which was rarely empty.

- [ ] **Step 3: Update `SettingsScreen.tsx` to import from the shared file instead of defining locally**

In `frontend/src/screens/SettingsScreen.tsx`:

1. Add the import near the top of the file, alongside the other component imports:
```typescript
import {
  MaintenanceItemForm, TriggerRow, TRIGGER_LABEL, triggerChipText,
  emptyDraft, draftFromTemplate, type ItemDraft,
} from '../components/MaintenanceItemForm';
```

2. Delete the now-duplicated local definitions between the `// Maintenance page` section comment and `function MaintenanceItemRow` — specifically remove these (they now live in the shared file): `TRIGGER_LABEL` constant, `triggerChipText` function, `ItemDraft` interface, `emptyDraft` function, `draftFromTemplate` function, `TriggerRow` function, `MaintenanceItemForm` function. Leave `MaintenanceItemRow` and `MaintenancePage` in place (unchanged in this task — Task 4 modifies `MaintenancePage`).

3. `MaintenanceItemRow` still uses `TRIGGER_LABEL`/`triggerChipText` (now imported) — no change needed to its body, only to where those symbols come from.

- [ ] **Step 4: Run tests to verify nothing broke**

This task alone will NOT type-check cleanly yet, because `MaintenanceItemForm.tsx` imports `FleetVendorModel` from `../api/maintenance`, which doesn't exist until Task 3. Two acceptable orders:

- **Recommended:** do Task 3 first (it has no dependency on this task), then come back and do this task — `npm run build` will pass cleanly at the end of Task 2 if done in that order.
- **If doing Task 2 first anyway:** it's fine for `npm run build` to fail at the end of this task specifically due to the missing `FleetVendorModel` export — note this in your report as an expected, temporary intermediate state, and do NOT attempt to work around it (e.g., don't inline a placeholder type) — Task 3 supplies the real one next.

Either way, once both Task 2 and Task 3 are done, run:
Run: `cd frontend && npx vitest run src/screens/SettingsScreen.maintenance.test.tsx`
Expected: PASS (same 2 tests as the Step 1 baseline — proving the extraction changed nothing observable).

Run: `cd frontend && npx vitest run` (full suite) and `cd frontend && npm run build`
Expected: PASS, no regressions, clean type-check.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MaintenanceItemForm.tsx frontend/src/screens/SettingsScreen.tsx
git commit -m "refactor(maintenance): extract MaintenanceItemForm into a shared component"
```

(If you did Task 3 first as recommended, this commit will already build cleanly. If you did this task first, make this commit anyway — the working tree is expected to be in a temporarily-broken intermediate state until Task 3's commit lands, which is normal for a plan executed as ordered tasks.)

---

### Task 3: Fleet-scoped vendor/model resolver

**Files:**
- Modify: `frontend/src/api/maintenance.ts`
- Test: `frontend/src/api/maintenance.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/api/maintenance.test.ts`:

```typescript
import { resolveVendorModelForProfile, resolveFleetVendorModels } from './maintenance';

describe('resolveVendorModelForProfile', () => {
  const catalog = [
    { name: 'Elegoo Centauri Carbon 0.4 nozzle', vendor: 'Elegoo', printer_model: 'Centauri Carbon', nozzle: '0.4', source: 'system' as const },
    { name: 'Bambu Lab X1 Carbon 0.4 nozzle', vendor: 'Bambu Lab', printer_model: 'X1 Carbon', nozzle: '0.4', source: 'system' as const },
  ];

  it('resolves a matching profile name to its vendor/model', () => {
    expect(resolveVendorModelForProfile('Elegoo Centauri Carbon 0.4 nozzle', catalog))
      .toEqual({ vendor: 'Elegoo', printer_model: 'Centauri Carbon' });
  });

  it('returns null for an unset or unmatched profile', () => {
    expect(resolveVendorModelForProfile(null, catalog)).toBeNull();
    expect(resolveVendorModelForProfile('Unknown Preset', catalog)).toBeNull();
  });
});

describe('resolveFleetVendorModels', () => {
  const catalog = [
    { name: 'Elegoo Centauri Carbon 0.4 nozzle', vendor: 'Elegoo', printer_model: 'Centauri Carbon', nozzle: '0.4', source: 'system' as const },
    { name: 'Bambu Lab X1 Carbon 0.4 nozzle', vendor: 'Bambu Lab', printer_model: 'X1 Carbon', nozzle: '0.4', source: 'system' as const },
  ];

  it('dedupes fleet printers to a sorted, unique vendor/model list', () => {
    const printers = [
      { current_orca_printer_profile: 'Elegoo Centauri Carbon 0.4 nozzle' },
      { current_orca_printer_profile: 'Elegoo Centauri Carbon 0.4 nozzle' }, // duplicate printer, same model
      { current_orca_printer_profile: 'Bambu Lab X1 Carbon 0.4 nozzle' },
      { current_orca_printer_profile: null }, // unset profile, ignored
      { current_orca_printer_profile: 'Totally Unknown Printer' }, // unresolvable, ignored
    ];
    expect(resolveFleetVendorModels(printers, catalog)).toEqual([
      { vendor: 'Bambu Lab', printer_model: 'X1 Carbon' },
      { vendor: 'Elegoo', printer_model: 'Centauri Carbon' },
    ]);
  });

  it('returns an empty list when the fleet has no resolvable profiles', () => {
    expect(resolveFleetVendorModels([{ current_orca_printer_profile: null }], catalog)).toEqual([]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/api/maintenance.test.ts`
Expected: FAIL — `resolveVendorModelForProfile`/`resolveFleetVendorModels` are not exported from `./maintenance` yet.

- [ ] **Step 3: Add the resolver functions and hook**

In `frontend/src/api/maintenance.ts`, add the import at the top (alongside the existing `import { useCallback, useEffect, useState } from 'react';`):

```typescript
import { fetchPrinters, fetchMachineCatalog, type ApiPrinter, type MachinePreset } from './printers';
```

Add this new type and these new functions (a sensible place is right after the existing `MaintenanceStatusRow` interface, before the `request<T>` helper):

```typescript
export interface FleetVendorModel {
  vendor: string;
  printer_model: string;
}

export function resolveVendorModelForProfile(
  profile: string | null,
  catalog: Pick<MachinePreset, 'name' | 'vendor' | 'printer_model'>[],
): FleetVendorModel | null {
  if (!profile) return null;
  const match = catalog.find(c => c.name === profile);
  return match ? { vendor: match.vendor, printer_model: match.printer_model } : null;
}

export function resolveFleetVendorModels(
  printers: Pick<ApiPrinter, 'current_orca_printer_profile'>[],
  catalog: Pick<MachinePreset, 'name' | 'vendor' | 'printer_model'>[],
): FleetVendorModel[] {
  const seen = new Set<string>();
  const result: FleetVendorModel[] = [];
  for (const p of printers) {
    const resolved = resolveVendorModelForProfile(p.current_orca_printer_profile, catalog);
    if (!resolved) continue;
    const key = `${resolved.vendor} ${resolved.printer_model}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(resolved);
  }
  return result.sort((a, b) =>
    a.vendor.localeCompare(b.vendor) || a.printer_model.localeCompare(b.printer_model));
}

export function useFleetVendorModels(): FleetVendorModel[] {
  const [models, setModels] = useState<FleetVendorModel[]>([]);
  useEffect(() => {
    let alive = true;
    Promise.all([fetchPrinters(), fetchMachineCatalog()])
      .then(([printers, catalog]) => { if (alive) setModels(resolveFleetVendorModels(printers, catalog)); })
      .catch(console.error);
    return () => { alive = false; };
  }, []);
  return models;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/api/maintenance.test.ts`
Expected: PASS (all tests, including the 4 new ones).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/maintenance.ts frontend/src/api/maintenance.test.ts
git commit -m "feat(maintenance): add fleet-scoped vendor/model resolver"
```

---

### Task 4: Wire the fleet-scoped catalog into Settings → Maintenance

**Files:**
- Modify: `frontend/src/screens/SettingsScreen.tsx`
- Test: `frontend/src/screens/SettingsScreen.maintenance.test.tsx`

- [ ] **Step 1: Write the failing test**

The existing `SettingsScreen.maintenance.test.tsx` stubs `/api/v1/printers/orca-machine-catalog` to return `[]` (empty) and never stubs `/api/v1/printers` at all — meaning after this task's change, the vendor/model select in the "add template as item" flow would have zero options (since there'd be no fleet printers to resolve). Update the test file's `stubFetch()` to add a non-empty catalog and a matching printer, and add one new test asserting the model-specific option list is fleet-scoped. Replace the existing `stubFetch` function with:

```typescript
function stubFetch() {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/v1/maintenance/items' && (!init || init.method === undefined)) {
      return new Response(JSON.stringify([{
        id: 1, name: 'Wash build plate', scope: 'general', machine_vendor: null,
        machine_model: null, enabled: true, notes: null,
        triggers: [{ id: 1, trigger_type: 'job_count', amount: 10, unit: null }],
      }]), { status: 200 });
    }
    if (url === '/api/v1/maintenance/items' && init?.method === 'POST') {
      return new Response(JSON.stringify({
        id: 2, name: 'Clean fans', scope: 'general', machine_vendor: null,
        machine_model: null, enabled: true, notes: null,
        triggers: [{ trigger_type: 'calendar', amount: 3, unit: 'months' }],
      }), { status: 201 });
    }
    if (url === '/api/v1/maintenance/templates') {
      return new Response(JSON.stringify([
        { name: 'Clean fans', description: 'Blow out dust.', triggers: [{ trigger_type: 'calendar', amount: 3, unit: 'months' }] },
      ]), { status: 200 });
    }
    if (url === '/api/v1/printers/orca-machine-catalog') {
      return new Response(JSON.stringify([
        { name: 'Elegoo Centauri Carbon 0.4 nozzle', vendor: 'Elegoo', printer_model: 'Centauri Carbon', nozzle: '0.4', source: 'system' },
        { name: 'Bambu Lab X1 Carbon 0.4 nozzle', vendor: 'Bambu Lab', printer_model: 'X1 Carbon', nozzle: '0.4', source: 'system' },
      ]), { status: 200 });
    }
    if (url === '/api/v1/printers') {
      return new Response(JSON.stringify([
        { id: 1, name: 'P1', current_orca_printer_profile: 'Elegoo Centauri Carbon 0.4 nozzle' },
      ]), { status: 200 });
    }
    // Other settings sub-pages' unrelated calls (spoolman config, queue config, etc.)
    return new Response(JSON.stringify({}), { status: 200 });
  }));
}
```

Add a new test to the `describe` block:

```typescript
  it('limits model-specific vendor/model choices to the current fleet', async () => {
    render(<MemoryRouter initialEntries={['/settings/maintenance']}><SettingsScreen /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Wash build plate')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /new item/i }));
    fireEvent.click(screen.getByLabelText(/model-specific/i));

    await waitFor(() => expect(screen.getByRole('option', { name: 'Elegoo' })).toBeInTheDocument());
    expect(screen.queryByRole('option', { name: 'Bambu Lab' })).toBeNull(); // no Bambu printer in the fleet fixture
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/screens/SettingsScreen.maintenance.test.tsx`
Expected: FAIL — the vendor select still lists both "Elegoo" and "Bambu Lab" (current code uses the full `fetchMachineCatalog()` result, unfiltered by fleet).

- [ ] **Step 3: Switch `MaintenancePage` to the fleet-scoped resolver**

In `frontend/src/screens/SettingsScreen.tsx`, add the import:

```typescript
import { useFleetVendorModels } from '../api/maintenance';
```

In `MaintenancePage()`, remove the local catalog state and its fetch, replacing with the hook. Current code:

```typescript
  const { items, refetch } = useMaintenanceItems();
  const [templates, setTemplates] = useState<MaintenanceTemplate[]>([]);
  const [catalog, setCatalog] = useState<MachinePreset[]>([]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<ItemDraft>(emptyDraft());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getMaintenanceTemplates().then(setTemplates).catch(console.error);
    fetchMachineCatalog().then(setCatalog).catch(console.error);
  }, []);
```

Replace with:

```typescript
  const { items, refetch } = useMaintenanceItems();
  const [templates, setTemplates] = useState<MaintenanceTemplate[]>([]);
  const catalog = useFleetVendorModels();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<ItemDraft>(emptyDraft());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getMaintenanceTemplates().then(setTemplates).catch(console.error);
  }, []);
```

Now remove the now-unused import `import { fetchMachineCatalog, type MachinePreset } from '../api/printers';` from the top of `SettingsScreen.tsx` — confirm first (`grep -n "fetchMachineCatalog\|MachinePreset" frontend/src/screens/SettingsScreen.tsx`) that no other code in the file still references either symbol; if the grep comes back clean except for that import line itself, delete the line. TypeScript's `noUnusedLocals` will fail the build if you leave an unused import in place, so this is not optional polish — leaving it will break Step 4.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/screens/SettingsScreen.maintenance.test.tsx`
Expected: PASS (3 tests: the original 2 plus the new fleet-scoping test).

Run: `cd frontend && npx vitest run` (full suite) and `cd frontend && npm run build`
Expected: PASS, no regressions, clean type-check (confirms the unused-import removal was correct and complete).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/screens/SettingsScreen.tsx frontend/src/screens/SettingsScreen.maintenance.test.tsx
git commit -m "feat(maintenance): limit Settings model-specific picker to fleet vendor/models"
```

---

### Task 5: DueMaintenanceHat tooltip component

**Files:**
- Create: `frontend/src/components/DueMaintenanceHat.tsx`
- Test: `frontend/src/components/DueMaintenanceHat.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/DueMaintenanceHat.test.tsx`:

```tsx
// frontend/src/components/DueMaintenanceHat.test.tsx
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DueMaintenanceHat } from './DueMaintenanceHat';

describe('DueMaintenanceHat', () => {
  it('renders nothing when no items are due', () => {
    const { container } = render(<DueMaintenanceHat dueItemNames={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders the hat with a title listing due items for desktop hover', () => {
    render(<DueMaintenanceHat dueItemNames={['Wash build plate', 'Clean fans']} />);
    expect(screen.getByTitle('Wash build plate, Clean fans')).toBeInTheDocument();
  });

  it('toggles a visible tooltip popover on click, for mobile tap', () => {
    render(<DueMaintenanceHat dueItemNames={['Wash build plate']} />);
    expect(screen.queryByText('Wash build plate', { selector: 'div' })).toBeNull();
    fireEvent.click(screen.getByTitle('Wash build plate'));
    expect(screen.getByText('Wash build plate', { selector: 'div' })).toBeInTheDocument();
    fireEvent.click(screen.getByTitle('Wash build plate'));
    expect(screen.queryByText('Wash build plate', { selector: 'div' })).toBeNull();
  });

  it('stops click propagation so it does not trigger a parent card click', () => {
    const onCardClick = vi.fn();
    render(
      <div onClick={onCardClick}>
        <DueMaintenanceHat dueItemNames={['Wash build plate']} />
      </div>
    );
    fireEvent.click(screen.getByTitle('Wash build plate'));
    expect(onCardClick).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/DueMaintenanceHat.test.tsx`
Expected: FAIL — `Cannot find module './DueMaintenanceHat'`.

- [ ] **Step 3: Write the component**

Create `frontend/src/components/DueMaintenanceHat.tsx`:

```tsx
// frontend/src/components/DueMaintenanceHat.tsx
import { useState } from 'react';

export function DueMaintenanceHat({ dueItemNames }: { dueItemNames: string[] }) {
  const [open, setOpen] = useState(false);
  if (dueItemNames.length === 0) return null;

  const label = dueItemNames.join(', ');

  return (
    <span
      style={{ position: 'relative', display: 'inline-flex', cursor: 'pointer', lineHeight: 1 }}
      title={label}
      onClick={e => { e.stopPropagation(); setOpen(v => !v); }}
    >
      👷
      {open && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, marginTop: 4, zIndex: 20,
          background: 'var(--bg-3)', border: '1px solid var(--border-2)', color: 'var(--text-1)',
          padding: '6px 10px', borderRadius: 0, fontSize: 12, whiteSpace: 'nowrap',
          boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
        }}>
          {label}
        </div>
      )}
    </span>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/DueMaintenanceHat.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/DueMaintenanceHat.tsx frontend/src/components/DueMaintenanceHat.test.tsx
git commit -m "feat(maintenance): add due-maintenance hat emoji + tooltip component"
```

---

### Task 6: Wire the hat into Fleet cards, remove the old badge

**Files:**
- Modify: `frontend/src/screens/FleetScreen.tsx`
- Modify: `frontend/src/screens/FleetScreen.test.tsx`
- Delete: `frontend/src/components/MaintenanceDueBadge.tsx`
- Delete: `frontend/src/components/MaintenanceDueBadge.test.tsx`

- [ ] **Step 1: Write the failing test**

In `frontend/src/screens/FleetScreen.test.tsx`, the existing test (inside `describe('FleetScreen', ...)`, currently the last one before the block's closing brace) reads:

```typescript
  it('shows the due-maintenance badge on the matching printer card', async () => {
    mockFetch([PRINTER_1], [
      { printer_id: PRINTER_1.id, printer_name: PRINTER_1.name, item_id: 1, item_name: 'Wash plate', due: true, last_done_at: '2026-01-01T00:00:00' },
    ]);
    render(<FleetScreen />);
    await waitFor(() => expect(screen.getByText('Forge')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/1 due/i)).toBeInTheDocument());
  });
```

Replace it with:

```typescript
  it('shows the due-maintenance hat with a tooltip on the matching printer card', async () => {
    mockFetch([PRINTER_1], [
      { printer_id: PRINTER_1.id, printer_name: PRINTER_1.name, item_id: 1, item_name: 'Wash plate', due: true, last_done_at: '2026-01-01T00:00:00' },
    ]);
    render(<FleetScreen />);
    await waitFor(() => expect(screen.getByText('Forge')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTitle('Wash plate')).toBeInTheDocument());
    expect(screen.queryByText(/1 due/i)).toBeNull(); // the old count-pill badge no longer renders
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/screens/FleetScreen.test.tsx`
Expected: FAIL — the hat isn't wired in yet; the old badge text still renders instead.

- [ ] **Step 3: Update `FleetScreen.tsx`**

1. Update the imports at the top of the file. Remove:
```typescript
import { useMaintenanceStatus } from '../api/maintenance';
import { MaintenanceDueBadge } from '../components/MaintenanceDueBadge';
```
Replace with:
```typescript
import { useMaintenanceStatus, type MaintenanceStatusRow } from '../api/maintenance';
import { DueMaintenanceHat } from '../components/DueMaintenanceHat';
```

2. In `FleetScreen()`, replace the current due-count computation:
```typescript
  const { rows: maintenanceRows } = useMaintenanceStatus();
  const dueCountByPrinter = (Array.isArray(maintenanceRows) ? maintenanceRows : []).reduce<Record<string, number>>((acc, r) => {
    if (r.due) acc[String(r.printer_id)] = (acc[String(r.printer_id)] ?? 0) + 1;
    return acc;
  }, {});
```
with:
```typescript
  const { rows: maintenanceRows, refetch: refetchMaintenance } = useMaintenanceStatus();
  const dueRowsByPrinter = (Array.isArray(maintenanceRows) ? maintenanceRows : []).reduce<Record<string, MaintenanceStatusRow[]>>((acc, r) => {
    if (r.due) {
      const key = String(r.printer_id);
      (acc[key] ??= []).push(r);
    }
    return acc;
  }, {});
```

3. Update every call site and prop declaration that currently reads `dueCountByPrinter: Record<string, number>` or passes `dueCountByPrinter={dueCountByPrinter}` — rename to `dueRowsByPrinter: Record<string, MaintenanceStatusRow[]>` / `dueRowsByPrinter={dueRowsByPrinter}`. This includes: the two `<FleetGrid .../>` / `<FleetRows .../>` JSX call sites in `FleetScreen()`'s return, and the prop type + destructuring in `FleetGrid`, `FleetRows`, `PrinterTile`, `PrinterRow`, and `PrinterExpandedCard`'s function signatures, and the prop passed down from `FleetGrid`/`FleetRows` into `PrinterTile`/`PrinterRow`/`PrinterExpandedCard`. Search the file for every occurrence of `dueCountByPrinter` and `Record<string, number>` (the type appears only in this due-tracking context) — there should be roughly 10 occurrences across the 5 function signatures + call sites; rename every one.

4. Also thread a new `refetchMaintenance: () => void` prop from `FleetScreen()` down through `FleetGrid`/`FleetRows` into `PrinterExpandedCard` only (not `PrinterTile`/`PrinterRow` — they don't need it). Add it to the `FleetGrid`/`FleetRows` prop signatures and pass-through, and to `PrinterExpandedCard`'s signature (used in Task 8, unused-but-declared is fine for now — TypeScript won't complain about an unused prop in a destructured signature, only unused local variables/imports).

5. Remove the three `<MaintenanceDueBadge count={dueCountByPrinter[p.id] ?? 0} />` lines (in `PrinterTile`, `PrinterRow`, `PrinterExpandedCard`) entirely — do not replace them in place; the hat goes next to the name instead (next step).

6. Add the hat next to each printer's name, in all 3 render paths:

   **`PrinterTile`** — current name row:
   ```tsx
        <div className="row gap-2" style={{ alignItems: 'baseline' }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>{p.nickname}</span>
          <span className="tiny muted">{p.badge}</span>
        </div>
   ```
   becomes:
   ```tsx
        <div className="row gap-2" style={{ alignItems: 'baseline' }}>
          <DueMaintenanceHat dueItemNames={(dueRowsByPrinter[p.id] ?? []).map(r => r.item_name)} />
          <span style={{ fontSize: 14, fontWeight: 600 }}>{p.nickname}</span>
          <span className="tiny muted">{p.badge}</span>
        </div>
   ```

   **`PrinterRow`** — current name block:
   ```tsx
      <div className="col" style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: 14 }}>{p.nickname}</div>
        <div className="tiny muted" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</div>
      </div>
   ```
   becomes:
   ```tsx
      <div className="col" style={{ minWidth: 0 }}>
        <div className="row gap-1" style={{ alignItems: 'center' }}>
          <DueMaintenanceHat dueItemNames={(dueRowsByPrinter[p.id] ?? []).map(r => r.item_name)} />
          <div style={{ fontWeight: 600, fontSize: 14 }}>{p.nickname}</div>
        </div>
        <div className="tiny muted" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</div>
      </div>
   ```

   **`PrinterExpandedCard`** — current name row (inside the header, the row that also holds the rename button):
   ```tsx
            <div className="row gap-2" style={{ alignItems: 'baseline', whiteSpace: 'nowrap' }}>
              {editingName ? (
   ```
   becomes:
   ```tsx
            <div className="row gap-2" style={{ alignItems: 'baseline', whiteSpace: 'nowrap' }}>
              <DueMaintenanceHat dueItemNames={(dueRowsByPrinter[p.id] ?? []).map(r => r.item_name)} />
              {editingName ? (
   ```
   (leave everything else in that block unchanged — this only prepends one line before the existing `{editingName ? (...) : (...)}` conditional).

- [ ] **Step 4: Delete the now-dead `MaintenanceDueBadge` files**

```bash
git rm frontend/src/components/MaintenanceDueBadge.tsx frontend/src/components/MaintenanceDueBadge.test.tsx
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/screens/FleetScreen.test.tsx`
Expected: PASS (all tests, including the updated one).

Run: `cd frontend && npx vitest run` (full suite — confirms `MaintenanceDueBadge`'s deletion didn't orphan a reference anywhere else) and `cd frontend && npm run build`
Expected: PASS, no regressions, clean type-check.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(maintenance): replace the due-count badge with a hat emoji + tooltip on Fleet cards"
```

---

### Task 7: Quick-add maintenance item from a Fleet printer card

**Files:**
- Modify: `frontend/src/screens/FleetScreen.tsx`
- Test: `frontend/src/screens/FleetScreen.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to the `describe('FleetScreen', ...)` block in `frontend/src/screens/FleetScreen.test.tsx` (this codebase's established pattern for expanding a card is `fireEvent.click(await screen.findByText('Forge'))` — clicking anywhere on the printer's tile, which has an `onClick` at the outer div level, toggles it into `PrinterExpandedCard` — see the existing `'clicking Pause calls pausePrinter with printer id'` test a few lines above for the same pattern):

```typescript
  it('adds a maintenance item from the expanded printer card', async () => {
    mockFetch([PRINTER_1]);
    render(<FleetScreen />);
    fireEvent.click(await screen.findByText('Forge')); // expand the card

    fireEvent.click(await screen.findByTitle('Add maintenance item'));
    fireEvent.change(screen.getByPlaceholderText('Maintenance item name'), { target: { value: 'Check belts' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(
      (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.some(
        (c: unknown[]) => c[0] === '/api/v1/maintenance/items' && (c[1] as RequestInit)?.method === 'POST'
      )
    ).toBe(true));
  });
```

Note: `mockFetch`'s fallback branch (anything not `/api/v1/fleet` or `/api/v1/maintenance/status`) resolves with `{ ok: true, json: () => Promise.resolve(data) }`, i.e. it returns the `FleetPrinter[]` fixture array for every other call — including the quick-add modal's `fetchPrinter`/`fetchMachineCatalog`/`createMaintenanceItem` calls. That's fine for this test: those calls just need to resolve successfully (`ok: true`) without throwing, and this test only asserts that the `POST /api/v1/maintenance/items` call happened, not what the (mismatched-shape) response contained. Don't add more specific stubbing than this — it isn't needed and would just be unused setup.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/screens/FleetScreen.test.tsx`
Expected: FAIL — `screen.findByTitle('Add maintenance item')` throws (button doesn't exist yet).

- [ ] **Step 3: Add the quick-add modal and button**

In `frontend/src/screens/FleetScreen.tsx`, add imports:

```typescript
import { MaintenanceItemForm, emptyDraft, type ItemDraft } from '../components/MaintenanceItemForm';
import {
  useFleetVendorModels, resolveVendorModelForProfile, createMaintenanceItem,
  type FleetVendorModel,
} from '../api/maintenance';
```

Add a new component, placed right before `PrinterExpandedCard`'s definition (so it can be used by it):

```tsx
function QuickAddMaintenanceModal({ printer, fleetModels, onClose, onSaved }: {
  printer: Printer;
  fleetModels: FleetVendorModel[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<ItemDraft>(emptyDraft());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([fetchPrinter(Number(printer.id)), fetchMachineCatalog()])
      .then(([apiPrinter, catalog]) => {
        if (!alive) return;
        const resolved = resolveVendorModelForProfile(apiPrinter.current_orca_printer_profile, catalog);
        if (resolved) {
          setDraft(d => ({ ...d, scope: 'model', machine_vendor: resolved.vendor, machine_model: resolved.printer_model }));
        }
      })
      .catch(console.error);
    return () => { alive = false; };
  }, [printer.id]);

  async function handleSave() {
    setError(null);
    try {
      await createMaintenanceItem({
        name: draft.name, scope: draft.scope,
        machine_vendor: draft.scope === 'model' ? draft.machine_vendor : null,
        machine_model: draft.scope === 'model' ? draft.machine_model : null,
        triggers: draft.triggers,
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0,
      background: 'rgba(2,6,16,0.65)', backdropFilter: 'blur(4px)',
      zIndex: 100, display: 'grid', placeItems: 'center', padding: 24,
    }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{
        width: 'min(480px, 100%)', maxHeight: '90vh', overflowY: 'auto',
        padding: 0, borderColor: 'var(--border-3)',
        boxShadow: '0 20px 60px -20px rgba(0,0,0,0.7), 0 0 0 1px var(--accent-glow)',
      }}>
        <div className="row between" style={{
          padding: '16px 20px', borderBottom: '1px solid var(--border-1)',
          alignItems: 'center', background: 'var(--bg-3)',
        }}>
          <div className="col">
            <div className="tag-key">Add maintenance item</div>
            <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em' }}>{printer.nickname}</div>
          </div>
          <button className="btn ghost icon sm" onClick={onClose}>{Icons.x}</button>
        </div>
        <div style={{ padding: 20 }}>
          {error && (
            <div style={{ marginBottom: 12, padding: '10px 14px', background: 'var(--bg-1)', border: '1px solid var(--err)', borderRadius: 8, color: 'var(--err)', fontSize: 13 }}>
              {error}
            </div>
          )}
          <MaintenanceItemForm draft={draft} catalog={fleetModels} onChange={setDraft} onSave={handleSave} onCancel={onClose} />
        </div>
      </div>
    </div>
  );
}
```

In `PrinterExpandedCard`, add state and the trigger button. Add to the existing `useState` block near the top of the function:

```typescript
  const [addingMaintenance, setAddingMaintenance] = useState(false);
```

In the header's action button row, add a new button right before the existing "Edit printer" wrench button (`<button className="btn icon sm" title="Edit printer" onClick={() => setEditingPrinter(true)}>`):

```tsx
            <button className="btn icon sm" title="Add maintenance item" onClick={() => setAddingMaintenance(true)}>
              👷
            </button>
```

At the end of the component's returned JSX, alongside the existing `{editingPrinter && (<EditPrinterModal .../>)}` block, add:

```tsx
      {addingMaintenance && (
        <QuickAddMaintenanceModal
          printer={p}
          fleetModels={fleetModels}
          onClose={() => setAddingMaintenance(false)}
          onSaved={() => { setAddingMaintenance(false); refetchMaintenance(); }}
        />
      )}
```

This requires `PrinterExpandedCard`'s prop signature to also accept `fleetModels: FleetVendorModel[]` (in addition to the `refetchMaintenance` prop already added in Task 6 Step 3.4). Add `fleetModels` to `PrinterExpandedCard`'s destructured props and type signature, then thread it from `FleetScreen()` (call `useFleetVendorModels()` once there, alongside the existing `useMaintenanceStatus()` call) through `FleetGrid`/`FleetRows` into `PrinterExpandedCard` only (same threading pattern as `refetchMaintenance` — `PrinterTile`/`PrinterRow` don't need it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/screens/FleetScreen.test.tsx`
Expected: PASS (all tests, including the new one).

Run: `cd frontend && npx vitest run` (full suite) and `cd frontend && npm run build`
Expected: PASS, no regressions, clean type-check.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/screens/FleetScreen.tsx frontend/src/screens/FleetScreen.test.tsx
git commit -m "feat(maintenance): add quick-add maintenance item from the Fleet expanded card"
```

---

### Task 8: List + acknowledge due maintenance items in the printer's detailed view

**Files:**
- Modify: `frontend/src/screens/FleetScreen.tsx`
- Test: `frontend/src/screens/FleetScreen.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to the `describe('FleetScreen', ...)` block in `frontend/src/screens/FleetScreen.test.tsx`, reusing the same due-row fixture shape as Task 6's hat test:

```typescript
  it('lists due maintenance items in the expanded card and acknowledges one', async () => {
    mockFetch([PRINTER_1], [
      { printer_id: PRINTER_1.id, printer_name: PRINTER_1.name, item_id: 1, item_name: 'Wash plate', due: true, last_done_at: '2026-01-01T00:00:00' },
    ]);
    render(<FleetScreen />);
    fireEvent.click(await screen.findByText('Forge')); // expand the card

    expect(await screen.findByText('Wash plate')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /acknowledge/i }));

    await waitFor(() => expect(
      (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.some(
        (c: unknown[]) => typeof c[0] === 'string' && (c[0] as string) === `/api/v1/maintenance/printers/${PRINTER_1.id}/items/1/complete`
          && (c[1] as RequestInit)?.method === 'POST'
      )
    ).toBe(true));
  });
```

`screen.findByText('Wash plate')` only matches the new Maintenance-due card's visible text (the hat's own `title`/popover text isn't matched by `getByText`, and only one of `PrinterTile`/`PrinterExpandedCard` is ever mounted for a given printer at a time — `FleetGrid` renders them mutually exclusively based on `expanded`), so there's no ambiguity between the hat and this new list.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/screens/FleetScreen.test.tsx`
Expected: FAIL — no "Acknowledge" button or due-items list exists in the expanded card yet.

- [ ] **Step 3: Add the Maintenance section to `PrinterExpandedCard`**

Add the import at the top of `FleetScreen.tsx`:

```typescript
import { completeMaintenanceItem } from '../api/maintenance';
```

In `PrinterExpandedCard`, after the existing "Temperatures"/"Fans" card in the RIGHT column (right before that column's closing `</div>`), add a new card that only renders when there's something due:

```tsx
            {(dueRowsByPrinter[p.id] ?? []).length > 0 && (
              <div className="card" style={{ padding: 14, background: 'var(--bg-1)', borderColor: 'var(--warn)' }}>
                <div className="tag-key" style={{ marginBottom: 10 }}>👷 Maintenance due</div>
                <div className="col gap-2">
                  {(dueRowsByPrinter[p.id] ?? []).map(row => (
                    <div key={row.item_id} className="row between" style={{ alignItems: 'center' }}>
                      <div style={{ fontWeight: 500, fontSize: 13 }}>{row.item_name}</div>
                      <button
                        className="btn sm"
                        onClick={async () => {
                          await completeMaintenanceItem(Number(p.id), row.item_id);
                          refetchMaintenance();
                        }}
                      >
                        Acknowledge
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
```

This card lives in the same `{/* RIGHT */}` column `<div className="col gap-4">` block that already contains the Filament card and the Temperatures/Fans card — add it as the third card in that column, after the existing two.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/screens/FleetScreen.test.tsx`
Expected: PASS (all tests, including the new one).

Run: `cd frontend && npx vitest run` (full suite) and `cd frontend && npm run build`
Expected: PASS, no regressions, clean type-check.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/screens/FleetScreen.tsx frontend/src/screens/FleetScreen.test.tsx
git commit -m "feat(maintenance): list and acknowledge due maintenance items in the printer's expanded card"
```

---

### Task 9: Verify "acknowledge resets all causes" end-to-end

**Files:**
- Test only: `backend/tests/api/test_maintenance_api.py` (backend — this is the one place in this plan that touches the backend, and only to ADD a verification test; no production code changes)

This requirement is already satisfied by existing, already-shipped code (`maintenance_service.mark_done` resets `last_done_at`, `baseline_job_count`, and `baseline_print_seconds` together in a single call — verified during the original feature's code review). This task adds one explicit end-to-end test proving it, since the existing test suite covers "does complete clear the due flag" but not specifically "does completing an item with BOTH a job_count trigger AND a calendar trigger clear both simultaneously, not just whichever one actually fired."

- [ ] **Step 1: Write the failing (well, it should actually already pass — see Step 2) test**

Append to `backend/tests/api/test_maintenance_api.py`:

```python
@pytest.mark.asyncio
async def test_complete_resets_all_triggers_not_just_the_one_that_fired(client):
    """An item with a job_count trigger (fired) AND a calendar trigger (not yet due) —
    completing it must reset BOTH, so the calendar trigger's clock also restarts now,
    not just the job_count trigger that actually crossed its threshold."""
    r = await client.post("/api/v1/printers", json={
        "name": "P3", "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "10.0.0.7"},
    })
    printer_id = r.json()["id"]

    r = await client.post("/api/v1/maintenance/items", json={
        "name": "Multi-trigger item", "scope": "general",
        "triggers": [
            {"trigger_type": "job_count", "amount": 5, "unit": None},
            {"trigger_type": "calendar", "amount": 12, "unit": "months"},
        ],
    })
    item_id = r.json()["id"]

    # Drive the printer's lifetime job count past the job_count threshold directly
    # (simulating completed jobs — this test only needs the counter to move, not a
    # full job lifecycle, so it patches the printer row via the printers API's
    # update path is not available for this field; instead confirm via /status
    # that it's due, which is the observable behavior this test cares about).
    r = await client.get("/api/v1/maintenance/status")
    row = next(row for row in r.json() if row["printer_id"] == printer_id and row["item_id"] == item_id)
    # Fresh printer, 0 jobs done yet — not due on a freshly-created item/printer pair.
    assert row["due"] is False

    # Acknowledge (complete) it once — this must reset BOTH triggers' baselines together.
    r = await client.post(f"/api/v1/maintenance/printers/{printer_id}/items/{item_id}/complete")
    assert r.status_code == 200

    r = await client.get("/api/v1/maintenance/status")
    row = next(row for row in r.json() if row["printer_id"] == printer_id and row["item_id"] == item_id)
    assert row["due"] is False  # still not due — both baselines reset to "now"/current counts together
```

- [ ] **Step 2: Run the test**

Run: `cd backend && pytest tests/api/test_maintenance_api.py::test_complete_resets_all_triggers_not_just_the_one_that_fired -v`
Expected: PASS immediately — this is a verification test for already-correct existing behavior, not a bug fix. If it fails, STOP and report back rather than "fixing" it — a failure here would mean the original feature's `mark_done` behavior regressed or was misunderstood, which needs investigation, not a quick patch.

- [ ] **Step 3: Run the full backend suite**

Run: `cd backend && pytest -q`
Expected: PASS, one more test than before, no regressions.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/api/test_maintenance_api.py
git commit -m "test(maintenance): verify completing an item resets all its triggers together"
```

---

## Self-review notes (already applied while writing this plan)

- **Spec coverage:** all 6 user-reported items map to a task — (1)→Task 1, (2)→Tasks 3-4 (+ Task 7's reuse), (3)→Task 7, (4)→Tasks 5-6, (5)→Task 8, (6)→Task 9 (verification only, already-correct behavior).
- **Ordering dependency called out explicitly:** Task 2 (extraction) and Task 3 (resolver) have a two-way type dependency (`MaintenanceItemForm.tsx` imports `FleetVendorModel` from `maintenance.ts`) — Task 2's Step 4 explicitly flags the recommended order and what to do if executed in the other order, rather than silently assuming one order.
- **No backend changes except one verification test** — confirmed while writing this plan that `/status` and `/complete` already provide everything needed; Task 9 adds coverage, not new behavior.
