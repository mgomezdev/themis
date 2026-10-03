# Conventions & Gotchas

Non-obvious invariants and dev-environment traps. **Skim before editing or running anything.**

## Invariants (don't violate these)

- **blocked vs failed**: `blocked` is *transient* — the queue re-evaluates it every cycle (filament
  mismatch, or a slice failure: `_handle_slice_failure` marks that printer's config `slice_failed` and blocks
  the job, even when every config has failed — it then waits for an unblock). `failed` is *terminal* — set
  only by an upload/start error after slicing (`_fail_job_post_slice`), never by a slice failure. Never set
  `failed` for a recoverable filament/slice issue.
- **awaiting_plate_clear**: set `True` the moment a print **starts** (`status=printing`), not when it
  finishes. A printer is eligible only when `is_idle AND not awaiting_plate_clear AND queue_on`. Cleared
  only by `POST /printers/{id}/plate-cleared` (the Fleet "Ready for new work" button). Lives in the DB
  row AND the `PrinterManager` set — keep both in sync.
- **Versioned migrations, not `create_all` alone**: `init_db()` runs `Base.metadata.create_all` *then*
  `runner.py`'s Flyway-style migration runner (`backend/app/migrations/v00N_*.py`, applied in order,
  tracked in a `schema_migrations` table). A new table can ride on `create_all`, but a new column on an
  *existing* table needs its own migration file (see `data-model.md` § Migrations) — there is no
  `_migrate()` guard function; that pattern was retired.
- **Pre-sliced gcode jobs (BIZ-188)**: a job whose `UploadedFile` is a `.gcode` (`library_scanner.is_gcode_file`)
  is never sliced. The claim skips the Laminus health check; `_run_slice_and_print` stages a *copy* into
  `<data>/gcode/<job_id>/` (`_stage_gcode`) and goes straight to upload+print — finished jobs delete their
  `GcodeFile` path, so it must never be the library's own file. `print_profile` is `""`, overrides are dropped on
  create/PATCH, the estimate is parsed from the header at create (no background test-slice) and `verify-slice` 422s.
  A missing library file blocks (not fails) the job. Themis can't verify gcode against a printer: the UI warns
  (`GcodeWarning`, dismissal remembered in localStorage) and the user owns the match — a job still needs explicit
  printers or a make/model target (BIZ-187).
- **filament_profile vs filament ask**: `job_printer_configs.filament_type/color` is the *ask* (matched
  for eligibility). The OrcaSlicer filament *preset* used for slicing comes from the matched
  `printer.loaded_filaments` slot's `filament_profile` (the config's own `filament_profile` is a legacy
  fallback). Don't conflate them.
- **Per-printer flags are read from the client, not StartPrintOptions**: vendor `start_print` reads
  `self._bed_leveling` etc. `StartPrintOptions` carries only `plate_id/gcode_path/ams_mapping` reliably.
- **Cancel ↔ stop are bidirectional**: cancelling an active job stops the printer; stopping a printer
  reconciles its running job → `cancelled`. Keep both directions wired when touching either.
- **Head-of-line queue**: a job that can't run blocks; the engine does **not** skip to a runnable job
  behind it. Intentional (predictable order). Change only in `_try_claim_for_printer` with intent.
