# Recipes — Step-by-Step Cookbooks

Concrete edit sequences for the recurring change shapes. Each lists every file that must change.
Verify symbols against current code before relying on them ("code wins").

## Add a printer vendor

1. `backend/app/services/<vendor>_client.py` — subclass `AbstractPrinterClient`. Implement the
   abstract methods; override `connection_fields()` (form + ctor kwargs), `get_capabilities()`,
   `is_idle`, `orca_export_args()` (raw vs `--export-3mf`), and if AMS-like, `get_loaded_filaments()`
   + an `_on_ams_change` attr. Read per-printer flags from `self._*` in `start_print` (options always
   present). Set `printer_type` ClassVar.
2. `printer_client_factory.py` — add to `REGISTRY` (type→dotted class) + the display-name map.
3. `printer_manager.py` — add a `_serialize_<vendor>` and register in `_STATUS_SERIALIZERS`. If AMS,
   nothing else (the `on_ams_change` wiring is generic, gated on the client having the attr).
4. `backend/tests/services/test_<vendor>_client.py` — connect/parse/upload/start_print/connection-fields.
5. No frontend change needed: the add-printer wizard reads `GET /printers/types` (driven by
   `connection_fields()`); the Fleet card reads the normalized serializer dict.

## Add an API route

1. `backend/app/api/routes/<x>.py` — `router = APIRouter(prefix="/api/v1/<x>")`. Pydantic
   `*Create`/`*Patch`, a `_to_dict(row)`, a `_get_or_404`, `Depends(get_session)`. `HTTPException(404,
   "msg")` positional. Every route needs `dependencies=[Depends(require_scope("<x>:<read|write>"))]`
   (import from `...auth`) — add the scope(s) to `SCOPES` in `app/auth.py` if new. See `backend.md`'s
   Auth section.
2. `backend/app/main.py` — `app.include_router(<x>.router)`.
3. `backend/tests/` — use the `client` fixture (httpx + in-memory SQLite; already sends a full-scope
   `X-Api-Key` by default).
4. Frontend client: add to the matching `frontend/src/api/*.ts` (typed `request<T>` wrapper, calling
   `apiFetch` from `api/client.ts` — never raw `fetch`).

## Add a frontend screen

1. `frontend/src/screens/<X>Screen.tsx`.
2. `frontend/src/App.tsx` — add `<Route>`; add a `screenConfig` entry (title/crumbs/actions); if it's a
   detail route (`:id`), add a path-normalization case; add a Sidebar link if top-level.
3. `frontend/src/api/<x>.ts` — typed client + hook (`apiFetch` on mount via `api/client.ts`, merge `/ws`
   with `?key=` from `withKeyParam` if live).
4. If adding a required field to a shared `data/types.ts` type used by mocks, update `data/mock.ts`.
5. Style with token-driven classes from `app.css` + shared `components/ui.tsx` — no new CSS framework.
   See `styling.md`.
6. Type-check with `npm run build` (`tsc -b`), NOT `tsc --noEmit`.

## Style a component / add a visual element

1. Compose existing utility/component classes (`styling.md` vocabulary) + tokens; prefer the
   `components/ui.tsx` components (`Card`, `StatusPill`, `Progress`, `SectionHeader`, `Empty`, `Kv`…).
2. Only add a new class to `frontend/src/styles/app.css` if nothing fits; reference tokens
   (`var(--bg-2)`, `var(--accent)`, `var(--pad-3)`), never raw hex/px, so density/accent theming works.
3. **New styled status** → add the key to `StatusKey` (`data/types.ts`) AND the tone map in
   `components/ui.tsx` (pick an `ok/warn/err/info/idle/accent` tone), else it renders as a grey pill.
4. **New design token** → define in `:root`; add `[data-density]`/`[data-accent]` overrides if it
   should react to theming.

## Add a table or column

**Corrected 2026-07-29** — the previous version of this recipe was wrong: `database.py::init_db()`
never calls `Base.metadata.create_all()` in production. Every table, new or existing, is created by an
explicit versioned migration file (`backend/app/migrations/v00N_name.py`, applied by
`migrations/runner.py` at startup) — there is no `database._migrate()` function. `create_all` IS used,
but only inside test fixtures (`backend/tests/conftest.py`'s `client` fixture and the various `db`
fixtures in `backend/tests/services/`), which build schema straight from `models.py` and never run
migrations — this is why a model-level constraint (e.g. `UniqueConstraint`) must be declared in
`models.py` itself, not only in the migration's raw SQL, or tests silently run without it.

- **New table**: define the model in `backend/app/models.py` (inherit `Base`), **and** add a migration
  file with `CREATE TABLE IF NOT EXISTS ...` DDL (see `backend/app/migrations/v010_project_parts.py` or
  `v011_maintenance_tracking.py` for reference), registered in `runner.py`'s import + `_MIGRATIONS` list.
- **New column on an existing table**: add to the model **and** add an idempotent guard in the
  migration's `up(conn)` (`PRAGMA table_info` check → `ALTER TABLE … ADD COLUMN`), or existing dev DBs
  won't get it. Update the route's `_to_dict` and the frontend type if it crosses the API.
- **Constraints beyond a bare column** (unique, FK `ondelete`): declare them on the model via
  `__table_args__`/`ForeignKey(..., ondelete=...)` in addition to the migration's SQL — both must agree,
  since the model is what test fixtures actually build from.
- JSON column: store a list/dict; document its shape in `data-model.md`.

## Change queue / print behavior

- **Eligibility** (when a printer may claim): `printer_manager.is_printer_ready` and
  `queue_engine._try_claim_for_printer`. Head-of-line — a mismatch blocks the job, it does not skip ahead.
- **Filament matching / AMS mapping**: `queue_engine._matching_loaded_filament` + the loaded-slot dict
  shape. AMS mapping flows from the matched slot's `ams_tray_id` into `StartPrintOptions.ams_mapping`.
- **Slice→upload→print sequence**: `queue_engine._run_slice_and_print`. Sets `awaiting_plate_clear=True`
  at `status=printing`.
- **Block vs fail**: `_handle_slice_failure` marks `config.slice_failed`, re-blocks while eligible
  printers remain, fails only when exhausted. Unblock = `jobs.unblock_job` (clears `slice_failed` + re-
  queues at top).

## Wire a new live (`/ws`) event

1. Broadcast from the backend hub (the WS manager `main.py` exposes; `printer_manager`/`queue_engine`
   call it). Message shape `{type, data}`.
2. Frontend: handle the new `type` in the relevant hook's WS `onmessage` (`useQueue`/`useOrders`/
   `useFleetData`). Existing types: `job_update`, `queue_update`, `printer_state`, `plate_clear_required`.

## Add a per-printer print option (e.g. a new calibration toggle)

1. Vendor client `connection_fields()` — add the field (sets the form input + ctor kwarg). Coerce in
   `__init__` (`_as_bool` for checkboxes), store on `self._*`.
2. Use it in `start_print` from `self._*`.
3. It persists automatically in `printers.connection_config`; the add/edit-printer form renders it from
   `GET /printers/types`. No DB migration (JSON column).
