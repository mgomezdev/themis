# Bambu Add-Flow + AMS/Profile Mapping — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a make/model→printer-profile step to the add-printer wizard (all types), let the user map each AMS tray to an OrcaSlicer filament profile + optional Spoolman spool (in the printer's Edit surface, after create), and make AMS auto-sync preserve those mappings instead of wiping them.

**Architecture:** `loaded_filaments` is a JSON column — one new optional field `spoolman_spool_id` (no DB migration). Backend change is a merge in `PrinterManager.on_ams_change`. Frontend adds a reusable `MachinePicker`, a wizard Profile step, and per-tray mapping controls in `EditForm`; the Fleet filament picker is de-overloaded to the new field.

**Tech Stack:** FastAPI + async SQLAlchemy + pytest-asyncio (backend); React 18 + Vite + TS + Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-06-03-bambu-add-flow-design.md`

**Branch:** `bambu-printer`. Commit after each task.

## Model tuning (per subagent-driven-development)
Each task is tagged **Model: Haiku** (mechanical — complete code below, paste verbatim) or **Model: Sonnet** (UI integration into a large existing file — code is given but placement needs care). Dispatch the tagged model. Tasks are ordered so Haiku tasks (1,2,3,6) can run first.

## Conventions (this repo — give to every subagent)
- Backend tests: `cd backend && backend\.venv\Scripts\python.exe -m pytest <path> -v` (python.org venv; `pytest` may not be on PATH — use the venv python).
- Frontend type-check/build: `cd frontend && npm run build` (`tsc -b`; `tsc --noEmit` is a NO-OP here — never use it). Tests: `cd frontend && npx vitest run <path>`. Strict TS: unused imports/vars fail the build.
- `loaded_filaments` is opaque JSON end-to-end (printers routes + serializer pass it through) — no backend serializer change is needed for the new field.

## File structure
- **Modify** `backend/app/services/printer_manager.py` — `on_ams_change` merge (Task 1).
- **Create** `backend/tests/services/test_ams_merge.py` (Task 1).
- **Modify** `frontend/src/api/printers.ts` — `LoadedFilament.spoolman_spool_id` (Task 2).
- **Create** `frontend/src/components/MachinePicker.tsx` + `MachinePicker.test.tsx` (Task 3).
- **Modify** `frontend/src/screens/PrintersScreen.tsx` — wizard Profile step (Task 4) + EditForm per-tray mapping (Task 5).
- **Modify** `frontend/src/screens/FleetScreen.tsx` — FilamentPicker de-overload (Task 6).

---

## Task 1: Backend — `on_ams_change` merges (preserve mappings by slot)

**Model: Haiku**

**Files:**
- Modify: `backend/app/services/printer_manager.py` (the `on_ams_change` method, ~lines 178-193)
- Create: `backend/tests/services/test_ams_merge.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/services/test_ams_merge.py
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.database import Base
from app.models import Printer
from app.services.printer_manager import PrinterManager


async def _factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_on_ams_change_preserves_mappings_by_slot():
    Session = await _factory()
    async with Session() as s:
        s.add(Printer(
            name="P", printer_type="bambu", connection_config={},
            loaded_filaments=[
                {"slot": 0, "filament_id": "OLD", "name": "old", "type": "PLA",
                 "color": "#111", "filament_profile": "Generic PLA @BBL",
                 "spoolman_spool_id": "42"},
            ],
        ))
        await s.commit()

    mgr = PrinterManager()
    mgr.set_session_factory(Session)
    await mgr.on_ams_change(1, [
        {"slot": 0, "filament_id": "NEW", "name": "new", "type": "PLA", "color": "#222"},
        {"slot": 1, "filament_id": "GFL96", "name": "mint", "type": "PLA", "color": "#0f0"},
    ])

    async with Session() as s:
        p = await s.get(Printer, 1)
        lf = {f["slot"]: f for f in p.loaded_filaments}
        # slot 0: AMS fields updated, user mappings preserved
        assert lf[0]["filament_id"] == "NEW"
        assert lf[0]["color"] == "#222"
        assert lf[0]["filament_profile"] == "Generic PLA @BBL"
        assert lf[0]["spoolman_spool_id"] == "42"
        # slot 1: brand-new tray, no mappings
        assert lf[1].get("filament_profile") is None
        assert lf[1].get("spoolman_spool_id") is None


