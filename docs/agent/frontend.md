# Frontend Reference

React 18 + Vite + TypeScript SPA in `frontend/src`. React Router. **No global store** — each screen
uses hooks that fetch on mount and merge live `/ws` events. Entry: `main.tsx` → `App.tsx`.

> **Styling lives in `styling.md`** — design tokens, the no-framework CSS approach (one global
> `app.css`, no Tailwind), the shared `components/ui.tsx` set, and the `StatusKey`→pill-tone mapping.
> Load it for any visual/CSS work; it's referenced inline below where it matters.

## App shell & routing (`App.tsx`)

`AppShell` renders `Sidebar` + `Topbar` + `<Routes>`. Topbar title/crumbs/actions come from a
`screenConfig` map keyed by a normalized path; detail routes (`/orders/:id/edit`, `/jobs/:id`,
`/jobs/:id/edit`) are special-cased into synthetic keys (`/orders/edit`, `/jobs/detail`, `/jobs/edit`)
in the `path` computation. Sidebar badge counts come from `useQueue()`/`useOrders()`.

Routes: `/queue`, `/queue/new`, `/fleet`, `/orders`, `/orders/new`, `/orders/:id/edit`, `/jobs/:id`,
`/jobs/:id/edit`, `/files`, `/filaments`, `/settings/*`. To add a screen: add the `<Route>`, a
`screenConfig` entry (+ path-normalization case if it's a detail route), and a Sidebar link if top-level.

## Screens (`src/screens/*.tsx`)

| Screen | Purpose / notes |
|---|---|
| `QueueScreen` | Active job list (`useQueue`), filters, per-job side panel: status, block reason inline + slice-error fetch, **Edit settings** / **Unblock** / **Cancel** / **View details** buttons. `DisplayJob` flattens `ApiJob`. |
| `NewJobScreen` | Upload → per-plate config: eligible printers (`PrinterPicker`), `PerPrinterConfig` (print profile from `getPrinterProfiles`, filament via Spoolman catalog OR manual type+color), single-select order picker. Multi-plate → multiple jobs. |
| `EditJobScreen` | `/jobs/:id/edit` — reload configs via `getJobDetails`, re-pick printers + per-printer slicing, `updateJobConfigs` (resets slice_failed, re-queues). |
| `JobDetailScreen` | `/jobs/:id` — full read view: thumbnail, file/plate stats, per-printer slicing config (incl. slice errors), assigned printer; actions: edit / unblock / cancel. |
| `OrdersScreen` | `useOrders` accordion; per-order derived progress bar, parts table (Part/Material/Qty/Est), linked jobs (clickable → `/jobs/:id`), Hold/Edit/Delete. |
| `NewOrderScreen` | Create **and** edit (`/orders/:id/edit` via `useParams`). Parts table with Spoolman-aware filament picker; `createOrder`/`updateOrder`. |
| `FleetScreen` | Printer cards (tile/row/expanded). Live telemetry/camera. Queue-off cue (orange border + badge), **Ready for new work** button (`markPlateCleared`), AMS/loaded filament, edit-printer modal (`EditForm`), `FilamentPicker` (writes `spoolman_spool_id`, sets `filament_id: null`). |
| `PrintersScreen` | `PrinterAddForm` (4-step wizard: Type → Connect → **Profile** → Review; step 3 uses `MachinePicker`, sets `current_orca_printer_profile` on create). `EditForm` (exported; make/model picker via `MachinePicker` + per-slot filament-profile `<select>` from `GET /printers/{id}/profiles` + optional Spoolman spool `<select>` writing `spoolman_spool_id`). Not a top-level route. |
| `FilesScreen`, `FilamentsScreen`, `SettingsScreen` | Model library; filament library; settings sub-nav (general/tags/print-defaults/notifications/data/about/spoolman). |

## API clients & hooks (`src/api/*.ts`)

Typed fetch wrappers + React hooks. Shared `request<T>(url, init?)` throws on non-ok. Mutations use
`{ method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(...) }`.

| File | Exports |
|---|---|
| `queue.ts` | Types `ApiJob`, `ApiJobDetails`, `ApiJobPrinterConfig`, `ApiSliceFailure`, `ApiPlate`. `useQueue()` (list + `/ws` `job_update`/`queue_update` merge). `useFilePlates(ids)` (cached plate metadata). `createJob`, `cancelJob`, `unblockJob`, `updateJobConfigs`, `getJobDetails`, `getSliceFailures`, `reorderQueue`, `uploadFile`, `getPrinterProfiles`, `checkOverrides`, `plateThumbnailUrl`. |
| `orders.ts` | `ApiOrder`(status:`StatusKey`, progress 0..1), `ApiOrderDetail`(+jobs), `ApiOrderPart`(+filament_id/color). `useOrders()` (refetch on `/ws` job/queue events). `getOrders/getOrder/createOrder/updateOrder/deleteOrder`. |
| `printers.ts` | `ApiPrinter`, `PrinterType`, `ConnectionField`, `LoadedFilament`(`filament_id`: Bambu AMS code or null; `filament_profile?`; `spoolman_spool_id?`), `MachinePreset`. CRUD, `testConnection`, `fetchPrinterTypes`, `fetchMachineCatalog`, `rescanProfiles`, control fns, `markPlateCleared(id)`. |
| `fleet.ts` | `FleetPrinter` (raw) → `toFleetPrinter` → `Printer` (data/types). `useFleetData()` (poll + `/ws` `printer_state` merge). `mapStatus` does NOT fold `awaiting_plate_clear` into a status — it's a separate field/cue. |
| `spoolman.ts` | `useSpoolmanConfig`, `useFilaments`, `useSpools`, `filamentDisplayName`. |

## Conventions (enforced)

- **TS strict + `noUnusedLocals`/`noUnusedParameters`** — import only what you use; unused locals fail the build.
- **Type-check with `npx tsc -b` / `npm run build`, NOT `npx tsc --noEmit`** — the root `tsconfig.json` is references-only, so `--noEmit` is a no-op (checks nothing). See `conventions.md`.
- Shared types in `data/types.ts`; `data/mock.ts` still backs Fleet/Filaments display fields — adding a required field to `Printer` means updating the 3 mock `PRINTERS`.
- `StatusKey` (`data/types.ts`) lists styled statuses. Order statuses are all in it; job statuses can exceed it → cast `as never`/`as StatusKey` at `StatusPill` call sites for job status. The key→pill-tone map is in `components/ui.tsx`; adding a styled status means editing both. See `styling.md`.
- **Styling**: no CSS framework — compose token-driven utility/component classes from `app.css` and the shared `components/ui.tsx` (`Card`/`StatusPill`/`Progress`/…). Full vocabulary + tokens in `styling.md`.
- Live updates: hooks open a `/ws` WebSocket; message `{type, data}`; types `job_update`, `queue_update`, `printer_state`, `plate_clear_required`. Guard async setState after unmount with an `alive` flag (see `useOrders`).
- Tests: Vitest + Testing Library. Stub `fetch` via `vi.stubGlobal`; stub `WebSocket` with a `FakeWS` class when a screen uses a `/ws` hook.

## Build/run

`npm run dev` (Vite :5173, proxies `/api`+`/ws`→:8001; `vite.config.ts` has `host:true` + `allowedHosts` for LAN/Tailscale). `npm run build` = `tsc -b && vite build` → `dist/`. `npm test` / `npx vitest run`.
