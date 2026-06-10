# Playwright UI Tests (Mocked API) — Design Spec (Sub-project D)

**Date:** 2026-06-10
**Status:** Approved (pending spec review)
**Branch:** `worktree-multi-material-tool-mapping`

## Goal & scope

Add a Playwright browser-test suite that exercises the **filament/tool job flows end-to-end in a real
browser** — the integration the 168 vitest component tests can't reach (routing, multi-screen flows, real
DOM, payload assembly). Deterministic via **route-mocked `/api/v1/**`**: no real backend, no printer
dependency, **zero print risk** (mutating calls are intercepted, never reach a printer).

**In scope:** Playwright setup in `frontend/`; a reusable API-mock fixture; three flows — Fleet card
(multi-slot filament editor + single-tool picker), New Job (defer toggle, single-tool pick, multi-material
filament→tool mapping, `createJob` payload), Edit Job (pre-fill + `updateJobConfigs` round-trip of
`tool_index`/`filament_map`).

**Out of scope:** testing the live backend (covered by 377 backend tests + the live API smoke); visual
regression/screenshots; non-job screens (Orders, Files, Settings); the live `/ws` WebSocket (live updates
are not needed for these flows).

## Architecture & components

### 1. Setup
- Dev-dependency `@playwright/test`; browser via `npx playwright install chromium` (note in README/CI;
  the install downloads ~150MB — a CI step, not committed).
- `frontend/playwright.config.ts`:
  - `testDir: './e2e'`, `testMatch: '**/*.spec.ts'`.
  - One project: `chromium`, headless, `baseURL: 'http://localhost:5173'`.
  - `webServer: { command: 'npm run dev', port: 5173, reuseExistingServer: true, timeout: 120_000 }`
    (locally reuses the running dev server; in CI it starts one).
  - `use: { trace: 'on-first-retry' }`; `retries: process.env.CI ? 1 : 0`.
- `package.json` scripts: `"test:e2e": "playwright test"`, `"test:e2e:ui": "playwright test --ui"`.
- `frontend/e2e/` holds the specs + the mock fixture. Add `frontend/e2e/.gitignore` for
  `playwright-report/`, `test-results/` (or extend the existing gitignore).

### 2. The mock layer — `frontend/e2e/mock-api.ts`
A helper `mockApi(page, overrides?)` installed before navigation:
- `await page.route('**/api/v1/**', route => …)` — match the request URL/method to a canned response.
  Default fixtures (overridable per test):
  - `GET /printers` → list incl. a multi-tool U1 (`snapmaker_extended`, `loaded_filaments` of ≥2 slots)
    and a single-tool printer.
  - `GET /fleet` → the same printers in fleet shape (`state`, `connected`, `temperatures`, `loaded_filaments`).
  - `GET /printers/{id}` → the printer (with `loaded_filaments`).
  - `GET /printers/{id}/profiles` → `{ print_profiles: [...], filament_profiles: [...] }`.
  - `GET /files` → one multi-material file (id 1) + plates available.
  - `GET /files/1/plates` → 2 plates.
  - `GET /files/1/model-filaments` → `[{index:1,color,type}, {index:2,color,type}]` (multi-material).
  - `GET /spoolman/config` (or whatever `useSpoolmanConfig` hits) → disabled (`{enabled:false}`) so the
    manual filament path renders deterministically.
- **Mutating calls** (`POST /jobs`, `PATCH /jobs/{id}/configs`): the route handler pushes
  `route.request().postDataJSON()` into a captured array the test can read, then fulfills with a canned
  success (e.g. `{id: 123}`). This is how tests assert the submitted `tool_index`/`filament_map` WITHOUT a
  backend.
- The `/ws` WebSocket: not mocked (Playwright `page.route` is HTTP-only). The screens render from the HTTP
  fetch; a failed WS is tolerated by the app. If a failed WS proves noisy/flaky, abort it with
  `page.routeWebSocket('**/ws', ws => ws.close())` (Playwright ≥1.48) — include only if needed.