@pytest.mark.asyncio
async def test_on_ams_change_drops_orphaned_slots():
    Session = await _factory()
    async with Session() as s:
        s.add(Printer(
            name="P", printer_type="bambu", connection_config={},
            loaded_filaments=[
                {"slot": 0, "filament_id": "A", "name": "a", "type": "PLA", "color": "#111"},
                {"slot": 1, "filament_id": "B", "name": "b", "type": "PLA", "color": "#222"},
            ],
        ))
        await s.commit()

    mgr = PrinterManager()
    mgr.set_session_factory(Session)
    await mgr.on_ams_change(1, [
        {"slot": 0, "filament_id": "A", "name": "a", "type": "PLA", "color": "#111"},
    ])

    async with Session() as s:
        p = await s.get(Printer, 1)
        assert [f["slot"] for f in p.loaded_filaments] == [0]
```

- [ ] **Step 2: Run the tests — confirm they FAIL**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_ams_merge.py -v`
Expected: FAIL — `test_on_ams_change_preserves_mappings_by_slot` fails because the current code overwrites (slot 0 loses `filament_profile`/`spoolman_spool_id`).

- [ ] **Step 3: Implement the merge**

Replace the body of `on_ams_change` in `backend/app/services/printer_manager.py`. Find this exact block:

```python
    async def on_ams_change(self, printer_id: int, trays: list) -> None:
        """AMS filament change → persist the printer's `loaded_filaments` from the
        auto-detected trays, so the queue engine's filament gating and the Fleet UI
        reflect what's actually loaded (no manual entry for AMS printers)."""
        if self._session_factory:
            async with self._session_factory() as session:
                from ..models import Printer
                printer = await session.get(Printer, printer_id)
                if printer is not None:
                    printer.loaded_filaments = trays
                    await session.commit()
```

and replace it with:

```python
    async def on_ams_change(self, printer_id: int, trays: list) -> None:
        """AMS filament change → persist the printer's `loaded_filaments` from the
        auto-detected trays. User-set per-slot mappings (`filament_profile`,
        `spoolman_spool_id`) are preserved by slot across AMS reports; slots no
        longer reported drop with their mappings."""
        if self._session_factory:
            async with self._session_factory() as session:
                from ..models import Printer
                printer = await session.get(Printer, printer_id)
                if printer is not None:
                    prev_by_slot = {
                        f.get("slot"): f for f in (printer.loaded_filaments or [])
                    }
                    merged = []
                    for tray in trays:
                        prev = prev_by_slot.get(tray.get("slot"))
                        if prev is not None:
                            tray = {
                                **tray,
                                "filament_profile": prev.get("filament_profile"),
                                "spoolman_spool_id": prev.get("spoolman_spool_id"),
                            }
                        merged.append(tray)
                    printer.loaded_filaments = merged
                    await session.commit()
```

(Leave the `if self._on_state_broadcast:` block below it unchanged.)

- [ ] **Step 4: Run the tests — confirm they PASS**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/services/test_ams_merge.py -v`
Expected: PASS (2 passed). Then the full suite: `... -m pytest -q` → all green (was 315).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/printer_manager.py backend/tests/services/test_ams_merge.py
git commit -m "fix(printers): AMS sync merges loaded_filaments (preserve mappings by slot)"
```

---

## Task 2: Frontend — add `spoolman_spool_id` to `LoadedFilament`

**Model: Haiku**

**Files:**
- Modify: `frontend/src/api/printers.ts` (the `LoadedFilament` interface, ~lines 29-36)

- [ ] **Step 1: Edit the type**

In `frontend/src/api/printers.ts`, find:

```ts
export interface LoadedFilament {
  slot: number;
  filament_id: string | null;
  name: string;
  type: string;
  color: string;
  filament_profile?: string | null;  // OrcaSlicer filament preset used to slice with this filament
}
```

and replace with:

```ts
export interface LoadedFilament {
  slot: number;
  filament_id: string | null;          // Bambu AMS code (e.g. "GFL99") or null — NOT a Spoolman id
  name: string;
  type: string;
  color: string;
  filament_profile?: string | null;    // OrcaSlicer filament preset used to slice with this filament
  spoolman_spool_id?: string | null;   // optional mapped Spoolman spool id
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npm run build`
Expected: builds clean (no consumers break — the field is optional).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/printers.ts
git commit -m "feat(printers): add optional spoolman_spool_id to LoadedFilament"
```

---

## Task 3: Frontend — reusable `MachinePicker` component

**Model: Haiku**

**Files:**
- Create: `frontend/src/components/MachinePicker.tsx`
- Create: `frontend/src/components/MachinePicker.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/MachinePicker.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MachinePicker } from './MachinePicker';
import type { MachinePreset } from '../api/printers';

const CATALOG: MachinePreset[] = [
  { name: 'Bambu Lab P1S 0.4 nozzle', vendor: 'Bambu Lab', printer_model: 'P1S', nozzle: '0.4', source: 'system' },
  { name: 'Bambu Lab P1S 0.6 nozzle', vendor: 'Bambu Lab', printer_model: 'P1S', nozzle: '0.6', source: 'system' },
];

