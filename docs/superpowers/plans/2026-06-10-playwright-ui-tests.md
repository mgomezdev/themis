# Playwright UI Tests (Mocked API) — Implementation Plan (Sub-project D)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A Playwright browser-test suite that drives the filament/tool job flows end-to-end against a route-mocked API — deterministic, no real backend/printers, zero print risk.

**Architecture:** `@playwright/test` in `frontend/`; a `mock-api.ts` fixture intercepts `**/api/v1/**` with canned data and captures mutating-request bodies; three specs drive Fleet (multi-slot loaded-filaments editor), New Job (defer/tool/mapping + `createJob` payload), Edit Job (pre-fill + `updateJobConfigs` round-trip). `webServer` reuses the running dev server.

**Tech Stack:** Playwright, React/Vite/TS.

**Spec:** `docs/superpowers/specs/2026-06-10-playwright-ui-tests-design.md`. **Branch:** `worktree-multi-material-tool-mapping` (worktree at `C:\Users\mgome\Documents\projects\themis\.claude\worktrees\multi-material-tool-mapping`).

## Conventions (every subagent)
- Work ONLY in the worktree; absolute paths. Run from `frontend/`.
- Run a spec: `cd frontend && npx playwright test e2e/<name>.spec.ts --project=chromium`. Debug a flow live: `npx playwright test --ui` or `--headed`.
- **e2e is iterative against the real DOM:** READ the target screen's source first, then drive it with Playwright's auto-waiting locators (`getByTestId`, `getByRole`, `getByText`); iterate until green. Don't guess selectors — inspect (`--headed`/`--debug` or read the JSX). Assertions must check **real DOM + captured request payloads**, never mocks-testing-mocks.
- The dev server is already running on :5173 (mocked routes apply per-page, so the live backend is bypassed — no print risk). Commit after each task; do NOT push.
- **Important UI fact:** the `tool-select` / `map-tool-*` / `filament-mode` controls live in **New Job & Edit Job** (the shared `PerPrinterConfig`). The **Fleet card** hosts the multi-slot **loaded-filaments** editor (define what's in each printer slot) — a different control. Scope each spec to the right screen.

## Model tuning
**T1** Haiku (scaffold). **T2, T3, T4, T5** Sonnet (read screens, drive the real UI, docs).

## File structure
- Create `frontend/playwright.config.ts`, `frontend/e2e/mock-api.ts`, `frontend/e2e/smoke.spec.ts` (T1), `…/fleet.spec.ts` (T2), `…/new-job.spec.ts` (T3), `…/edit-job.spec.ts` (T4).
- Modify `frontend/package.json` (T1), `frontend/.gitignore` (T1), `docs/agent/frontend.md` (T5).

---

## Task 1: Setup — Playwright + mock fixture + smoke spec

**Model: Haiku.**

- [ ] **Step 1: Install Playwright**

```
cd C:\Users\mgome\Documents\projects\themis\.claude\worktrees\multi-material-tool-mapping\frontend
npm install -D @playwright/test
npx playwright install chromium
```
(The browser download is ~150MB, one-time. If `playwright install` fails on the network, report BLOCKED.)

- [ ] **Step 2: `playwright.config.ts`**

Create `frontend/playwright.config.ts`:
```typescript
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: 'list',
  use: { baseURL: 'http://localhost:5173', trace: 'on-first-retry' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
```

- [ ] **Step 3: `package.json` scripts + gitignore**

Add to `frontend/package.json` `scripts`: `"test:e2e": "playwright test"`, `"test:e2e:ui": "playwright test --ui"`.
Append to `frontend/.gitignore` (create if absent): `playwright-report/`, `test-results/`, `/blob-report/`, `.last-run.json`.

- [ ] **Step 4: `e2e/mock-api.ts` — the shared fixture**

Create `frontend/e2e/mock-api.ts`. It installs one route handler for `**/api/v1/**` that matches method+path to canned JSON and captures mutating bodies. Default fixture data: a multi-tool U1 (id 3, 4 loaded slots) + a single-tool printer (id 1); one multi-material file (id 1, 2 model-filaments, 2 plates); spoolman disabled.

```typescript
import type { Page, Route } from '@playwright/test';

export interface Mocks { captured: { url: string; method: string; body: any }[]; }

const U1 = {
  id: 3, name: 'U1', printer_type: 'snapmaker_extended', connected: true, state: 'IDLE',
  current_orca_printer_profile: 'Snapmaker U1 (0.4 nozzle)', queue_on: true, enabled: true,
  awaiting_plate_clear: false, progress: 0, remaining_time: 0,
  temperatures: { nozzle: 27, bed: 25, extruders: [{index:0,temp:27},{index:1,temp:27},{index:2,temp:28},{index:3,temp:28}] },
  loaded_filaments: [
    { slot: 0, filament_id: null, name: 'PLA White', type: 'PLA', color: '#ffffff', filament_profile: 'Generic PLA @System' },
    { slot: 1, filament_id: null, name: 'PETG Black', type: 'PETG', color: '#000000', filament_profile: 'Generic PETG @System' },
    { slot: 2, filament_id: null, name: 'TPU Green', type: 'TPU', color: '#00ff00', filament_profile: 'Generic TPU @System' },
    { slot: 3, filament_id: null, name: 'PLA Blue', type: 'PLA', color: '#0000ff', filament_profile: 'Generic PLA @System' },
  ],
};
const MONO = {
  id: 1, name: 'Mono', printer_type: 'elegoo_centauri', connected: true, state: 'IDLE',
  current_orca_printer_profile: 'Mono', queue_on: true, enabled: true, awaiting_plate_clear: false,
  progress: 0, remaining_time: 0, temperatures: { nozzle: 25, bed: 25 }, loaded_filaments: [],
};
const PROFILES = { print_profiles: ['0.20mm Standard', '0.08 Extra Fine'], filament_profiles: ['Generic PLA @System', 'Generic PETG @System', 'Generic TPU @System'] };
const FILE = { id: 1, original_filename: 'multi.3mf', folder: '/', plate_count: 2 };
const PLATES = [
  { plate_number: 1, estimated_time: 3600, filament_g: 12, thumbnail_path: null },
  { plate_number: 2, estimated_time: 1800, filament_g: 6, thumbnail_path: null },
];
const MODEL_FILAMENTS = [{ index: 1, color: '#F78E0E', type: 'PLA' }, { index: 2, color: '#003776', type: 'PLA' }];

type Json = (route: Route, body?: any) => Promise<void>;
const ok: Json = (route, body = {}) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

export async function mockApi(page: Page, over: Partial<{
  printers: any[]; fleet: any[]; profiles: any; files: any[]; plates: any[]; modelFilaments: any[]; jobDetails: any;
}> = {}): Promise<Mocks> {
  const printers = over.printers ?? [MONO, U1];
  const fleet = over.fleet ?? [MONO, U1];
  const profiles = over.profiles ?? PROFILES;
  const files = over.files ?? [FILE];
  const plates = over.plates ?? PLATES;
  const modelFilaments = over.modelFilaments ?? MODEL_FILAMENTS;
  const mocks: Mocks = { captured: [] };

  await page.route('**/api/v1/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/v1/, '');
    const method = req.method();

    if (method !== 'GET') {
      let body: any = null;
      try { body = req.postDataJSON(); } catch { /* no body */ }
      mocks.captured.push({ url: path, method, body });
      return ok(route, { id: 123, status: 'queued' });
    }
    if (path === '/printers' || path === '/printers/') return ok(route, printers);
    if (path === '/printers/types') return ok(route, []);
    if (path === '/fleet') return ok(route, fleet);
    let m;
    if ((m = path.match(/^\/printers\/(\d+)\/profiles$/))) return ok(route, profiles);
    if ((m = path.match(/^\/printers\/(\d+)$/))) return ok(route, printers.find(p => p.id === +m[1]) ?? {});
    if (path === '/files') return ok(route, files);
    if ((m = path.match(/^\/files\/(\d+)\/plates$/))) return ok(route, plates);
    if ((m = path.match(/^\/files\/(\d+)\/model-filaments$/))) return ok(route, modelFilaments);
    if (path === '/settings/spoolman') return ok(route, { enabled: false });
    if (path === '/spoolman/filaments' || path === '/spoolman/spools') return ok(route, []);
    if ((m = path.match(/^\/jobs\/(\d+)\/details$/)) || (m = path.match(/^\/jobs\/(\d+)$/)))
      return over.jobDetails ? ok(route, over.jobDetails) : ok(route, {});
    if (path === '/queue/config' || path === '/settings/queue') return ok(route, { check_interval_minutes: 5 });
    return ok(route, {});  // permissive default for any unlisted GET
  });
  return mocks;
}
```
(The exact response SHAPES may need tweaks once you drive a real screen — adjust a field if the UI expects it; the structure above mirrors the live API smoke results. Add any GET path a screen actually calls that isn't listed.)

- [ ] **Step 5: Smoke spec + run**

Create `frontend/e2e/smoke.spec.ts`:
```typescript
import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

test('fleet loads with mocked printers', async ({ page }) => {
  await mockApi(page);
  await page.goto('/fleet');
  await expect(page.getByText('U1')).toBeVisible();
});
```
Run: `cd frontend && npx playwright test --project=chromium` → the smoke test PASSES (the app renders the U1 from the mocked `/fleet`). If "U1" text isn't the right anchor, read `FleetScreen.tsx` and assert on a stable element that proves the fleet rendered.

- [ ] **Step 6: Commit**

```bash
git add frontend/playwright.config.ts frontend/e2e/mock-api.ts frontend/e2e/smoke.spec.ts frontend/package.json frontend/.gitignore frontend/package-lock.json
git commit -m "test(e2e): Playwright setup + mocked-API fixture + smoke spec"
```

---

## Task 2: `fleet.spec.ts` — multi-slot loaded-filaments editor

**Model: Sonnet.**

**Goal:** On `/fleet`, open the U1's printer editor and verify the **multi-slot loaded-filaments editor** (the control that defines what filament is in each slot — color/type/name/profile + "Add slot"). This is the Sub-project-A multi-slot editor on the Fleet card / Edit-printer modal.

- [ ] **Step 1: Read `frontend/src/screens/FleetScreen.tsx`** to find: how a printer card is opened/expanded, the affordance that opens the loaded-filaments editor (an "Edit"/"Change" button or the `EditPrinterModal`), and the slot rows (look for the `updateSlot`/"Add slot"/color/type/`filament_profile` controls and any `data-testid`s). Decide stable locators.

- [ ] **Step 2: Write `frontend/e2e/fleet.spec.ts`**

```typescript
import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

test.describe('Fleet — loaded filament slots', () => {
  test('U1 shows its 4 loaded slots and an Add-slot affordance', async ({ page }) => {
    await mockApi(page);
    await page.goto('/fleet');
    // open the U1 card / printer editor (drive per the source read in Step 1)
    // ... open editor ...
    // Assert the four loaded slots render (PLA White / PETG Black / TPU Green / PLA Blue)
    await expect(page.getByText('PETG Black')).toBeVisible();
    // Assert the "Add slot" affordance exists
    await expect(page.getByRole('button', { name: /add slot/i })).toBeVisible();
  });
});
```
Fill in the open-editor steps from Step 1. If the slots are only shown after clicking "Change"/"Edit", click it first (Playwright auto-waits). Assert at least: ≥1 mocked slot's filament name is visible AND the add-slot control is present. (If the editor uses different copy than "Add slot", match the real text.)

- [ ] **Step 3: Run + iterate**

`cd frontend && npx playwright test e2e/fleet.spec.ts --project=chromium` → PASS. Use `--headed`/`--debug` to inspect if a locator doesn't resolve; fix selectors against the real DOM.

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/fleet.spec.ts
git commit -m "test(e2e): Fleet multi-slot loaded-filaments editor"
```

---

## Task 3: `new-job.spec.ts` — defer / tool pick / mapping + createJob payload

**Model: Sonnet.**

**Goal:** On `/queue/new`, drive the multi-step flow with the mocked multi-material file + U1, and assert (a) the per-printer control renders the right widget, (b) the submitted **`createJob` payload** carries `tool_index`/`filament_map`.

- [ ] **Step 1: Read `frontend/src/screens/NewJobScreen.tsx`** + `frontend/src/components/PerPrinterConfig.tsx`. Map the steps: Step 1 source file (how a file is selected — there's a file picker/list; the mocked `/files` returns file id 1), Step 2 per-plate config (select plate(s) + printer(s); the `PerPrinterConfig` renders per selected printer), job name, the Create button (disabled until `isComplete`), and the `createJob` call (~line 1010). Note the `data-testid`s in `PerPrinterConfig`: `print-profile-select`, `tool-select`, `filament-mode`, `map-tool-{index}`, `filament-type-input`.

- [ ] **Step 2: Write `frontend/e2e/new-job.spec.ts`**

```typescript
import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

test.describe('New Job — filament/tool selection', () => {
  test('multi-material file shows a mapping row per model filament; createJob sends filament_map', async ({ page }) => {
    const mocks = await mockApi(page);
    await page.goto('/queue/new');
    // 1) select the mocked file (id 1) ; 2) select a plate ; 3) select the U1 printer ;
    //    4) the multi-material file => PerPrinterConfig shows map-tool-0 + map-tool-1
    await expect(page.getByTestId('map-tool-0')).toBeVisible();
    await expect(page.getByTestId('map-tool-1')).toBeVisible();
    await page.getByTestId('map-tool-1').selectOption('0');   // map model filament 2 -> tool T0
    await page.getByTestId('print-profile-select').selectOption({ index: 1 });
    // fill job name, click Create
    // ...
    const created = mocks.captured.find(c => c.method === 'POST' && c.url.startsWith('/jobs'));
    expect(created).toBeTruthy();
    const cfgs = created!.body.printer_configs;
    expect(cfgs.some((c: any) => Array.isArray(c.filament_map) && c.filament_map.length === 2)).toBeTruthy();
  });

  test('single-tool printer defaults to defer; Require reveals the filament ask', async ({ page }) => {
    await mockApi(page, { /* select the MONO single-tool printer flow */ });
    await page.goto('/queue/new');
    // select file/plate/MONO -> filament-mode defaults to "defer"; switching to require shows filament-type-input
    await expect(page.getByTestId('filament-mode')).toHaveValue('defer');
  });
});
```
Drive the multi-step selection from Step 1's reading (clicking the file, the plate thumbnail/checkbox, the printer toggle, filling the name). The KEY assertions: the mapping rows render for the multi-material file; the captured `createJob` body's `printer_configs` carries `filament_map` (2 entries) for the U1; and the single-tool printer shows the `filament-mode` defer control. Iterate with `--headed` until the flow drives cleanly.

- [ ] **Step 3: Run + iterate** → `npx playwright test e2e/new-job.spec.ts --project=chromium` PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/new-job.spec.ts
git commit -m "test(e2e): New Job mapping/defer + createJob payload"
```

---

## Task 4: `edit-job.spec.ts` — pre-fill + updateJobConfigs round-trip

**Model: Sonnet.**

**Goal:** On `/jobs/1/edit`, with `getJobDetails` mocked to return a config carrying `tool_index`/`filament_map`, assert the controls **pre-fill** and a Save sends them back in `updateJobConfigs`.

- [ ] **Step 1: Read `frontend/src/screens/EditJobScreen.tsx`** — how it loads `getJobDetails` (the `/jobs/{id}` or `/jobs/{id}/details` GET), builds `perPrinter` from `printer_configs` (incl. `tool_index`/`filament_map`), renders `PerPrinterConfig`, and the Save button → `updateJobConfigs` (PATCH `/jobs/{id}/configs`). Confirm the exact details endpoint path so the mock matches (`mock-api.ts` already handles `/jobs/{id}` and `/jobs/{id}/details` via `over.jobDetails`).

- [ ] **Step 2: Write `frontend/e2e/edit-job.spec.ts`**

```typescript
import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api';

const JOB = {
  id: 1, status: 'queued', name: 'Edit me', block_reason: null,
  file: { id: 1, original_filename: 'multi.3mf' }, plate: { plate_number: 1 },
  printer_configs: [{
    printer_id: 3, printer_name: 'U1', printer_type: 'snapmaker_extended',
    print_profile: '0.20mm Standard', filament_profile: null, filament_id: null,
    filament_type: null, filament_color: null, tool_index: null,
    filament_map: [{ model_filament: 1, tool_index: 0 }, { model_filament: 2, tool_index: 1 }],
    slice_failed: false, slice_error: null,
  }],
};

test('Edit Job pre-fills filament_map and round-trips it on save', async ({ page }) => {
  const mocks = await mockApi(page, { jobDetails: JOB });
  await page.goto('/jobs/1/edit');
  // mapping pre-filled: map-tool-1 reflects tool_index 1
  await expect(page.getByTestId('map-tool-1')).toHaveValue('1');
  await page.getByTestId('map-tool-1').selectOption('2');     // change model filament 2 -> tool T2
  // click Save (read the button label/role from Step 1)
  // ...
  const saved = mocks.captured.find(c => c.method === 'PATCH' && c.url.includes('/configs'));
  expect(saved).toBeTruthy();
  const fm = saved!.body.printer_configs[0].filament_map;
  expect(fm).toContainEqual({ model_filament: 2, tool_index: 2 });
});
```
Adjust `JOB` fields if Edit Job's loader expects more (read Step 1). Drive the Save click per the screen. KEY: `map-tool-1` pre-fills to the saved value; after change + save, the captured `updateJobConfigs` body carries the updated `filament_map`.

- [ ] **Step 3: Run + iterate** → PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/edit-job.spec.ts
git commit -m "test(e2e): Edit Job pre-fill + updateJobConfigs round-trip"
```

---

## Task 5: Docs

**Model: Sonnet.** Update `docs/agent/frontend.md`: add the `frontend/e2e/` Playwright suite — mocked-API browser tests of Fleet (loaded-slot editor), New Job (defer/tool/mapping + createJob payload), Edit Job (pre-fill + updateJobConfigs); how to run (`npm run test:e2e`; needs `npx playwright install chromium` once); deterministic via `e2e/mock-api.ts` route mocks, no backend/printers. Commit `docs(agent): note the Playwright e2e suite`.

---

## Final verification
`cd frontend && npx playwright test --project=chromium` → all specs pass. `npm run build` + `npx vitest run` unchanged (Playwright is additive). After D, the branch (Project 2 + A/B/C/D) is ready to merge.

## Self-review notes (author)
- **Spec coverage:** setup + config + mock fixture + smoke (T1); Fleet loaded-slot editor (T2); New Job defer/tool/mapping + createJob payload (T3); Edit Job pre-fill + updateJobConfigs round-trip (T4); docs (T5). All spec sections mapped. Mutating-call capture (the payload-assert mechanism) is in `mock-api.ts` (T1) and used by T3/T4.
- **Scope correction vs spec:** the spec listed "single-tool picker" under Fleet; the actual UI puts `tool-select`/mapping in New/Edit Job (the Fleet card has the loaded-slot editor). The plan scopes each spec to the real screen and notes it in Conventions.
- **e2e realism:** each flow task says READ the screen first + drive with auto-waiting locators + iterate; assertions check real DOM + captured payloads, not mocks. The `mock-api.ts` shapes mirror the live API smoke output; fields adjustable per screen.
- **No print risk:** every non-GET is intercepted in `mock-api.ts` (captured + canned success) — no submit reaches the backend even though the live worktree backend is running.
