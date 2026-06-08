# Defer Filament + Shared Per-Printer Job Config — Implementation Plan (Sub-project A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let a job config "defer" filament (use whatever's loaded, the new default), bring the single-tool picker to Edit Job, and consolidate the duplicated New/Edit per-printer config into one shared component.

**Architecture:** A shared `PerPrinterConfig` component (one filament/tool control: multi-tool printers get a Tool dropdown whose first option "Any / default tool" = defer; single-tool printers get a "Use loaded filament" (default) vs "Require specific filament" toggle) replaces both screens' copies. "Defer" = `tool_index=null` + empty ask, which the queue already resolves to the first loaded slot — so no schema/queue change; only `get_job_details`/`update_job_configs` gain `tool_index` round-trip.

**Tech Stack:** React/Vite/TS, vitest; FastAPI/pytest. **Spec:** `docs/superpowers/specs/2026-06-08-defer-and-shared-job-config-design.md`. **Branch:** `tool-slot-mapping` (worktree `C:\Users\mgome\Documents\projects\themis-tool-mapping`).

## Conventions (every subagent)
- Work ONLY in the worktree; absolute paths. Backend venv: `backend\.venv`; frontend deps already installed.
- Frontend: `cd frontend && npx vitest run <file>`; `npm run build` (tsc -b — the real typecheck). `noUnusedLocals`/`noUnusedParameters` ON.
- Backend: `cd backend && backend\.venv\Scripts\python.exe -m pytest <path> -v`.
- Commit after each task; do NOT push. TDD throughout.

## Model tuning
**T1** is Haiku (mechanical backend). **T2** (shared component), **T3** (New Job adopt), **T4** (Edit Job adopt), **T5** (docs) are Sonnet (refactors/judgment).

## File structure
- Create `frontend/src/components/PerPrinterConfig.tsx` (shared component + `PerPrinterCfg` + `defaultPerPrinterCfg`) and `PerPrinterConfig.test.tsx` (T2).
- Modify `backend/app/api/routes/jobs.py` (`get_job_details` + `update_job_configs` tool_index) (T1).
- Modify `frontend/src/screens/NewJobScreen.tsx` (adopt shared; relax validation) (T3).
- Modify `frontend/src/screens/EditJobScreen.tsx` (adopt shared; pre-fill + payload tool_index; relax validation) + `frontend/src/api/queue.ts` (`ApiJobPrinterConfig.tool_index`) (T4).

---

## Task 1: Backend — round-trip `tool_index` through Edit Job

**Model: Haiku.**

**Files:**
- Modify: `backend/app/api/routes/jobs.py`
- Test: `backend/tests/api/test_jobs_tool_index_roundtrip.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_jobs_tool_index_roundtrip.py
import inspect
from app.api.routes import jobs


def test_get_job_details_serializes_tool_index():
    # get_job_details builds each printer_configs entry with a tool_index key.
    src = inspect.getsource(jobs.get_job_details)
    assert '"tool_index"' in src


def test_update_job_configs_persists_tool_index():
    # update_job_configs constructs JobPrinterConfig with tool_index from the input.
    src = inspect.getsource(jobs.update_job_configs)
    assert "tool_index=cfg.tool_index" in src
```

