# Themis Agent Reference — INDEX

Authoritative, LLM-facing map of this codebase. **Load this first** for any change to Themis,
then load only the subsystem docs the task touches. Goal: plan & implement without re-deriving
architecture from source. Terse by design — every line is a fact or a pointer.

> Keep in sync with code via the `themis-docs-sync` skill. If a doc contradicts the code, the
> **code wins** — fix the doc (and flag it). Verify any symbol/path before relying on it; these
> drift.

## Doc ownership — load what the task touches

| Doc | Owns | Load when |
|---|---|---|
| `backend.md` | FastAPI routes, services, the queue/slice/print flow, WS hub | any backend change |
| `frontend.md` | React screens, `api/` clients + hooks, routing, conventions | any frontend change |
| `styling.md` | Design tokens, the no-framework CSS approach, `ui.tsx` components, status→pill mapping, density/accent theming | any visual/CSS/component-styling change |
| `data-model.md` | SQLite tables, JSON column shapes, relationships, derived fields | schema or persisted-shape change |
| `printers.md` | `AbstractPrinterClient` pattern, Bambu/Elegoo/Snapmaker protocols, AMS, slicing pipeline | printer/vendor/slicing change |
| `recipes.md` | Step-by-step cookbooks (add vendor, route, screen, table, queue behavior) | implementing a known shape |
| `conventions.md` | Non-obvious invariants, dev-env gotchas, testing patterns | always skim before editing/running |

Human-facing (narrative — not the agent source of truth): `README.md`, `CLAUDE.md`,
`docs/printer-interface.md`, `docs/elegoo-centauri-client.md`.

## System in one screen

Single FastAPI process (`backend/app/main.py`) = REST + WebSocket hub + static SPA host. SQLite
(WAL) via async SQLAlchemy 2.0. Three in-process subsystems, all wired in `main.py` `lifespan`:

- **`PrinterManager`** (`services/printer_manager.py`) — singleton; owns vendor client objects,
  their live state, the `awaiting_plate_clear` set, and state→DB/WS fan-out.
- **`QueueEngine`** (`services/queue_engine.py`) — one asyncio loop woken by `asyncio.Event`;
  claims jobs for idle/eligible printers; slices on a `ThreadPoolExecutor`.
- **`SlicerService`** (`services/slicer_service.py`) — drives the real OrcaSlicer CLI headless.

Frontend: React + Vite + TS SPA (`frontend/src`). No global store; per-screen hooks fetch on mount
and merge live `/ws` events. Dev: Vite :5173 proxies `/api`+`/ws` → FastAPI :8001. Prod: FastAPI
serves the built SPA from `THEMIS_STATIC_DIR`.

## The job lifecycle (the spine of the app)

```
upload .3mf/.stl (files route, parse plates)
  → create job(s): plate + eligible printer configs (+ optional order)   [jobs route]
  → enqueue (status=queued, queue_position float)
QueueEngine loop (woken by wake() or interval):
  for each ready printer (is_idle AND not awaiting_plate_clear AND queue_on):
    pick lowest queue_position job whose config row targets this printer
    filament check: job ask (type+color) vs printer.loaded_filaments  → mismatch ⇒ BLOCKED
    config.slice_failed ⇒ BLOCKED (stale failure; cleared by unblock/edit)
    else: status=slicing, assigned_printer_id set
      SlicerService.slice(SliceRequest)  [thread pool]:
        resolve presets → build embedded-config 3MF → run OrcaSlicer → artifact
      status=uploading → client.upload_file(gcode) 
      status=printing → client.start_print(file, opts)   ← sets awaiting_plate_clear=True
  on print complete (vendor callback) → awaiting_plate_clear stays True, broadcast plate_clear_required
user marks ready: POST /printers/{id}/plate-cleared → awaiting_plate_clear=False → wake()
```

States: `queued | slicing | uploading | printing | paused | complete | blocked | failed | cancelled`.
**blocked** = transient (filament/slice), re-evaluated each cycle. **failed** = terminal post-slice
(upload/start error). See `conventions.md` for the blocked-vs-failed and plate-clear invariants.

## Extension points (where new work usually goes)

- New **printer vendor** → one `AbstractPrinterClient` subclass + one registry entry. `printers.md` + `recipes.md`.
- New **API route** → `backend/app/api/routes/<x>.py` + register in `main.py`. `recipes.md`.
- New **screen** → `frontend/src/screens/<X>.tsx` + route in `App.tsx` + `api/<x>.ts` client. `recipes.md`.
- New **table/column** → `models.py` + (for columns on existing tables) `database._migrate`. `data-model.md`.
- New **queue/print behavior** → `queue_engine.py`. `backend.md`.