describe('MachinePicker', () => {
  it('resolves make→model→nozzle to a preset name via onChange', () => {
    const onChange = vi.fn();
    render(<MachinePicker catalog={CATALOG} value="" onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Make'), { target: { value: 'Bambu Lab' } });
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'P1S' } });
    fireEvent.change(screen.getByLabelText('Nozzle'), { target: { value: '0.4' } });
    expect(onChange).toHaveBeenLastCalledWith('Bambu Lab P1S 0.4 nozzle');
  });
});
```

- [ ] **Step 2: Run — confirm FAIL** (`cannot resolve './MachinePicker'`).

Run: `cd frontend && npx vitest run src/components/MachinePicker.test.tsx`

- [ ] **Step 3: Implement the component**

```tsx
// frontend/src/components/MachinePicker.tsx
import { useState, useEffect } from 'react';
import type { MachinePreset } from '../api/printers';

const uniq = (xs: string[]) => [...new Set(xs)].sort();

/**
 * Cascading make → model → nozzle picker over the OrcaSlicer machine catalog.
 * Resolves a selection to a machine-preset name and reports it via onChange.
 */
export function MachinePicker({
  catalog, value, onChange,
}: {
  catalog: MachinePreset[];
  value: string;
  onChange: (presetName: string) => void;
}) {
  const [vendor, setVendor] = useState('');
  const [model, setModel] = useState('');
  const [nozzle, setNozzle] = useState('');

  // Initialise the three selects from the current preset once the catalog is present.
  useEffect(() => {
    if (!catalog.length || vendor) return;
    const e = catalog.find(c => c.name === value);
    if (e) { setVendor(e.vendor); setModel(e.printer_model); setNozzle(e.nozzle); }
  }, [catalog, value, vendor]);

  const vendors = uniq(catalog.map(c => c.vendor));
  const models = uniq(catalog.filter(c => c.vendor === vendor).map(c => c.printer_model));
  const nozzles = uniq(catalog.filter(c => c.vendor === vendor && c.printer_model === model).map(c => c.nozzle));

  const pickVendor = (v: string) => { setVendor(v); setModel(''); setNozzle(''); onChange(''); };
  const pickModel = (m: string) => { setModel(m); setNozzle(''); onChange(''); };
  const pickNozzle = (nz: string) => {
    setNozzle(nz);
    const matches = catalog.filter(c => c.vendor === vendor && c.printer_model === model && c.nozzle === nz);
    const chosen = matches.find(c => c.source === 'system') ?? matches[0];
    onChange(chosen ? chosen.name : '');
  };

  if (catalog.length === 0) {
    return (
      <div className="tiny muted">
        No OrcaSlicer machine profiles found — add profiles in OrcaSlicer, then use Settings → “Rescan profiles”.
      </div>
    );
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 110px', gap: 8 }}>
      <div className="col gap-1">
        <label className="label" htmlFor="mp-make">Make</label>
        <select id="mp-make" aria-label="Make" className="select" value={vendor}
                onChange={e => pickVendor(e.target.value)}>
          <option value="">— make —</option>
          {vendors.map(v => <option key={v} value={v}>{v}</option>)}
        </select>
      </div>
      <div className="col gap-1">
        <label className="label" htmlFor="mp-model">Model</label>
        <select id="mp-model" aria-label="Model" className="select" value={model} disabled={!vendor}
                onChange={e => pickModel(e.target.value)}>
          <option value="">— model —</option>
          {models.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>
      <div className="col gap-1">
        <label className="label" htmlFor="mp-nozzle">Nozzle</label>
        <select id="mp-nozzle" aria-label="Nozzle" className="select" value={nozzle} disabled={!model}
                onChange={e => pickNozzle(e.target.value)}>
          <option value="">—</option>
          {nozzles.map(n => <option key={n} value={n}>{n} mm</option>)}
        </select>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run — confirm PASS + build**

Run: `cd frontend && npx vitest run src/components/MachinePicker.test.tsx` → PASS.
Run: `cd frontend && npm run build` → clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MachinePicker.tsx frontend/src/components/MachinePicker.test.tsx
git commit -m "feat(fe): reusable MachinePicker (make/model/nozzle cascade)"
```

---

## Task 4: Frontend — wizard gains a "Printer profile" step

**Model: Sonnet** (integrates into the existing `PrinterAddForm`; code below is complete — paste carefully).

**Files:**
- Modify: `frontend/src/screens/PrintersScreen.tsx` (`PrinterAddForm`, ~lines 184-415)
- Create: `frontend/src/screens/PrintersScreen.wizard.test.tsx`

Context: `PrinterAddForm` is a 3-step wizard (Type · Connect · Review). Add a **Profile** step between Connect and Review using `MachinePicker`, carry `machinePreset` in state, and send it on create.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/screens/PrintersScreen.wizard.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PrinterAddForm } from './PrintersScreen';
import type { PrinterType } from '../api/printers';

const TYPES: PrinterType[] = [
  { printer_type: 'bambu', display_name: 'Bambu', connection_fields: [
    { name: 'ip_address', label: 'IP', field_type: 'text', required: true, default: null, placeholder: '', help_text: '' },
  ] },
];
const CATALOG = [{ name: 'Bambu Lab P1S 0.4 nozzle', vendor: 'Bambu Lab', printer_model: 'P1S', nozzle: '0.4', source: 'system' }];

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/orca-machine-catalog')) return new Response(JSON.stringify(CATALOG), { status: 200 });
    if (url === '/api/v1/printers' && init?.method === 'POST')
      return new Response(JSON.stringify({ id: 1 }), { status: 201 });
    return new Response('[]', { status: 200 });
  }));
});

describe('PrinterAddForm profile step', () => {
  it('sends current_orca_printer_profile on finish', async () => {
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
    render(<PrinterAddForm types={TYPES} onCancel={() => {}} onCreated={() => {}} />);
    // Step 1 → 2
    fireEvent.click(screen.getByRole('button', { name: /Next/i }));
    // Step 2 (Connect) → 3 (Profile)
    fireEvent.click(screen.getByRole('button', { name: /Next/i }));
    // pick make/model/nozzle
    await waitFor(() => screen.getByLabelText('Make'));
    fireEvent.change(screen.getByLabelText('Make'), { target: { value: 'Bambu Lab' } });
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'P1S' } });
    fireEvent.change(screen.getByLabelText('Nozzle'), { target: { value: '0.4' } });
    // Profile → Review
    fireEvent.click(screen.getByRole('button', { name: /Next/i }));
    // Finish
    fireEvent.click(screen.getByRole('button', { name: /Finish/i }));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(c => c[0] === '/api/v1/printers' && (c[1] as RequestInit)?.method === 'POST');
      expect(post).toBeTruthy();
      const body = JSON.parse((post![1] as RequestInit).body as string);
      expect(body.current_orca_printer_profile).toBe('Bambu Lab P1S 0.4 nozzle');
    });
  });
});
```

- [ ] **Step 2: Run — confirm FAIL** (`cd frontend && npx vitest run src/screens/PrintersScreen.wizard.test.tsx`). It fails: there is no Profile step / `Make` control, and create body has no `current_orca_printer_profile`.

- [ ] **Step 3: Implement**

In `frontend/src/screens/PrintersScreen.tsx`:

(a) Add imports — extend the existing `../api/printers` import to include `fetchMachineCatalog` and `type MachinePreset`, and add the MachinePicker import:

```ts
import {
  fetchPrinters,
  fetchPrinterTypes,
  createPrinter,
  updatePrinter,
  deletePrinter,
  testConnection,
  fetchMachineCatalog,
  type ApiPrinter,
  type PrinterType,
  type ConnectionField,
  type LoadedFilament,
  type MachinePreset,
} from '../api/printers';
import { MachinePicker } from '../components/MachinePicker';
```

(b) In `PrinterAddForm`, add state + catalog load. After the existing `const [finishError, setFinishError] = useState<string | null>(null);` line add:

```ts
  const [machinePreset, setMachinePreset] = useState<string>('');
  const [catalog, setCatalog] = useState<MachinePreset[]>([]);
  useEffect(() => { fetchMachineCatalog().then(setCatalog).catch(() => {}); }, []);
```

Ensure `useEffect` is imported at the top of the file (the file currently imports `useState, useEffect, Fragment` — keep `useEffect`).

(c) Update `handleFinish` — change the `createPrinter({...})` call to include the preset:

```ts
      await createPrinter({
        name: data.nickname || data.printerType.display_name,
        printer_type: data.printerType.printer_type,
        connection_config: data.connectionConfig,
        current_orca_printer_profile: machinePreset || null,
        orca_printer_profiles: machinePreset ? [machinePreset] : [],
      });
```

(d) Change the steps array from 3 to 4 entries:

```ts
  const steps = [
    { n: 1, label: 'Type' },
    { n: 2, label: 'Connect' },
    { n: 3, label: 'Profile' },
    { n: 4, label: 'Review' },
  ] as const;
```

(e) In the step-indicator `.map`, the connector currently renders `{s.n < 3 && (...)}`. Change `3` to `4` so the connector shows after steps 1-3:

```tsx
              {s.n < 4 && (
                <div style={{ width: 40, height: 1, background: 'var(--border-1)', marginLeft: 8 }} />
              )}
```

(f) In Step 2's footer, the "Next" button does `onClick={() => setStep(3)}`. Leave it (Connect → Profile is step 3 now).

(g) Insert a new **Step 3 (Profile)** block immediately BEFORE the existing `{/* Step 3: Review + finish */}` block, and change that Review block's guard from `step === 3` to `step === 4`:

New Profile block:

```tsx
        {/* Step 3: Printer profile (make/model) */}
        {step === 3 && data.printerType && (
          <div className="card" style={{ padding: 24 }}>
            <SectionHeader title="Printer profile"
              sub="Pick the make/model so jobs slice with the right OrcaSlicer machine + filament presets. You can set this later." />
            <MachinePicker catalog={catalog} value={machinePreset} onChange={setMachinePreset} />
            <div className="tiny muted" style={{ marginTop: 8 }}>
              {machinePreset
                ? <>Preset: <span className="mono">{machinePreset}</span></>
                : 'Optional now — required before this printer can slice.'}
            </div>
            <div className="row gap-2" style={{ marginTop: 24, justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setStep(2)}>{Icons.chevL} Back</button>
              <button className="btn primary" onClick={() => setStep(4)}>Next {Icons.chevR}</button>
            </div>
          </div>
        )}
```

Then change the Review block opening from:
```tsx
        {step === 3 && data.printerType && (
          <div className="card" style={{ padding: 24 }}>
            <SectionHeader title="Review" sub="Confirm the details before adding." />
```
to:
```tsx
        {step === 4 && data.printerType && (
          <div className="card" style={{ padding: 24 }}>
            <SectionHeader title="Review" sub="Confirm the details before adding." />
```

And inside the Review block, the "Back" button currently does `onClick={() => setStep(2)}`. Change it to `onClick={() => setStep(3)}`. Also add a Profile row to the review list — after the Nickname row (`<span ...>Nickname</span>...`) add:

```tsx
              <div className="row gap-3">
                <span className="muted small" style={{ width: 120 }}>Printer profile</span>
                <span className="small mono">{machinePreset || '— not set —'}</span>
              </div>
```

- [ ] **Step 4: Run — confirm PASS + build + full FE suite**

Run: `cd frontend && npx vitest run src/screens/PrintersScreen.wizard.test.tsx` → PASS.
Run: `cd frontend && npx vitest run && npm run build` → all green, clean build.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/screens/PrintersScreen.tsx frontend/src/screens/PrintersScreen.wizard.test.tsx
git commit -m "feat(fe): add printer-profile (make/model) step to add-printer wizard"
```

---

## Task 5: Frontend — `EditForm` per-tray filament-profile + Spoolman mapping + make/model

**Model: Sonnet** (largest UI task; the full replacement `EditForm` is below).

**Files:**
- Modify: `frontend/src/screens/PrintersScreen.tsx` (`EditForm`, ~lines 56-178)
- Create: `frontend/src/screens/PrintersScreen.editform.test.tsx`

Context: `EditForm` currently edits name + connection fields + a manual loaded-filaments list (color/type/name + add/remove slot). Add: a `MachinePicker` (saved as `current_orca_printer_profile`), and per slot a **filament-profile `<select>`** (from `GET /printers/{id}/profiles`) + an optional **Spoolman spool `<select>`** (writes `spoolman_spool_id`).

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/screens/PrintersScreen.editform.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { EditForm } from './PrintersScreen';
import type { ApiPrinter, PrinterType } from '../api/printers';

const TYPES: PrinterType[] = [
  { printer_type: 'bambu', display_name: 'Bambu', connection_fields: [] },
];
const PRINTER: ApiPrinter = {
  id: 7, name: 'Iris', printer_type: 'bambu', connection_config: {},
  awaiting_plate_clear: false, orca_printer_profiles: [], current_orca_printer_profile: 'Bambu Lab P1S 0.4 nozzle',
  enabled: true, queue_on: true, connected: true,
  loaded_filaments: [{ slot: 0, filament_id: 'GFL99', name: 'PLA', type: 'PLA', color: '#fff' }],
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/orca-machine-catalog')) return new Response('[]', { status: 200 });
    if (url.includes('/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
    if (url.match(/\/printers\/7\/profiles$/)) return new Response(JSON.stringify({ print_profiles: [], filament_profiles: ['Generic PLA @BBL', 'PolyTerra PLA @BBL'] }), { status: 200 });
    if (url.match(/\/printers\/7$/) && init?.method === 'PATCH') return new Response(JSON.stringify(PRINTER), { status: 200 });
    return new Response('[]', { status: 200 });
  }));
});

describe('EditForm per-tray mapping', () => {
  it('saves a chosen filament_profile on the slot', async () => {
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
    render(<EditForm printer={PRINTER} types={TYPES} onSave={() => {}} onCancel={() => {}} />);
    // filament-profile select for slot 0 populated from /profiles
    const sel = await screen.findByLabelText('Filament profile for slot 1');
    fireEvent.change(sel, { target: { value: 'Generic PLA @BBL' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(c => /\/printers\/7$/.test(c[0] as string) && (c[1] as RequestInit)?.method === 'PATCH');
      expect(patch).toBeTruthy();
      const body = JSON.parse((patch![1] as RequestInit).body as string);
      expect(body.loaded_filaments[0].filament_profile).toBe('Generic PLA @BBL');
    });
  });
});
```

- [ ] **Step 2: Run — confirm FAIL** (`cd frontend && npx vitest run src/screens/PrintersScreen.editform.test.tsx`): no “Filament profile for slot 1” control exists.

- [ ] **Step 3: Implement — replace the whole `EditForm` function**

First ensure these imports exist at the top of `PrintersScreen.tsx` (add what's missing):
- from `../api/printers`: `fetchMachineCatalog`, `getPrinterProfiles` is in `../api/queue` (import separately), `type MachinePreset`.
- `import { MachinePicker } from '../components/MachinePicker';` (added in Task 4 — keep it).
- `import { getPrinterProfiles } from '../api/queue';`
- `import { useSpoolmanConfig, useSpools, spoolDisplayName, type ApiSpool } from '../api/spoolman';`

Replace the entire `EditForm` function (from `function EditForm({` through its closing `}` before the `PrinterAddForm` comment) with:

```tsx
function EditForm({
  printer,
  types,
  onSave,
  onCancel,
}: {
  printer: ApiPrinter;
  types: PrinterType[];
  onSave: () => void;
  onCancel: () => void;
}) {
  const ptype = types.find(t => t.printer_type === printer.printer_type);
  const [name, setName] = useState(printer.name);
  const [config, setConfig] = useState<Record<string, string>>(
    Object.fromEntries(
      Object.entries(printer.connection_config).map(([k, v]) => [k, String(v)])
    )
  );
  const [slots, setSlots] = useState<LoadedFilament[]>(printer.loaded_filaments ?? []);
  const [preset, setPreset] = useState<string>(printer.current_orca_printer_profile ?? '');
  const [catalog, setCatalog] = useState<MachinePreset[]>([]);
  const [filamentProfiles, setFilamentProfiles] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { config: spoolmanCfg } = useSpoolmanConfig();
  const spoolmanActive = !!(spoolmanCfg?.enabled && spoolmanCfg?.url);
  const spools = useSpools(spoolmanActive);

  useEffect(() => { fetchMachineCatalog().then(setCatalog).catch(() => {}); }, []);
  useEffect(() => {
    let alive = true;
    getPrinterProfiles(printer.id)
      .then(p => { if (alive) setFilamentProfiles(p.filament_profiles); })
      .catch(() => { if (alive) setFilamentProfiles([]); });
    return () => { alive = false; };
  }, [printer.id, preset]);

  function addSlot() {
    setSlots(s => [...s, { slot: s.length, filament_id: null, name: '', type: 'PLA', color: '#888888' }]);
  }
  function removeSlot(i: number) {
    setSlots(s => s.filter((_, idx) => idx !== i).map((x, idx) => ({ ...x, slot: idx })));
  }
  function updateSlot(i: number, patch: Partial<LoadedFilament>) {
    setSlots(s => s.map((x, idx) => idx === i ? { ...x, ...patch } : x));
  }
  function pickSpool(i: number, spoolId: string) {
    if (!spoolId) { updateSlot(i, { spoolman_spool_id: null }); return; }
    const sp = spools.find(x => String(x.id) === spoolId);
    if (!sp) { updateSlot(i, { spoolman_spool_id: spoolId }); return; }
    updateSlot(i, {
      spoolman_spool_id: String(sp.id),
      name: spoolDisplayName(sp),
      type: sp.filament.material,
      color: sp.filament.color_hex ? `#${sp.filament.color_hex}` : (slots[i]?.color ?? '#888888'),
    });
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await updatePrinter(printer.id, {
        name,
        connection_config: config,
        current_orca_printer_profile: preset || null,
        orca_printer_profiles: preset ? [preset] : [],
        loaded_filaments: slots,
      });
      onSave();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card" style={{ padding: 16, marginTop: 8 }}>
      <div className="col gap-3">
        <div>
          <label className="label">Nickname</label>
          <input className="input" value={name} onChange={e => setName(e.target.value)} />
        </div>

        {ptype?.connection_fields.map((f: ConnectionField) => (
          <div key={f.name}>
            <label className="label">{f.label}</label>
            <input
              className="input"
              type={f.field_type === 'password' ? 'password' : 'text'}
              placeholder={f.placeholder}
              value={config[f.name] ?? ''}
              onChange={e => setConfig({ ...config, [f.name]: e.target.value })}
            />
          </div>
        ))}

        <div>
          <div className="label" style={{ marginBottom: 8 }}>Printer profile (make / model)</div>
          <MachinePicker catalog={catalog} value={preset} onChange={setPreset} />
        </div>

        <div>
          <div className="label" style={{ marginBottom: 8 }}>Loaded filaments</div>
          <div className="col gap-3">
            {slots.map((s, i) => (
              <div key={i} className="card" style={{ padding: 10, background: 'var(--bg-1)' }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
                  <span className="tiny muted" style={{ width: 44, flexShrink: 0 }}>Slot {i + 1}</span>
                  <input
                    type="color"
                    value={s.color}
                    onChange={e => updateSlot(i, { color: e.target.value })}
                    style={{ width: 32, height: 32, padding: 2, border: '1px solid var(--border-1)', borderRadius: 6, cursor: 'pointer', background: 'var(--bg-2)', flexShrink: 0 }}
                  />
                  <select
                    className="input"
                    style={{ width: 90, flexShrink: 0 }}
                    value={s.type}
                    onChange={e => updateSlot(i, { type: e.target.value })}>
                    {MAT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <input
                    className="input"
                    style={{ flex: '1 1 140px' }}
                    placeholder="Filament name"
                    value={s.name}
                    onChange={e => updateSlot(i, { name: e.target.value })}
                  />
                  <button className="btn ghost icon sm" onClick={() => removeSlot(i)} title="Remove slot">
                    {Icons.x}
                  </button>
                </div>

                <div className="row gap-2" style={{ marginTop: 8, flexWrap: 'wrap' }}>
                  <div className="col gap-1" style={{ flex: '1 1 200px' }}>
                    <label className="tiny muted" htmlFor={`fp-${i}`}>Filament profile</label>
                    <select
                      id={`fp-${i}`}
                      aria-label={`Filament profile for slot ${i + 1}`}
                      className="input"
                      value={s.filament_profile ?? ''}
                      onChange={e => updateSlot(i, { filament_profile: e.target.value || null })}>
                      <option value="">— none (slicer default) —</option>
                      {filamentProfiles.map(fp => <option key={fp} value={fp}>{fp}</option>)}
                    </select>
                  </div>
                  {spoolmanActive && (
                    <div className="col gap-1" style={{ flex: '1 1 200px' }}>
                      <label className="tiny muted" htmlFor={`sp-${i}`}>Spoolman spool (optional)</label>
                      <select
                        id={`sp-${i}`}
                        aria-label={`Spoolman spool for slot ${i + 1}`}
                        className="input"
                        value={s.spoolman_spool_id ?? ''}
                        onChange={e => pickSpool(i, e.target.value)}>
                        <option value="">— not mapped —</option>
                        {spools.map(sp => (
                          <option key={sp.id} value={String(sp.id)}>{spoolDisplayName(sp)}</option>
                        ))}
                      </select>
                    </div>
                  )}
                </div>
                {!s.filament_profile && (
                  <div className="tiny" style={{ color: 'var(--warn)', marginTop: 6 }}>
                    No filament profile — jobs slice with OrcaSlicer’s default.
                  </div>
                )}
              </div>
            ))}
            <button className="btn ghost sm" onClick={addSlot} style={{ alignSelf: 'flex-start' }}>
              {Icons.plus} Add slot
            </button>
          </div>
        </div>

        {error && <div style={{ color: 'var(--err)', fontSize: 13 }}>{error}</div>}
        <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
          <button className="btn ghost sm" onClick={onCancel}>Cancel</button>
          <button className="btn primary sm" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
```

Note: `EditForm` is referenced inside `PrintersScreen` (default-not-exported is fine for the screen, but the test imports `{ EditForm }`). Add `export` to the function: `export function EditForm({` so the test can import it. Do the same for `PrinterAddForm` if not already exported (it is exported).

- [ ] **Step 4: Run — confirm PASS + full suite + build**

Run: `cd frontend && npx vitest run src/screens/PrintersScreen.editform.test.tsx` → PASS.
Run: `cd frontend && npx vitest run && npm run build` → all green, clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/screens/PrintersScreen.tsx frontend/src/screens/PrintersScreen.editform.test.tsx
git commit -m "feat(fe): per-tray filament profile + Spoolman mapping + make/model in printer edit"
```

---

## Task 6: Frontend — de-overload Fleet `FilamentPicker` to `spoolman_spool_id`

**Model: Haiku**

**Files:**
- Modify: `frontend/src/screens/FleetScreen.tsx` (`FilamentPicker.selectSpool`, ~lines 360-377)

- [ ] **Step 1: Edit `selectSpool`**

In `frontend/src/screens/FleetScreen.tsx`, find:

```tsx
      await updatePrinter(printerId, {
        loaded_filaments: [{
          slot: 0,
          filament_id: String(spool.id),
          name: spoolDisplayName(spool),
          type: spool.filament.material,
          color: spool.filament.color_hex ? `#${spool.filament.color_hex}` : '#888888',
          filament_profile: profile || null,
        }],
      });
```

and replace `filament_id: String(spool.id),` with the Spoolman field (leave `filament_id` null so it isn't conflated):

```tsx
      await updatePrinter(printerId, {
        loaded_filaments: [{
          slot: 0,
          filament_id: null,
          spoolman_spool_id: String(spool.id),
          name: spoolDisplayName(spool),
          type: spool.filament.material,
          color: spool.filament.color_hex ? `#${spool.filament.color_hex}` : '#888888',
          filament_profile: profile || null,
        }],
      });
```

- [ ] **Step 2: Type-check + full FE suite**

Run: `cd frontend && npm run build` → clean (the `LoadedFilament` type from Task 2 already allows `spoolman_spool_id`).
Run: `cd frontend && npx vitest run` → all green.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/screens/FleetScreen.tsx
git commit -m "refactor(fe): Fleet filament picker writes spoolman_spool_id (de-overload filament_id)"
```

---

## Task 7 (OPTIONAL): refactor Fleet `EditPrinterModal` to use `MachinePicker`

**Model: Sonnet** — **optional, low priority.** The inline cascade in `FleetScreen.EditPrinterModal` (lines ~112-136 logic, ~239-267 JSX) duplicates `MachinePicker`. Replacing it with `<MachinePicker catalog={catalog} value={machinePreset} onChange={setMachinePreset} />` (and deleting the now-unused `selVendor/selModel/selNozzle` state + `pickVendor/pickModel/pickNozzle` + the init effect) removes the duplication with no behavior change. **Skip unless time permits** — it carries refactor risk and the inline version already works. If done: run `npx vitest run && npm run build`, commit `refactor(fe): Fleet edit modal uses shared MachinePicker`.

---

## Task 8: Update agent docs

**Model: Sonnet** (skill-driven).

Run the `themis-docs-sync` skill against this branch's diff (the `docs/agent/*` set is present on `bambu-printer`). Update: `printers.md` (AMS merge behavior; `loaded_filaments` now has `spoolman_spool_id`), `frontend.md` (MachinePicker component; wizard Profile step; EditForm per-tray mapping), `data-model.md` (loaded_filaments slot shape gains `spoolman_spool_id`; `filament_id` is Bambu-code-only). Commit `docs(agent): sync for bambu add-flow + AMS mapping`.

---

## Final review
After all tasks: dispatch a final reviewer over `git diff main...bambu-printer` for the new feature (focus: AMS merge correctness, no `filament_id`/`spoolman_spool_id` confusion left, wizard+edit save the right payloads). Then:
```
cd backend && backend\.venv\Scripts\python.exe -m pytest -q
cd frontend && npx vitest run && npm run build
```
Then optionally restart the app on `bambu-printer` so the user can add their real Bambu through the new flow.

## Self-review notes (author)
- **Spec coverage:** make/model→profile in wizard (T4) + edit (T5); per-tray filament_profile (T5) + Spoolman mapping (T5, field T2); AMS merge preserves mappings (T1); de-overload (T2 field, T5 + T6 usage). All spec sections mapped.
- **Type/name consistency:** `spoolman_spool_id` used identically in T1 (backend dict key), T2 (TS field), T5 (`pickSpool`/save), T6. `current_orca_printer_profile` + `orca_printer_profiles` payload identical in T4 + T5. `getPrinterProfiles` (from `../api/queue`) returns `{print_profiles, filament_profiles}` — used in T5.
- **Haiku-safety:** T1/T2/T3/T6 are mechanical with complete code. T4/T5 are tagged Sonnet (integration into a large file); their code is complete but placement needs care. T7 optional.
- **Soft spots flagged:** T5 requires `export function EditForm` (added) so the test can import it; confirm `getPrinterProfiles` import path is `../api/queue`.