(These are source-level guards — cheap and exact for two one-line additions. The behavior is already covered by Project 2's `JobPrinterConfig.tool_index` + `PrinterConfigInput.tool_index` tests.)

- [ ] **Step 2: Run — confirm FAIL**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/api/test_jobs_tool_index_roundtrip.py -v`
Expected: FAIL — neither string present.

- [ ] **Step 3: Implement**

In `backend/app/api/routes/jobs.py`, `get_job_details` — in the `printer_configs.append({...})` dict (after `"filament_color": cfg.filament_color,`), add:
```python
            "tool_index": cfg.tool_index,
```

In `update_job_configs` — in the `JobPrinterConfig(...)` it constructs in the rebuild loop (after `filament_color=cfg.filament_color,`), add:
```python
            tool_index=cfg.tool_index,
```

- [ ] **Step 4: Run — confirm PASS + full suite**

Run: `cd backend && backend\.venv\Scripts\python.exe -m pytest tests/api/test_jobs_tool_index_roundtrip.py -v` → PASS.
Then `cd backend && backend\.venv\Scripts\python.exe -m pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/jobs.py backend/tests/api/test_jobs_tool_index_roundtrip.py
git commit -m "feat(jobs): round-trip tool_index through get_job_details + update_job_configs"
```

---

## Task 2: Shared `PerPrinterConfig` component (with defer)

**Model: Sonnet.**

**Files:**
- Create: `frontend/src/components/PerPrinterConfig.tsx`
- Test: `frontend/src/components/PerPrinterConfig.test.tsx`

- [ ] **Step 1: Write the failing test**

Model the render/mocks on the existing tool-picker test in `frontend/src/screens/NewJobScreen.test.tsx` (it renders `PerPrinterConfig` standalone with a mock printer — reuse its mock setup for `useSpoolmanConfig`/`useFilaments`/`getPrinterProfiles` if it has any). Create `frontend/src/components/PerPrinterConfig.test.tsx`:

```tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { PerPrinterConfig, defaultPerPrinterCfg } from './PerPrinterConfig';

const MULTI = {
  id: 3, name: 'U1', printer_type: 'snapmaker_extended', current_orca_printer_profile: 'U1',
  loaded_filaments: [
    { slot: 0, type: 'PLA', color: '#fff', name: 'PLA', filament_profile: 'PLA @U1' },
    { slot: 1, type: 'PETG', color: '#000', name: 'PETG', filament_profile: 'PETG @U1' },
    { slot: 2, type: 'TPU', color: '#0f0', name: 'TPU', filament_profile: 'TPU @U1' },
  ],
};
const SINGLE = { id: 1, name: 'Mono', printer_type: 'elegoo_centauri', current_orca_printer_profile: 'M', loaded_filaments: [] };

function renderCfg(printer: any, config = defaultPerPrinterCfg()) {
  const onChange = vi.fn();
  render(<PerPrinterConfig printerId={String(printer.id)} printers={[printer as any]} config={config} onChange={onChange} />);
  return onChange;
}

describe('PerPrinterConfig', () => {
  it('defaultPerPrinterCfg is all-null (defer)', () => {
    expect(defaultPerPrinterCfg()).toEqual({
      printProfile: null, filamentProfile: null, filamentId: null,
      filamentType: null, filamentColor: null, toolIndex: null,
    });
  });

  it('multi-tool: tool select offers Any/default first and writes toolIndex+slot identity', async () => {
    const onChange = renderCfg(MULTI);
    const sel = await screen.findByTestId('tool-select');
    expect((sel as HTMLSelectElement).options[0].textContent).toMatch(/Any \/ default/i);
    fireEvent.change(sel, { target: { value: '2' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ toolIndex: 2, filamentProfile: 'TPU @U1', filamentType: 'TPU' }));
  });

  it('multi-tool: selecting Any/default defers (toolIndex null + cleared ask)', async () => {
    const onChange = renderCfg(MULTI, { ...defaultPerPrinterCfg(), toolIndex: 1, filamentType: 'PETG' });
    const sel = await screen.findByTestId('tool-select');
    fireEvent.change(sel, { target: { value: '' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ toolIndex: null }));
  });

  it('single-tool: defaults to defer (mode select present, ask hidden), Require reveals the ask', async () => {
    renderCfg(SINGLE);
    const mode = await screen.findByTestId('filament-mode');
    expect((mode as HTMLSelectElement).value).toBe('defer');
    expect(screen.queryByTestId('filament-type-input')).toBeNull();
    expect(screen.queryByTestId('filament-catalog-select')).toBeNull();
    fireEvent.change(mode, { target: { value: 'require' } });
    // require mode shows either the catalog select or the manual type input
    expect(screen.queryByTestId('filament-catalog-select') || screen.queryByTestId('filament-type-input')).not.toBeNull();
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Run: `cd frontend && npx vitest run src/components/PerPrinterConfig.test.tsx`
Expected: FAIL — module `./PerPrinterConfig` not found.

- [ ] **Step 3: Implement**

Create `frontend/src/components/PerPrinterConfig.tsx` with EXACTLY:

```tsx
import { useEffect, useState } from 'react';
import type { ApiPrinter } from '../api/printers';
import { getPrinterProfiles } from '../api/queue';
import { useSpoolmanConfig, useFilaments, filamentDisplayName } from '../api/spoolman';

export interface PerPrinterCfg {
  printProfile: string | null;
  filamentProfile: string | null;
  filamentId: number | null;
  filamentType: string | null;
  filamentColor: string | null;
  toolIndex: number | null;
}

export function defaultPerPrinterCfg(): PerPrinterCfg {
  return {
    printProfile: null, filamentProfile: null, filamentId: null,
    filamentType: null, filamentColor: null, toolIndex: null,
  };
}

const BADGE: Record<string, string> = {
  bambu: 'P1S', elegoo_centauri: 'ECC', snapmaker_extended: 'U1',
};
const FILAMENT_TYPES = ['PLA', 'PLA+', 'PETG', 'ABS', 'ASA', 'TPU', 'Nylon', 'PC'];

function usePrinterProfiles(printerId: number | null): { printProfiles: string[]; filamentProfiles: string[] } {
  const [data, setData] = useState<{ printProfiles: string[]; filamentProfiles: string[] }>({
    printProfiles: [], filamentProfiles: [],
  });
  useEffect(() => {
    if (printerId == null) return;
    let alive = true;
    getPrinterProfiles(printerId)
      .then(p => { if (alive) setData({ printProfiles: p.print_profiles, filamentProfiles: p.filament_profiles }); })
      .catch(() => {});
    return () => { alive = false; };
  }, [printerId]);
  return data;
}

export function PerPrinterConfig({ printerId, printers, config, onChange }: {
  printerId: string;
  printers: ApiPrinter[];
  config: PerPrinterCfg;
  onChange: (patch: Partial<PerPrinterCfg>) => void;
}) {
  const pid = Number(printerId);
  const printer = printers.find(p => p.id === pid);
  const { printProfiles } = usePrinterProfiles(pid);
  const { config: spoolmanCfg } = useSpoolmanConfig();
  const spoolmanActive = !!(spoolmanCfg?.enabled && spoolmanCfg?.url);
  const filaments = useFilaments(spoolmanActive);

  // Single-tool filament mode: 'defer' (use loaded) vs 'require'. Derived so Edit
  // Job restores it; default = defer.
  const [requireFilament, setRequireFilament] = useState(
    () => !!(config.filamentType || config.filamentProfile),
  );
  const [manualMode, setManualMode] = useState(
    () => !spoolmanActive || (config.filamentId === null && !!config.filamentType),
  );

  useEffect(() => {
    if (requireFilament && (!spoolmanActive || manualMode) && config.filamentColor === null) {
      onChange({ filamentColor: '#888888' });
    }
  }, [spoolmanActive, manualMode, requireFilament]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!printer) return null;
  const badge = BADGE[printer.printer_type] ?? printer.printer_type.slice(0, 3).toUpperCase();
  const slots = printer.loaded_filaments ?? [];
  const catalogValue = config.filamentId != null
    ? (filaments.find(f => f.id === config.filamentId) != null
        ? filamentDisplayName(filaments.find(f => f.id === config.filamentId)!) : '')
    : (config.filamentProfile ?? '');

  function clearAsk() {
    onChange({ filamentProfile: null, filamentId: null, filamentType: null, filamentColor: null });
  }

  return (
    <div style={{ padding: 14, background: 'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 10 }}>
      <div className="row gap-2" style={{ alignItems: 'center', marginBottom: 12 }}>
        <span className="elig on">{badge}</span>
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="small" style={{ fontWeight: 500 }}>{printer.name}</div>
          <div className="tiny muted">{printer.printer_type}</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label className="label">Print profile</label>
          <select data-testid="print-profile-select" className="select"
                  value={config.printProfile ?? ''}
                  onChange={e => onChange({ printProfile: e.target.value || null })}>
            <option value="">— select profile —</option>
            {printProfiles.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          {printProfiles.length === 0 && (
            <div className="tiny muted" style={{ marginTop: 4 }}>No profiles found for this printer</div>
          )}
        </div>

        {slots.length >= 2 ? (
          <div>
            <label className="label">Tool</label>
            <select data-testid="tool-select" className="select"
                    value={config.toolIndex ?? ''}
                    onChange={e => {
                      const v = e.target.value;
                      if (v === '') { onChange({ toolIndex: null }); clearAsk(); return; }
                      const ti = Number(v);
                      const s = slots[ti];
                      onChange({
                        toolIndex: ti,
                        filamentProfile: s?.filament_profile ?? null,
                        filamentId: null,
                        filamentType: s?.type ?? null,
                        filamentColor: s?.color ?? null,
                      });
                    }}>
              <option value="">Any / default tool</option>
              {slots.map((s, i) => (
                <option key={i} value={i}>T{i} · {s.type || '—'}{s.name ? ` (${s.name})` : ''}</option>
              ))}
            </select>
            <div className="tiny muted" style={{ marginTop: 4 }}>
              {config.toolIndex == null
                ? 'Prints on the default tool with whatever is loaded.'
                : 'Prints on this physical tool; its loaded filament profile is used to slice.'}
            </div>
          </div>
        ) : (
          <div>
            <label className="label">Filament</label>
            <select data-testid="filament-mode" className="select"
                    value={requireFilament ? 'require' : 'defer'}
                    onChange={e => {
                      const req = e.target.value === 'require';
                      setRequireFilament(req);
                      if (!req) clearAsk();
                    }}>
              <option value="defer">Use loaded filament</option>
              <option value="require">Require specific filament</option>
            </select>
            {requireFilament && (
              <div style={{ marginTop: 8 }}>
                {spoolmanActive && !manualMode ? (
                  <select data-testid="filament-catalog-select" className="select" value={catalogValue}
                          onChange={e => {
                            const v = e.target.value;
                            if (v === '__manual__') { setManualMode(true); clearAsk(); return; }
                            const f = filaments.find(f => filamentDisplayName(f) === v) ?? null;
                            onChange({
                              filamentProfile: v || null, filamentId: f?.id ?? null,
                              filamentType: f?.material ?? null,
                              filamentColor: f?.color_hex ? `#${f.color_hex}` : null,
                            });
                          }}>
                    <option value="">— select filament —</option>
                    {filaments.map(f => (
                      <option key={f.id} value={filamentDisplayName(f)}>{filamentDisplayName(f)} · {f.material}</option>
                    ))}
                    <option value="__manual__">Enter manually…</option>
                  </select>
                ) : (
                  <div className="col gap-2">
                    <div className="row gap-2">
                      <input data-testid="filament-type-input" className="input" list="filament-types"
                             placeholder="Type (PLA, PETG, ABS…)" value={config.filamentType ?? ''}
                             onChange={e => onChange({ filamentType: e.target.value || null, filamentProfile: e.target.value || null, filamentId: null })}
                             style={{ flex: 1 }} />
                      {spoolmanActive && (
                        <button className="btn ghost sm" onClick={() => { setManualMode(false); clearAsk(); }}>↩ Catalog</button>
                      )}
                    </div>
                    <datalist id="filament-types">
                      {FILAMENT_TYPES.map(t => <option key={t} value={t} />)}
                    </datalist>
                    <div className="row gap-2" style={{ alignItems: 'center' }}>
                      <input data-testid="filament-color-input" type="color" value={config.filamentColor ?? '#888888'}
                             onChange={e => onChange({ filamentColor: e.target.value })}
                             style={{ width: 36, height: 28, padding: 2, borderRadius: 6, border: '1px solid var(--border-2)', background: 'var(--bg-1)', cursor: 'pointer', flexShrink: 0 }} />
                      <span className="tiny muted">{config.filamentColor ?? '#888888'}</span>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run — confirm PASS + build**

Run: `cd frontend && npx vitest run src/components/PerPrinterConfig.test.tsx` → PASS.
Then `cd frontend && npm run build` → clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/PerPrinterConfig.tsx frontend/src/components/PerPrinterConfig.test.tsx
git commit -m "feat(jobs): shared PerPrinterConfig component with defer + tool picker"
```

---

## Task 3: New Job adopts the shared component

**Model: Sonnet.**

**Files:**
- Modify: `frontend/src/screens/NewJobScreen.tsx`
- Test: `frontend/src/screens/NewJobScreen.test.tsx` (the existing tool-picker test moves to the shared component's test; update imports)

- [ ] **Step 1: Adopt the shared component**

In `NewJobScreen.tsx`:
1. Add import: `import { PerPrinterConfig, defaultPerPrinterCfg, type PerPrinterCfg } from '../components/PerPrinterConfig';`
2. **Delete** the local `interface PerPrinterCfg { ... }` (~line 26-32) and the local `export function PerPrinterConfig(...)` component (~line 438-600) and the local `usePrinterProfiles` (~line 83) and the local `BADGE` (~line 59) **only if** they are now unused elsewhere in the file (search each symbol; if `BADGE`/`usePrinterProfiles` is referenced elsewhere in NewJobScreen, leave that one). Remove now-unused imports (`useFilaments`, `filamentDisplayName`, `useSpoolmanConfig`, `getPrinterProfiles`) only if nothing else uses them (the build's `noUnusedLocals` will tell you).
3. Replace the two inline `{ printProfile: null, filamentProfile: null, filamentId: null, filamentType: null, filamentColor: null, toolIndex: null }` default literals (~line 711 fallback and ~line 1122 toggle) with `defaultPerPrinterCfg()`.
4. The `createJob` payload (~line 1173) already sends `tool_index` — keep it.

- [ ] **Step 2: Relax submit validation for defer**

In the per-plate completeness predicate (~line 1153-1156), filament is no longer required — only the print profile. Change:
```tsx
    return cfg.selectedPrinters.every(pid => {
      const pp = cfg.perPrinter[pid];
      return !!(pp && pp.printProfile && pp.filamentType && pp.filamentColor);
    });
```
to:
```tsx
    return cfg.selectedPrinters.every(pid => {
      const pp = cfg.perPrinter[pid];
      return !!(pp && pp.printProfile);
    });
```

- [ ] **Step 3: Move the old inline tool-picker test to the shared test**

The tool-picker test added in Project 2 lives in `NewJobScreen.test.tsx` and imported `PerPrinterConfig` from `./NewJobScreen`. Since the component now lives in the shared file (covered by `PerPrinterConfig.test.tsx`), **delete that one test** from `NewJobScreen.test.tsx` (and the now-unused `PerPrinterConfig`/`fireEvent` imports if nothing else in the file uses them). Keep all other NewJobScreen tests.

- [ ] **Step 4: Run — build + tests**

Run: `cd frontend && npm run build` → clean (fix any unused-symbol errors flagged by `noUnusedLocals`).
Then `cd frontend && npx vitest run src/screens/NewJobScreen.test.tsx` → PASS. Then `npx vitest run` (full) → green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/screens/NewJobScreen.tsx frontend/src/screens/NewJobScreen.test.tsx
git commit -m "refactor(newjob): use shared PerPrinterConfig; defer makes filament optional"
```

---

## Task 4: Edit Job adopts the shared component (tool parity)

**Model: Sonnet.**

**Files:**
- Modify: `frontend/src/screens/EditJobScreen.tsx`, `frontend/src/api/queue.ts`
- Test: `frontend/src/screens/EditJobScreen.test.tsx` (create if absent, else extend)

- [ ] **Step 1: Expose `tool_index` on the API type**

In `frontend/src/api/queue.ts`, add to the `ApiJobPrinterConfig` interface:
```typescript
  tool_index: number | null;
```

- [ ] **Step 2: Adopt the shared component in Edit Job**

In `EditJobScreen.tsx`:
1. Add import: `import { PerPrinterConfig, defaultPerPrinterCfg, type PerPrinterCfg } from '../components/PerPrinterConfig';`
2. **Delete** the local `interface PerPrinterCfg`, the local `PerPrinterConfigEditor` component (~line 102-200+), the local `usePrinterProfiles` (~line 31), and the local `BADGE` (~line 61) if now unused (search each). Remove now-unused imports per `noUnusedLocals`. Replace the JSX usage `<PerPrinterConfigEditor .../>` (~line 355) with `<PerPrinterConfig .../>` (same props: `printerId`, `printers`, `config`, `onChange`).
3. **Pre-fill toolIndex** (~line 233-239): add `toolIndex: c.tool_index ?? null,` to the `pp[String(c.printer_id)] = {...}` object.
4. **Default literal** in `togglePrinter` (~line 254): replace `{ printProfile: null, ... filamentColor: null }` with `defaultPerPrinterCfg()`.
5. **Payload** (~line 273-280): add `tool_index: perPrinter[sid].toolIndex ?? null,` to each config object in the `updateJobConfigs` map.

- [ ] **Step 3: Relax Edit completeness for defer**

In `isComplete` (~line 263-266), require only the print profile:
```tsx
  const isComplete = selectedPrinters.length > 0 && selectedPrinters.every(sid => {
    const pp = perPrinter[sid];
    return !!(pp?.printProfile);
  });
```

- [ ] **Step 4: Write a test that Edit pre-fills + sends tool_index**

Create/extend `frontend/src/screens/EditJobScreen.test.tsx` — assert that after loading a job whose config has `tool_index: 2`, the per-printer config restores the tool, and the save payload includes `tool_index: 2`. If a full screen render is impractical (data fetching), at minimum unit-test the pre-fill mapping: a helper or inline assertion that a `printer_configs` entry with `tool_index: 2` produces a `PerPrinterCfg` with `toolIndex: 2`. (Mirror how the other `*.test.tsx` files mock `getJobDetails`/`fetch`.) Keep it green and meaningful — don't assert on mocks only.

- [ ] **Step 5: Run — build + tests + commit**

Run: `cd frontend && npm run build` → clean. Then `cd frontend && npx vitest run` → green.
```bash
git add frontend/src/screens/EditJobScreen.tsx frontend/src/api/queue.ts frontend/src/screens/EditJobScreen.test.tsx
git commit -m "feat(editjob): shared PerPrinterConfig (tool parity) + round-trip tool_index; defer optional filament"
```

---

## Task 5: Docs sync

**Model: Sonnet** (skill-driven).

Run `themis-docs-sync` against the branch diff. Update:
- `docs/agent/frontend.md` — screens/components: new `components/PerPrinterConfig.tsx` (shared per-printer job-config control: print profile + tool picker w/ "Any/default tool" defer for multi-tool printers, "Use loaded / Require" toggle for single-tool); New Job + Edit Job now consume it (deleted their local copies). Note "defer" = `tool_index=null` + empty ask → first loaded slot.
- `docs/agent/backend.md` — note `get_job_details`/`update_job_configs` round-trip `tool_index`.

Commit `docs(agent): sync for defer + shared per-printer job config`.

---

## Final verification
`cd frontend && npm run build` + `npx vitest run` green; `cd backend && backend\.venv\Scripts\python.exe -m pytest -q` green. Then in the running app: New Job and Edit Job both show the tool picker for the U1 and the "Use loaded / Require" toggle for single-tool printers; a deferred job (no filament chosen) is submittable and prints with the loaded slot's profile; editing a job restores its tool/defer selection.

## Self-review notes (author)
- **Spec coverage:** shared component (T2) used by New (T3) + Edit (T4); defer control — multi-tool "Any/default" + single-tool "Use loaded/Require" default-defer (T2); defer = `tool_index=null`+empty ask, no schema change; backend `tool_index` round-trip (T1) + `ApiJobPrinterConfig` (T4) + Edit pre-fill/payload (T4); validation relaxed so defer is submittable (T3/T4); docs (T5). All spec sections mapped.
- **Type/name consistency:** one canonical `PerPrinterCfg` + `defaultPerPrinterCfg()` exported from the shared file; both screens import it and delete their local copies. `data-testid`s (`tool-select`, `filament-mode`, `filament-catalog-select`, `filament-type-input`, `print-profile-select`) consistent across component + tests. `tool_index` (snake) at API boundary; `toolIndex` (camel) in `PerPrinterCfg`.
- **Backward-compat / risk:** the consolidation deletes duplicated code — the `noUnusedLocals` build is the guard for dangling symbols/imports. Defer relaxes validation (filament optional) but the queue already handled empty-ask, so existing filament-ask jobs behave unchanged.
