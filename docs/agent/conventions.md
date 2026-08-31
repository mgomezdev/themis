# Conventions & Gotchas

Non-obvious invariants and dev-environment traps. **Skim before editing or running anything.**

## Invariants (don't violate these)

- **blocked vs failed**: `blocked` is *transient* — the queue re-evaluates it every cycle (filament
  mismatch or a `slice_failed` config). `failed` is *terminal* — set only when slicing failed on **all**
  eligible printer configs, or an upload/start error post-slice. Never set `failed` for a recoverable
  filament/slice issue.
- **awaiting_plate_clear**: set `True` the moment a print **starts** (`status=printing`), not when it
  finishes. A printer is eligible only when `is_idle AND not awaiting_plate_clear AND queue_on`. Cleared
  only by `POST /printers/{id}/plate-cleared` (the Fleet "Ready for new work" button). Lives in the DB
  row AND the `PrinterManager` set — keep both in sync.
- **Versioned migrations, not `create_all` alone**: `init_db()` runs `Base.metadata.create_all` *then*
  `runner.py`'s Flyway-style migration runner (`backend/app/migrations/v00N_*.py`, applied in order,
  tracked in a `schema_migrations` table). A new table can ride on `create_all`, but a new column on an
  *existing* table needs its own migration file (see `data-model.md` § Migrations) — there is no
  `_migrate()` guard function; that pattern was retired.
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
  crash on an unknown scope. The bootstrap hatch (open access while `api_keys` is empty) is the only
  built-in exception; don't hand-roll another one. Frontend: every `api/*.ts` call goes through
  `apiFetch`/`withKeyParam` (`api/client.ts`), never raw `fetch`, or it silently 401s once a key exists.

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
pytest -v                       # all
pytest tests/services/test_bambu_mqtt.py -v

# Frontend (from frontend/)
npm run dev                     # :5173, proxies /api + /ws → :8001
npm run build                   # tsc -b && vite build  (this is the real type-check)
npx vitest run                  # tests
```

## Style conventions

- `HTTPException(404, "message")` — **positional** detail (matches existing routes).
- Backend route module: Pydantic `*Create`/`*Patch` + `_to_dict` serializer + `_get_or_404` +
  `Depends(get_session)`.
- Frontend: TS strict + `noUnusedLocals`/`noUnusedParameters` — unused imports fail the build. Cast job
  status to `StatusKey`/`as never` at `StatusPill` sites (job statuses exceed the styled `StatusKey`
  set). Guard post-await `setState` with an `alive`/unmount flag in hooks.
- Tests: pytest-asyncio with the `client` fixture (in-memory SQLite) backend; Vitest + Testing Library
  with `vi.stubGlobal('fetch', …)` and a `FakeWS` stub frontend.

## Git

Commit only when asked; branch off `develop`, not `main` (see root `CLAUDE.md` § Git workflow);
`Co-Authored-By` trailer. Update the relevant `docs/agent/*` doc in the same change (or run the
`themis-docs-sync` skill) so the reference doesn't drift.