- **Auth is mandatory, not opt-in**: every `/api/v1/*` route (new or existing) must carry
  `Depends(require_scope("<scope>"))`, and the scope must exist in the hardcoded `SCOPES` registry in
  `app/auth.py` — there's no auto-derivation, forgetting either half means an unprotected route or a
  crash on an unknown scope. There is **no** bootstrap hatch any more: an empty `api_keys` table is not
  open access. Don't hand-roll an exception. The one deliberate exception is
  `app/api/routes/public.py`'s `GET /api/v1/public/projects/{token}` — addressed by an unguessable
  per-project token instead of a scope, by design; it's the sole route in that file and the file exists
  specifically to keep that exception isolated and auditable. `app/api/routes/session.py`
  (`POST /auth/login`, `GET /auth/me`, `POST /auth/recover`, `POST /auth/recover/confirm`) is also
  unauthenticated by design — login/recovery is how a browser gets a key. **Admin account**: one
  `admin_account` row, created on first boot with no password; `POST /auth/login` with username
  `admin` mints an admin session (all scopes except `customer`). **Local mode**: requests whose socket
  peer IP is in `THEMIS_LOCAL_NETWORKS` (comma-separated CIDRs, default `192.168.0.0/16`; add
  `100.64.0.0/10` for Tailscale) are full admin with no key **while `admin_account.allow_local_login`**
  (Settings → Admin account; can only be turned off once a password is set) —
  `auth.local_admin_allowed` → `local_admin_key()`. A valid presented key always wins, so a customer
  signed in on the LAN stays a customer (HTTP and `/ws` alike). `/api/v1/admin-account` is admin-only
  (local admin, `THEMIS_BOOTSTRAP_KEY`, or an admin session — not a scoped staff key). Admin password
  ≥ 8 chars; failed `/auth/login` + `/auth/recover/confirm` are throttled per client IP
  (`services/login_throttle.py`, 10 per 15 min, in-memory). **Offline recovery**:
  `docker compose exec themis python -m app.admin reset-password` (prints to the terminal) /
  `allow-local-login`, or `/auth/recover` which writes a single-use 15-min code to the log (read with
  `docker compose logs themis`; never over HTTP; a live code is never replaced; 5 wrong guesses burn
  it). Old full-access API keys (e.g. pre-upgrade "Browser" keys) keep working when local sign-in is
  turned off — the Admin account page counts and warns about them. Only the peer IP is trusted, never proxy headers — a
  reverse proxy or Docker NAT gateway (e.g. Docker Desktop's `192.168.65.x`) whose own IP is in range
  makes every request local; check `request.client.host` in the deployed container before relying on it. **Customer sessions**
  are `api_keys` rows with `customer_id` set and scopes `["customer"]`; portal routes use
  `require_customer` and must filter by that `customer_id`. Frontend: every `api/*.ts` call goes
  through `apiFetch`/`withKeyParam` (`api/client.ts`), never raw `fetch`, or it silently 401s once a key
  exists — except the public share page (`SharedProjectScreen`), which deliberately uses a plain
  `fetch()` since it has no API key and must not touch the authenticated client's 401/403 handlers.

## Dev-environment traps

- **Use python.org Python for the venv, NOT the Microsoft Store build.** The Store Python runs in an
  AppContainer sandbox that hides `Program Files` (OrcaSlicer exe → `[WinError 2]`), redirects the pyc
  cache, and breaks `uvicorn --reload` (the reload worker is spawned through the sandbox). Rebuild
  `.venv` from `C:\Users\<you>\AppData\Local\Programs\Python\Python313\python.exe` if slicing/reload act
  haunted.
- **`npx tsc --noEmit` checks NOTHING here.** The root `frontend/tsconfig.json` is references-only; a
  no-emit run on it type-checks zero files. Always use `npm run build` or `npx tsc -b`.
- **Config is platform-aware.** `config.py` resolves `%APPDATA%\OrcaSlicer` + the Program Files
  `orca-slicer.exe` on Windows, Linux defaults otherwise. Override via `THEMIS_DATA_DIR`/
  `ORCA_CONFIG_DIR`/`ORCA_EXECUTABLE`/`FFMPEG_EXECUTABLE`.
- **Stale routes / 405s** usually mean the dev server didn't reload (often the Store-Python issue above)
  — restart uvicorn before debugging the route.
- **OrcaSlicer config dir is bind-mounted read-only** in Docker (`%APPDATA%\OrcaSlicer`). Don't write to
  it; `ProfileIndex` only reads + watches mtime.

## Running things

```
# Backend (from backend/, python.org venv active)
uvicorn app.main:app --reload --port 8001
pytest -v                       # all (CI: `pytest -v -ra --cov`, fails under `[tool.coverage.report] fail_under` in pyproject.toml)
pytest tests/services/test_bambu_mqtt.py -v

# Frontend (from frontend/)
npm run dev                     # :5173, proxies /api + /ws → :8001
npm run build                   # tsc -b && vite build  (this is the real type-check)
npx vitest run                  # tests (CI: `npm run test:cov`, thresholds in vitest.config.ts)
npx playwright test             # e2e specs (mocked API; `e2e/mock-api.ts`)
```

## Style conventions

- `HTTPException(404, "message")` — **positional** detail (matches existing routes).
- Backend route module: Pydantic `*Create`/`*Patch` + `_to_dict` serializer + `_get_or_404` +
  `Depends(get_session)`.
- Frontend: TS strict + `noUnusedLocals`/`noUnusedParameters` — unused imports fail the build. Cast job
  status to `StatusKey`/`as never` at `StatusPill` sites (job statuses exceed the styled `StatusKey`
  set). Guard post-await `setState` with an `alive`/unmount flag in hooks.
- Route order: Starlette matches in declaration order, so a literal path (`/items/reorder`) must be declared
  before its `/{param}` sibling (`/items/{item_id}`) or the param route swallows it (was a real bug). A list query
  param on a GET needs `Query()`; `tests/test_openapi_contract.py` fails any GET/HEAD/DELETE with a request body.
- Tests: pytest-asyncio with the `client` fixture; the shared `session_factory` is a per-test SQLite **file** with
  the app's connect pragmas (FKs on, separate connection per session) — never `:memory:` (one shared connection,
  no FKs). Factories in `tests/conftest.py` (`create_printer`, `create_job`, `upload_3mf`, `make_3mf`; `make_3mf_bytes`
  uses fixed zip timestamps so content hashes are stable); `tests/waiting.py` `wait_until` instead of `sleep`.
  Frontend: Vitest + Testing Library with `src/test/fetchStub.ts` (or `vi.stubGlobal('fetch', …)`) and a `FakeWS` stub.
  A response field the FE reads goes in `contracts/response-keys.json` (checked by both suites).

## Git

Commit only when asked; branch off `develop`, not `main` (see root `CLAUDE.md` § Git workflow);
`Co-Authored-By` trailer. Update the relevant `docs/agent/*` doc in the same change (or run the
`themis-docs-sync` skill) so the reference doesn't drift.