Helper accessor: `mockApi` returns `{ captured }` (the array of captured mutating-request bodies) so tests
assert payloads.

### 3. Flow specs (`frontend/e2e/*.spec.ts`)

**`fleet.spec.ts`** — `/fleet`:
- Mock a U1 with ≥2 loaded slots. Expand its card; open the filament editor ("Change"); assert the
  **multi-slot editor** renders the slots + an "Add slot" affordance; assert the **`tool-select`** is
  present with an "Any / default tool" option and `T0…` entries. (Per Sub-projects A/B the Fleet card hosts
  the multi-slot editor; confirm the exact open affordance by reading `FleetScreen.tsx`.)

**`new-job.spec.ts`** — `/queue/new`:
- Mock the multi-material file + printers. Drive: pick the file → pick a plate → select the U1.
  - Assert the **mapping rows** appear (`map-tool-0`, `map-tool-1`) with the model filaments' colours;
    change `map-tool-1` to a different tool.
  - Select the single-tool printer instead → assert the **`filament-mode`** defer toggle (default "defer")
    and that "Require" reveals `filament-type-input`; and/or the **`tool-select`** for a ≥2-slot printer.
  - Fill the print profile (`print-profile-select`) + job name; submit. Assert the captured **`createJob`
    payload**'s `printer_configs[*]` carries the expected `tool_index` and/or `filament_map`.
  (Read `NewJobScreen.tsx` for the exact step gating — file→plate→printer selection order, the submit
  button label, required fields — and drive accordingly.)

**`edit-job.spec.ts`** — `/jobs/:id/edit`:
- Mock `GET /jobs/{id}` (details) returning a job whose `printer_configs[0]` has a `tool_index` (and a
  second config or the same with a `filament_map`). Navigate to `/jobs/1/edit`; assert the control
  **pre-fills** (the `tool-select` shows the saved value; `map-tool-*` show the saved mapping). Change one;
  click Save; assert the captured **`updateJobConfigs` payload** round-trips `tool_index`/`filament_map`.

Each spec calls `mockApi(page)` (with per-test overrides) before `page.goto(...)`.

## Error handling / determinism
- All network is mocked → no flakiness from real printer state or timing. `webServer.reuseExistingServer`
  avoids port conflicts with the running dev server.
- If a flow has async gating (e.g. profiles load before the printer config renders), use Playwright
  auto-waiting (`expect(locator).toBeVisible()`), not fixed sleeps.
- Mutating-call interception guarantees no submit ever reaches the (running) backend → no print.

## Testing / success criteria
- `cd frontend && npx playwright test` runs the 3 specs headless and passes.
- Tests assert real DOM + captured payloads (not mocks-testing-mocks): the `createJob`/`updateJobConfigs`
  bodies contain the right `tool_index`/`filament_map`; the mapping rows / tool picker / defer toggle
  render per the mocked printer/file.
- `npm run build` + `vitest` unchanged (Playwright is additive).

## File structure
**Create:** `frontend/playwright.config.ts`, `frontend/e2e/mock-api.ts`, `frontend/e2e/fleet.spec.ts`,
`frontend/e2e/new-job.spec.ts`, `frontend/e2e/edit-job.spec.ts`, `frontend/e2e/.gitignore`.
**Modify:** `frontend/package.json` (devDep + `test:e2e` scripts).
**Docs:** `docs/agent/frontend.md` — note the `e2e/` Playwright suite (mocked API; flows covered; how to run).

## Sequencing
1. Setup (deps, config, `mock-api.ts` skeleton) — `playwright test` runs an empty/trivial spec green.
2. `fleet.spec.ts`.
3. `new-job.spec.ts`.
4. `edit-job.spec.ts`.
5. Docs.
Each spec is independent; the implementer reads the screen source for exact selectors/step gating and
drives accordingly. After D, the branch (Project 2 + A/B/C/D) is ready to merge.
