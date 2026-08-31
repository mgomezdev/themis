# Frontend Reference

React 18 + Vite + TypeScript SPA in `frontend/src`. React Router. **No global store** — each screen
uses hooks that fetch on mount and merge live `/ws` events. Entry: `main.tsx` → `App.tsx`.

> **Styling lives in `styling.md`** — design tokens, the no-framework CSS approach (one global
> `app.css`, no Tailwind), the shared `components/ui.tsx` set, and the `StatusKey`→pill-tone mapping.
> Load it for any visual/CSS work; it's referenced inline below where it matters.

## Auth (`src/auth/*.tsx`, `src/api/client.ts`)

`App.tsx` wraps `<AppShell>` in `<AuthGate>` (`auth/AuthGate.tsx`). On mount: if no key is stored
(`auth/apiKeyStore.ts`'s `getApiKey()`, namespaced localStorage key `themis.apiKey`), `AuthGate` POSTs
`/api/v1/api-keys` with `{name: "Browser"}` via plain `fetch` (nothing to inject yet), stores the
returned raw key, and renders children — this is the browser's own device-credential-style key, there's
no login system. If that bootstrap call 401/403s (table already non-empty — e.g. two tabs racing), it
falls back to a manual "enter your API key" form instead. A live 401 from any `apiFetch` call (e.g. this
browser's key got revoked elsewhere) clears the stored key and re-shows the manual form, wired via
`client.ts`'s `setUnauthorizedHandler` callback rather than a per-call-site check.

Every `api/*.ts` file calls `apiFetch(url, init)` (`api/client.ts`) instead of raw `fetch` — it injects
`X-Api-Key` from the stored key. `withKeyParam(url)` (same file) appends `?key=` for the handful of
things that can't set a header: the 3 `/ws` connections (`queue.ts`/`orders.ts`/`fleet.ts`), the printer
snapshot/camera `<img>` src in `ui.tsx`, and `plateThumbnailUrl` in `queue.ts`. Manage keys at
Settings → API Keys (`SettingsScreen.tsx`'s `ApiKeysPage`, backed by `api/apiKeys.ts`).

## App shell & routing (`App.tsx`)

`AppShell` renders `Sidebar` + `Topbar` + `<Routes>`. Topbar title/crumbs/actions come from a
`screenConfig` map keyed by a normalized path; detail routes (`/orders/:id/edit`, `/jobs/:id`,
`/jobs/:id/edit`) are special-cased into synthetic keys (`/orders/edit`, `/jobs/detail`, `/jobs/edit`)
in the `path` computation. Sidebar badge counts come from `useQueue()`/`useOrders()`.

Routes: `/queue`, `/queue/new`, `/fleet`, `/orders`, `/orders/new`, `/orders/:id/edit`, `/jobs/:id`,
`/jobs/:id/edit`, `/files`, `/projects`, `/projects/new`, `/projects/:id`, `/projects/:id/edit`,
`/history`, `/settings/*`. (There is no top-level `/filaments` route or `FilamentsScreen` — filament↔orca
mapping now lives at Settings → Filament Mappings, see the Screens table.) To add a screen: add the `<Route>`, a
`screenConfig` entry (+ path-normalization case if it's a detail route), and a Sidebar link if top-level.

## Screens (`src/screens/*.tsx`)

| Screen | Purpose / notes |
|---|---|
| `QueueScreen` | Active job list (`useQueue`), filters, per-job side panel: status, block reason inline + slice-error fetch, low-stock warning strip (from `low_stock_warning`, see `data-model.md`) shown when present and the job isn't already failed/blocked, **Edit settings** / **Unblock** / **Cancel** / **View details** buttons. `DisplayJob` flattens `ApiJob`. |
| `NewJobScreen` | Upload → per-plate config: eligible printers (`PrinterPicker`), shared `PerPrinterConfig` (print profile required; filament optional/"defer"), single-select order picker. Multi-plate → multiple jobs. Completeness: print profile only (filament not required). |
| `EditJobScreen` | `/jobs/:id/edit` — reload configs via `getJobDetails` (incl. `tool_index`), re-pick printers + per-printer slicing via shared `PerPrinterConfig` (gained tool picker vs old local copy), `updateJobConfigs`. Completeness: print profile only. |
| `JobDetailScreen` | `/jobs/:id` — full read view: thumbnail, file/plate stats, per-printer slicing config (incl. slice errors, and a low-stock warning block from that config's `low_stock_warning` — computed per `job_printer_configs` row, independent of whether the job's been assigned), assigned printer; actions: edit / unblock / cancel. |
| `OrdersScreen` | `useOrders` accordion; per-order derived progress bar, parts table (Part/Material/Qty/Est), linked jobs (clickable → `/jobs/:id`), Hold/Edit/Delete. |
| `NewOrderScreen` | Create **and** edit (`/orders/:id/edit` via `useParams`). Parts table with Spoolman-aware filament picker; `createOrder`/`updateOrder`. |
| `ProjectsScreen` | `/projects` — list view (`useProjects`), filter by pending/active/completed, per-project progress bar, Delete / Generate (`generateProject`) / open. |
| `ProjectBuilderScreen` | `/projects/new` and `/projects/:id/edit` (same component, `useParams` branches create vs edit) — assemble a project's items (`FilamentRequirementPicker`, `PrinterEligibilityPicker`, `ProcessPresetPicker`), links, and non-printed parts before generating. |
| `ProjectDetailScreen` | `/projects/:id` — read view: linked jobs with live status (`STATUS_META`), parts checklist (`allocated` toggle → `updateProjectPart`), links, Generate. |
| `HistoryScreen` | `/history` — completed/failed/cancelled job history (`HistoryJob`, fetched directly via `apiFetch`, no dedicated `api/*.ts` hook), `OutcomeModal` for recording why a job failed. |
| `FleetScreen` | Printer cards (tile/row/expanded). Live telemetry/camera. Queue-off cue (orange border + badge), **Ready for new work** button (`markPlateCleared`), AMS/loaded filament, edit-printer modal (`EditForm`), `FilamentPicker` (writes `spoolman_spool_id`, sets `filament_id: null`). Maintenance: `useMaintenanceStatus()` builds `dueRowsByPrinter: Record<string, MaintenanceStatusRow[]>`, threaded through `FleetGrid`/`FleetRows` into all three density components — `DueMaintenanceHat` renders next to the printer name on all three; `PrinterExpandedCard` additionally gets a 👷 "Add maintenance item" button (`QuickAddMaintenanceModal`, pre-fills scope/vendor/model from the printer's own resolved profile via `resolveVendorModelForProfile`, guards against clobbering an in-progress edit with a `userEditedRef`) and a conditional "Maintenance due" card listing due rows with per-item "Acknowledge" buttons (`completeMaintenanceItem`, then `refetchMaintenance()`). |
| `PrintersScreen` | `PrinterAddForm` (4-step wizard: Type → Connect → **Profile** → Review; step 3 uses `MachinePicker`, sets `current_orca_printer_profile` on create). `EditForm` (exported; make/model picker via `MachinePicker` + per-slot filament-profile `<select>` from `GET /printers/{id}/profiles` + optional Spoolman spool `<select>` writing `spoolman_spool_id`). Not a top-level route. |
| `FilesScreen`, `SettingsScreen` | Model library; settings sub-nav. `SettingsScreen`'s internal `PageId` union (`type PageId = 'tags'\|'print'\|'maintenance'\|'spoolman'\|'spoolman-mappings'\|'webhook'\|'notifications'\|'fleet-backup'\|'api-keys'\|'about'`) is the authoritative page list — `pageFromPath` derives the active page from `/settings/:page`. `MaintenancePage` lists items grouped General/Model-specific, with a "Suggested items" row from `GET /maintenance/templates`; model-specific scope options come from `useFleetVendorModels()` (fleet-scoped), not the raw OrcaSlicer catalog. `ApiKeysPage` ("Security" nav section): table of keys (name/prefix/scope pills/status), "Create key" modal (name + scope checkboxes grouped by resource, from `apiKeys.ts`'s `SCOPES`), one-time raw-key reveal dialog on create (cleared from state on close, never re-fetchable), Revoke (soft, primary) / Delete (hard, secondary, confirm-guarded, only on already-revoked keys). `WebhookPage`/`NotificationsPage` ("Integrations" section, additive to each other — see `data-model.md`'s `webhook_config`/`notification_config`): each has its own save-button-with-"Saved"-pill flow and per-event checkboxes; `NotificationsPage` additionally has a per-channel "Send test" button and loads a channel's saved (possibly empty) events list as-is — see `frontend-review.md` §3 before "fixing" an empty selection. `SpoolmanMappingsPage` (shown only when Spoolman is enabled): per-printer-model `orca_profiles` mapping. `FleetBackupPage`: download/import via `GET/POST /settings/fleet-{backup,import}`. All of these plus Maintenance/API Keys also appear in `Sidebar.tsx`'s separate `settingsSubItems` list (a hand-maintained duplicate of this screen's own nav — see `frontend-review.md` §1 — keep both in sync when adding a settings sub-page). |

## Shared components (`src/components/*.tsx`)

| File | Exports / purpose |
|---|---|
| `ui.tsx` | `Card`, `StatusPill`, `Progress`, `SectionHeader`, and other primitives. Tokens + classes from `app.css`. See `styling.md`. |
| `PerPrinterConfig.tsx` | `PerPrinterConfig` (component), `PerPrinterCfg` (interface), `defaultPerPrinterCfg()` (all-null/defer defaults). Per-printer job config panel: print-profile `<select>` + filament/tool control. **Multi-tool (≥2 loaded slots):** Tool `<select>` — first option "Any / default tool" = `toolIndex: null`; picking a slot sets `toolIndex + filamentProfile/Type`. **Single-tool (<2 slots):** "Use loaded filament" (default, `toolIndex: null`) vs "Require specific filament" toggle that reveals Spoolman catalog or manual type+color inputs. **Multi-material mapping** (when `modelFilaments` prop has >1 entry): renders a per-model-filament → tool mapping list; each row maps a declared model filament (index + color/type) to a printer tool `<select>`; result stored as `filamentMap: {model_filament, tool_index, catalog_filament_id?}[]` on `PerPrinterCfg`. Used by `NewJobScreen` and `EditJobScreen` (local copies deleted from both). |
| `SlotSpoolPicker.tsx` | Per-slot spool/filament config widget used in Fleet and Printers edit forms. Renders a unified dropdown: choose from Spoolman spools (grouped by filament) or enter custom type/color/name. Writes `{spoolman_spool_id, filament_id, name, type, color, filament_profile}` back to the parent slot. Shows weight remaining for Spoolman entries. Replaces the previous ad-hoc slot input fields. |
| `MachinePicker.tsx` | Cascading make → model → nozzle picker that resolves an OrcaSlicer machine preset. Used in add-printer wizard (step 3) and edit-printer modal. |
| `MaintenanceItemForm.tsx` | `MaintenanceItemForm` (component), `TriggerRow`, `ItemDraft` (interface), `emptyDraft()`, `draftFromTemplate()`, `TRIGGER_LABEL`, `triggerChipText()`. Name + general/model-specific scope radio + repeatable trigger-row editor (calendar/job_time/job_count). `catalog` prop is `FleetVendorModel[]` (fleet-scoped, not the raw OrcaSlicer catalog) — shows an empty-state hint when there are none. Shared between `SettingsScreen`'s `MaintenancePage` and `FleetScreen`'s `QuickAddMaintenanceModal`. |
| `DueMaintenanceHat.tsx` | `DueMaintenanceHat({dueItemNames: string[]})` — renders `null` when empty, else a 👷 span with `title` (desktop hover) + click-to-toggle popover (mobile tap); click handler calls `stopPropagation()` since it's always placed inside an already-clickable printer card. Replaced the older `MaintenanceDueBadge` (deleted) on Fleet cards. |

## API clients & hooks (`src/api/*.ts`)

Typed fetch wrappers + React hooks. Each file's local `request<T>(url, init?)` throws on non-ok and
calls `apiFetch` (not raw `fetch`) internally — see **Auth** above. Mutations use
`{ method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(...) }`.

| File | Exports |
|---|---|
| `queue.ts` | Types `ApiJob` (incl. `low_stock_warning: LowStockWarning \| null`), `ApiJobDetails`, `ApiJobPrinterConfig` (`tool_index: number \| null`; `filament_map?: {model_filament:number, tool_index:number}[] \| null`; `low_stock_warning: LowStockWarning \| null` — per-config, independent of `ApiJob`'s own copy), `ApiSliceFailure`, `ApiPlate`, `ModelFilament` (`index:number, color:string, type:string`), `LowStockWarning` (`{spool_id, spool_label, remaining_g, needed_g, message}`). `PrinterConfigInput` includes `filament_map?`. `useQueue()` (list + `/ws` `job_update`/`queue_update` merge). `useFilePlates(ids)` (cached plate metadata). `createJob`, `cancelJob`, `unblockJob`, `updateJobConfigs`, `getJobDetails`, `getSliceFailures`, `reorderQueue`, `uploadFile`, `getPrinterProfiles`, `getModelFilaments(fileId)` (→ `GET /files/{id}/model-filaments`), `checkOverrides`, `plateThumbnailUrl`. |
| `orders.ts` | `ApiOrder`(status:`StatusKey`, progress 0..1), `ApiOrderDetail`(+jobs), `ApiOrderPart`(+filament_id/color). `useOrders()` (refetch on `/ws` job/queue events). `getOrders/getOrder/createOrder/updateOrder/deleteOrder`. |
| `projects.ts` | Types `Project` (incl. `customer/order_type/on_hold/due_date` — see `data-model.md`), `ProjectItem`, `ProjectLink`, `ProjectPart`, `ProjectJob`, `GenerateOut`. `useProjects()`. Full CRUD for the project itself + its three child collections (items/links/parts), `generateProject(id, ...)`, `getProjectJobs`. Mirrors `backend/app/api/routes/projects.py` closely — see `backend.md`'s Routes table for the endpoint list. |
| `printers.ts` | `ApiPrinter`, `PrinterType`, `ConnectionField`, `LoadedFilament`(`filament_id`: Bambu AMS code or null; `filament_profile?`; `spoolman_spool_id?`), `MachinePreset`. CRUD, `testConnection`, `fetchPrinterTypes`, `fetchMachineCatalog`, `rescanProfiles`, control fns, `markPlateCleared(id)`. |
| `fleet.ts` | `FleetPrinter` (raw) → `toFleetPrinter` → `Printer` (data/types). `useFleetData()` (poll + `/ws` `printer_state` merge). `mapStatus` does NOT fold `awaiting_plate_clear` into a status — it's a separate field/cue. |
| `spoolman.ts` | Types `ApiFilament`, `ApiSpool`, `SpoolmanConfig`. `useSpoolmanConfig()`, `useFilaments(enabled)`, `useSpools(enabled)`, `filamentDisplayName`, `spoolDisplayName`. `parseOrcaProfiles(filament)` — decodes the double-JSON-encoded `extra.orca_profiles` field into `Record<string, string[]>`. `patchFilamentOrcaProfiles(id, profiles)` — `PATCH /api/v1/spoolman/filaments/{id}`. `getSpoolmanConfig`, `saveSpoolmanConfig`, `testSpoolmanConnection`, `fetchFilaments`, `fetchSpools`. |
| `settings.ts` | `WebhookConfig`, `getWebhookConfig`/`saveWebhookConfig`. `FleetImportReport`, `downloadFleetBackup()` (triggers a browser file save, not a typed return), `importFleetBackup(file)`. |
| `notifications.ts` | `NotificationConfig` (three channels: `ntfy`/`discord`/`email`, each with its own `events: string[]` — empty means *that channel* fires on nothing, see `data-model.md`'s `notification_config`), `getNotificationConfig`/`saveNotificationConfig`, `testNotificationChannel(channel, config)` → `POST /settings/notifications/test` with unsaved in-form values. |
| `orca.ts` | Read path for the cached Laminus profile catalog: `OrcaCatalog`/`OrcaCatalogStatus`, `getOrcaCatalog`, `getOrcaCatalogStatus`, `useOrcaCatalog()`. |
| `laminus.ts` | Write/remap path for catalog drift: `refreshCatalog()`/`rescanCatalog()` → `SyncResponse` (either `SyncOk` or `PendingRemaps` — a discriminated union the caller must branch on), `confirmRemap(syncId, resolutions)`. See `backend.md`'s `laminus.py` route row. |
| `tags.ts` | Tag CRUD + assign/unassign to files. |
| `files.ts` | File library: upload, list (with folder/tag/search filters), tree, rename, move, delete, download URL, thumbnail URL. |
| `maintenance.ts` | Types `MaintenanceTrigger`, `MaintenanceItem`, `MaintenanceTemplate`, `MaintenanceStatusRow`, `FleetVendorModel`. CRUD (`getMaintenanceItems`, `createMaintenanceItem`, `updateMaintenanceItem`, `setMaintenanceTriggers`, `deleteMaintenanceItem`), `getMaintenanceTemplates`, `getMaintenanceStatus`/`useMaintenanceStatus()`, `completeMaintenanceItem(printerId, itemId)`. `useMaintenanceItems()` hook. `resolveVendorModelForProfile(profile, catalog)` / `resolveFleetVendorModels(printers, catalog)` / `useFleetVendorModels()` — matches a printer's `current_orca_printer_profile` against the OrcaSlicer catalog (from `printers.ts`) to scope the maintenance vendor/model picker to what the fleet actually has, deduped + sorted. |
| `apiKeys.ts` | Types `ApiKeyOut`, `ApiKeyCreated extends ApiKeyOut` (+ raw `key`, create-response only). `getApiKeys`, `createApiKey(name, scopes)`, `revokeApiKey(id)`, `deleteApiKey(id)`. Exports `SCOPES` grouped by resource — hand-written mirror of the backend's `app/auth.py` registry (no shared codegen), used to render the scope-checkbox UI. |
| `client.ts` | Not a resource client — shared plumbing. `apiFetch(url, init)` (injects `X-Api-Key`, reports 401s via `setUnauthorizedHandler`), `withKeyParam(url)` (appends `?key=` for header-incapable consumers). See **Auth** above. |

## Conventions (enforced)

- **TS strict + `noUnusedLocals`/`noUnusedParameters`** — import only what you use; unused locals fail the build.
- **Type-check with `npx tsc -b` / `npm run build`, NOT `npx tsc --noEmit`** — the root `tsconfig.json` is references-only, so `--noEmit` is a no-op (checks nothing). See `conventions.md`.
- Shared types in `data/types.ts`; `data/mock.ts` still backs Fleet/Filaments display fields — adding a required field to `Printer` means updating the 3 mock `PRINTERS`.
- `StatusKey` (`data/types.ts`) lists styled statuses. Order statuses are all in it; job statuses can exceed it → cast `as never`/`as StatusKey` at `StatusPill` call sites for job status. The key→pill-tone map is in `components/ui.tsx`; adding a styled status means editing both. See `styling.md`.
- **Styling**: no CSS framework — compose token-driven utility/component classes from `app.css` and the shared `components/ui.tsx` (`Card`/`StatusPill`/`Progress`/…). Full vocabulary + tokens in `styling.md`.
- Live updates: hooks open a `/ws` WebSocket; message `{type, data}`; types `job_update`, `queue_update`, `printer_state`, `plate_clear_required`. Guard async setState after unmount with an `alive` flag (see `useOrders`).
- Tests: Vitest + Testing Library. Stub `fetch` via `vi.stubGlobal`; stub `WebSocket` with a `FakeWS` class when a screen uses a `/ws` hook. Since `api/*.ts` calls go through `apiFetch`, assertions on exact `fetch` call args should expect the injected `X-Api-Key` header (or mock `apiFetch` itself, per-file, where that's less invasive).

## Build/run

`npm run dev` (Vite :5173, proxies `/api`+`/ws`→:8001; `vite.config.ts` has `host:true` + `allowedHosts` for LAN/Tailscale). `npm run build` = `tsc -b && vite build` → `dist/`. `npm test` / `npx vitest run`.

## End-to-end (Playwright)

Suite under `frontend/e2e/`. Config: `frontend/playwright.config.ts` (Chromium, baseURL `:5173`, `webServer: npm run dev` with `reuseExistingServer`).

**Deterministic / no backend:** `e2e/mock-api.ts` exports `mockApi(page, over?)` which route-mocks `**/api/v1/**` with canned data and captures mutating request bodies into `mocks.captured` for payload assertions. Also mocks `/ws`. Seeds `localStorage` with a fake API key via `page.addInitScript` before the app loads (and route-mocks `POST **/api/v1/api-keys` as a fallback in case `AuthGate`'s bootstrap races anyway), so specs never see the auth gate. No backend process, no printers, zero print risk.

Canned data includes: a 4-slot U1 printer + a single-tool printer; a multi-material file with 2 model filaments + 2 plates; Spoolman disabled; list endpoints → `[]`.

| Spec | Covers |
|---|---|
| `smoke.spec.ts` | Fleet screen loads |
| `fleet.spec.ts` | Multi-slot loaded-filament editor |
| `new-job.spec.ts` | Multi-material mapping rows + defer toggle; asserts `createJob` payload carries `filament_map` |
| `edit-job.spec.ts` | Pre-fills a saved `filament_map`; asserts `updateJobConfigs` round-trip |

**Run:** one-time `npx playwright install chromium`, then `npm run test:e2e` (headless) or `npm run test:e2e:ui` (interactive).
