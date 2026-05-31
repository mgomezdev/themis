# Orders functionality — design

_Date: 2026-05-31_

## Summary

The frontend has Orders screens (`OrdersScreen`, `NewOrderScreen`) running entirely on
mock data; the backend has no orders concept (only a bare, vestigial `projects` table
and an unused `jobs.project_id` FK). This work makes orders real: a backend `orders`
table + CRUD API, a one-order-per-job link, derived order status/progress from linked
jobs, and rewired frontend screens.

## Decisions (locked during brainstorming)

1. **Orders group jobs; progress is derived from linked job statuses.** Parts are a
   simple bill-of-materials checklist (name, qty, material, est) — **no per-part
   "printed" tracking** and no per-unit fulfillment accounting.
2. **One order per job.** Reuse/rename the existing `jobs.project_id` FK as `order_id`.
3. **Status is derived, with a manual Hold override.** Progress bar always reflects jobs.
4. **"New order" creates the order record only.** Jobs are linked later via New Job's
   "Fulfills order" picker.
5. **Orders are editable after creation** (customers change their minds) — including
   their parts list.
6. **No "Suggested plates" panel** on New Order (it no longer drives job creation).

## Data model

### New `orders` table

Parts are stored as a JSON column, matching the existing `plates` / `loaded_filaments`
pattern — parts are never independently queried or linked to jobs, so a separate table
would add joins for no benefit.

| column        | type      | notes                                             |
|---------------|-----------|---------------------------------------------------|
| `id`          | int PK    |                                                   |
| `order_type`  | str       | `customer` \| `internal`                          |
| `customer`    | str       | customer name, or internal team/project label     |
| `title`       | str       |                                                   |
| `due_date`    | str\|null | ISO date                                          |
| `notes`       | str\|null |                                                   |
| `on_hold`     | bool      | manual override, default `false`                  |
| `parts`       | JSON      | list of `{id, name, qty, material, est_minutes}`  |
| `created_at`  | str       | ISO                                                |
| `updated_at`  | str       | ISO                                                |

`parts[].id` is a stable client-or-server-generated string so parts can be edited/removed
by identity. `est_minutes` is per-unit estimated print time (minutes).

### `jobs` table change

Rename the unused `project_id` column to **`order_id`** (FK → `orders.id`, nullable).
One order per job.

### Retire `projects`

Delete `models.Project`, `routes/projects.py`, and its router registration in
`main.py`. The table is unused by the frontend and fully superseded by orders.

### Migration note

There is no migration tool (`Base.metadata.create_all` on startup; CLAUDE.md). The new
`orders` table auto-creates, but the `jobs.project_id` → `order_id` rename will **not**
apply to an existing SQLite file. Dev remedy: delete the dev `/data` SQLite DB so it
rebuilds from the new schema. This must be documented in the implementation plan and is
acceptable given the dev-only, disposable database.

## Derived status + progress

Computed on read. The only persisted status field is `on_hold`.

Let `active_jobs` = linked jobs whose status is **not** `cancelled` (cancelled jobs are
ignored for both status and progress, so a cancelled job never drags an order below 100%).

```
if on_hold:                                    status = "hold"
elif active_jobs is empty:                     status = "queued"   # no jobs, or all cancelled
elif all active_jobs are complete:             status = "complete"
else:                                          status = "in_progress"
progress = completed_jobs / len(active_jobs)   # 0 when active_jobs is empty
```

- "Completed" = job status `complete`.
- The mockup's `partial` status collapses into `in_progress`.
- Status `complete` always pairs with `progress == 1.0`, by construction.

## Backend API (`routes/orders.py`, replaces `routes/projects.py`)

| method & path                | behavior                                                              |
|------------------------------|----------------------------------------------------------------------|
| `GET /api/v1/orders`         | list; each item carries derived `status`, `progress`, `job_count`    |
| `POST /api/v1/orders`        | create (order_type, customer, title, due_date, notes, parts)         |
| `GET /api/v1/orders/{id}`    | order + parts + **linked job summaries**                             |
| `PATCH /api/v1/orders/{id}`  | edit any field, toggle `on_hold`, replace parts list                 |
| `DELETE /api/v1/orders/{id}` | delete order; linked jobs have `order_id` set to null                |

Linked job summary (in `GET /{id}`): `{id, status, plate_number, uploaded_file_id,
queue_position}`. The frontend resolves plate names/est via the existing `useFilePlates`
hook rather than the backend re-deriving plate metadata.

`DELETE` nulls `order_id` on any linked jobs (does not delete or cancel jobs).

### Touch points in existing routes

- `jobs.py`: `JobCreate.project_id` → `order_id`; validate the order exists when provided;
  set `job.order_id`; `_to_dict` emits `order_id`.
- `queue.py`: `_to_dict` emits `order_id` instead of `project_id`.

## Frontend

### `api/orders.ts` (new)

- Types: `ApiOrder`, `ApiOrderPart`, `OrderJobSummary`, create/update payloads.
- Hooks/functions: `useOrders()`, `getOrder(id)`, `createOrder(body)`,
  `updateOrder(id, patch)`, `deleteOrder(id)`.
- `useOrders()` refetches on WebSocket `job_update` messages (reuse the `/ws` connection
  pattern from `api/queue.ts`) so order progress stays live as jobs complete.

### `OrdersScreen.tsx` (rewrite to real data)

- Drop `mock` imports.
- Accordion list + filters (Open / All / Customer / Internal) stay.
- Parts BoM table keeps **qty / material / est** columns; **removes** the
  printed/status/remaining-per-part columns (no per-part tracking).
- Order-level derived **progress bar** replaces per-part progress.
- "Jobs filling this order" lists the real linked jobs (status pill + plate name + est,
  via `useFilePlates`).
- Per-order actions: **Hold/Unhold** toggle (`PATCH on_hold`), **Edit** (→ edit form),
  **Delete**.

### `NewOrderScreen.tsx` (rewrite; doubles as edit form)

- Submit real `createOrder` (order + parts). Primary button label: **"Create order"**.
- Reused for editing via route `/orders/:id/edit`: preloads the order, calls
  `updateOrder`, button label **"Save changes"**.
- **Remove the "Suggested plates" panel** entirely. Right rail keeps a compact summary
  (unique parts, total units, total est time) + the create/save and cancel actions.

### `NewJobScreen.tsx`

- `OrdersPicker` fetches real orders and becomes **single-select** (one order per job).
- `createJob` sends `order_id`. The inline "New order" chip routes to `/orders/new`.

### `App.tsx`

- Sidebar `ordersOpen` badge counts real open orders (status ≠ `complete`) from
  `useOrders()` instead of mock `ORDERS`.
- Add the `/orders/:id/edit` route.

## Testing

- **Backend:** orders CRUD; derived status/progress across job-state combinations
  (no jobs, mixed, all complete, on_hold); job→order link on create; `DELETE` nulls
  `order_id` on linked jobs.
- **Frontend:** OrdersScreen renders real orders with derived progress and linked jobs;
  NewOrderScreen create + edit flows; NewJobScreen single-select order link. Mirror the
  existing `*.test.tsx` patterns and mocked-fetch style.

## Out of scope

- Per-part / per-unit fulfillment tracking.
- Auto-creating or queueing jobs from an order's parts.
- Many-to-many job↔order linkage.
