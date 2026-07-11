# Themis — Contributor Reference

Themis is the 3D print queue and farm management backend. It manages printer connections, slicing jobs via the Laminus sidecar, project/order tracking, and a file library. All application code is a single FastAPI service serving both the REST API and the React frontend.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11 / FastAPI / uvicorn |
| Database | SQLite via SQLAlchemy async + aiosqlite |
| Frontend | React 19 / TypeScript / Vite |
| Printer protocols | Bambu MQTT/TLS, Elegoo SDCP WebSocket, Snapmaker HTTP |
| Slicer | Laminus sidecar (external service, optional) |
| Container | Single image — uvicorn serves API + static frontend |

---

## Repo Layout

```
themis/
├── backend/
│   ├── pyproject.toml           # Dependencies + pytest config
│   └── app/
│       ├── main.py              # FastAPI app, lifespan, router registration
│       ├── database.py          # Engine, SessionLocal, init_db()
│       ├── models.py            # ALL SQLAlchemy ORM models (one file)
│       ├── config.py            # Path/env resolution helpers
│       ├── api/
│       │   ├── routes/          # One file per resource group
│       │   │   ├── files.py
│       │   │   ├── fleet.py
│       │   │   ├── jobs.py
│       │   │   ├── laminus.py
│       │   │   ├── orders.py
│       │   │   ├── printers.py
│       │   │   ├── projects.py
│       │   │   ├── queue.py
│       │   │   ├── settings.py
│       │   │   ├── spoolman.py
│       │   │   └── tags.py
│       │   └── websocket.py     # ConnectionManager + /ws endpoint
│       ├── migrations/
│       │   ├── runner.py        # Versioned migration runner
│       │   ├── migrate.py       # CLI: python -m app.migrations.migrate up|down
│       │   └── v001_initial.py  # Baseline schema migration
│       └── services/
│           ├── queue_engine.py          # Job state machine + asyncio loop
│           ├── slicer_service.py        # Laminus slice orchestration
│           ├── printer_manager.py       # All printer connection state
│           ├── printer_client_factory.py
│           ├── bambu_mqtt.py
│           ├── elegoo_centauri_client.py
│           ├── snapmaker_client.py
│           ├── laminus_sidecar_client.py
│           ├── library_scanner.py
│           ├── three_mf_parser.py
│           ├── override_inspector.py
│           └── spoolman_service.py
├── frontend/
│   ├── src/
│   │   ├── App.tsx              # BrowserRouter + all Routes + AppShell
│   │   ├── api/                 # Fetch-based API clients (9 modules)
│   │   ├── screens/             # 13 screen components
│   │   └── components/          # Shared UI components
│   └── vite.config.ts
└── docs/
    ├── agent/                   # Supplementary LLM-facing docs
    ├── slicing-flow.md          # Full pipeline Mermaid diagrams
    └── printer-interface.md     # AbstractPrinterClient reference
```

---

## Running Locally

```bash
# Backend (from repo root)
pip install -e "backend[dev]"
cd backend && uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
# Vite dev server on :5173 proxies /api/v1 → :8000

# DB migrations (run once after pulling)
cd backend && python -m app.migrations.migrate up
```

Docker:
```bash
docker compose up
# or from Concordia:
docker compose -f docker-compose.yml -f docker-compose.local.yml up
```

---

## Architecture

### Three core singletons

Three singleton objects are wired together in `app/main.py`'s `lifespan` and passed by reference:

| Singleton | Module | Role |
|---|---|---|
| `printer_manager` | `services/printer_manager.py` | Owns all live printer connections; normalizes state from protocol-specific clients; fans out WebSocket broadcasts |
| `queue_engine` | `services/queue_engine.py` | Asyncio event loop; claims jobs for idle printers; drives the job state machine |
| `slicer_service` | `services/slicer_service.py` | Resolves profile names→UUIDs; posts to Laminus `/api/slice/start`; polls status; downloads gcode |

### Startup sequence (`app/main.py :: lifespan`)

1. `init_db()` — WAL pragma + run pending migrations
2. Seed placeholder Elegoo Centauri Carbon printer (idempotent)
3. `LibraryScanner.scan()` — walk library dir, hash files, sync `uploaded_files` table
4. Wire `printer_manager` (load `awaiting_plate_clear` from DB, connect all enabled printers)
5. Wire `queue_engine` with session factory, printer_manager, slicer_service
6. If `LAMINUS_SIDECAR_URL` set: health-check sidecar, start `warm_catalog_cache()` background task

### Job state machine

```
queued
  ├─ (slicing required) → slicing → uploading → printing → complete
  ├─ (Laminus unreachable) → blocked  ← re-evaluated each queue cycle
  ├─ (filament mismatch) → blocked
  ├─ (slice failed) → failed          ← slice_failed=True, won't auto-retry
  └─ (cancelled)  → cancelled
                       ↑ (user unblock)
                    blocked
```

`blocked` jobs re-enter the queue cycle automatically. `failed` jobs (with `slice_failed=True`) do not retry without user intervention.

### Request → Response flow

```
Browser / API client
  └─ HTTP request
       └─ FastAPI (app/main.py)
            └─ router (app/api/routes/<resource>.py)
                 └─ async route function
                      └─ AsyncSession (via Depends(get_session))
                           └─ SQLAlchemy async queries / service calls
```

WebSocket updates push to all connected clients when queue, fleet, or order state changes.

### Laminus catalog caching

The Laminus profile catalog is large and expensive to fetch. Themis caches it module-level in `app/api/routes/laminus.py`:

```python
_catalog_dict: dict | None = None   # for internal callers
_catalog_bytes: bytes | None = None  # pre-serialised for HTTP responses
```

All internal routes call `get_cached_catalog()` — never fetch from Laminus per-request. To force a refresh: `POST /api/v1/laminus/catalog/refresh`.

---

## Routes Reference

All routes are under `/api/v1`. Route files are in `backend/app/api/routes/`.

### files.py — `/api/v1/files`
- `GET /` — list files (query: folder, tags, search, sort)
- `GET /tree` — folder tree from DB
- `POST /upload` — multipart upload (default folder: `/Job Uploads`)
- `POST /folders` / `DELETE /folders` — create/delete folder
- `PATCH /{file_id}` — rename or move
- `DELETE /{file_id}` — delete (blocks if active job references it)
- `POST /{file_id}/tags` / `DELETE /{file_id}/tags/{tag_id}` — tag management
- `POST /rescan` — re-walk library dir, sync DB
- `GET /{file_id}/plates` — plate metadata
- `GET /{file_id}/model-filaments` — filament slots from 3MF
- `GET /{file_id}/embedded-settings` — OrcaSlicer settings baked in 3MF
- `GET /{file_id}/thumbnails/{filename}` — serve plate PNG thumbnail

### jobs.py — `/api/v1/jobs`
- `POST /` — create job (body: uploaded_file_id, plate_number, printer_configs[], overrides)
- `POST /check-overrides` — diff 3MF embedded settings vs canonical OrcaSlicer presets
- `GET /` — list active queue jobs (ordered by queue_position)
- `GET /history` — completed/cancelled/failed jobs
- `GET /{job_id}/details` — enriched detail with file, plate, printer configs
- `POST /{job_id}/cancel` — cancel (stops printer if active)
- `POST /{job_id}/unblock` — re-queue blocked job at front of queue
- `POST /{job_id}/verify-slice` — test-slice without printing
- `PUT /{job_id}/outcome` — record per-item pass/fail counts

### printers.py — `/api/v1/printers`
- `GET /types` — supported printer types + connection form fields
- `POST /` — create printer and connect
- `GET /{printer_id}/profiles` — compatible print + filament profiles for this printer
- `PATCH /{printer_id}` — update printer settings
- `POST /{printer_id}/plate-cleared` — mark plate clear, wake queue engine
- `POST /{printer_id}/pause` / `resume` / `stop` — print controls
- `POST /{printer_id}/light` / `jog-z` / `fan` / `bed-temp` — hardware controls
- `GET /{printer_id}/camera` — MJPEG stream (multipart/x-mixed-replace)
- `GET /{printer_id}/snapshot` — single JPEG frame

### projects.py — `/api/v1/projects`
- `GET /` / `POST /` / `PATCH /{id}` / `DELETE /{id}` — project CRUD
- `GET /{project_id}/items` / `POST /{project_id}/items` / `PUT /{project_id}/items/{item_id}` / `DELETE` — item management
- `POST /{project_id}/generate` — pack STLs via Laminus, create 3MFs, enqueue print jobs per plate

### laminus.py — `/api/v1/laminus`
- `GET /catalog` — cached full catalog (JSON bytes)
- `GET /catalog/status` — cache state + Laminus live health
- `POST /catalog/refresh` — pull fresh catalog from Laminus
- `POST /catalog/rescan` — tell Laminus to rebuild from disk, then re-fetch (polls up to 120s)

### Other routers
- `fleet.py` — `GET /api/v1/fleet/` — all printers with live normalized state
- `orders.py` — CRUD for customer/internal print orders
- `queue.py` — `GET /api/v1/queue/`, `PATCH /api/v1/queue/reorder`
- `settings.py` — QueueConfig singleton, SpoolmanConfig singleton, fleet backup/import
- `spoolman.py` — filament and spool data from Spoolman
- `tags.py` — file tag CRUD

---

## Data Model

All models live in `backend/app/models.py`. Timestamps are stored as `VARCHAR(32)` ISO strings.

| Model | Table | Notes |
|---|---|---|
| `Printer` | `printers` | connection_config JSON, loaded_filaments JSON, awaiting_plate_clear |
| `UploadedFile` | `uploaded_files` | plates JSON, content_hash for dedup |
| `Job` | `jobs` | queue_position FLOAT for fractional reordering, overrides JSON |
| `JobPrinterConfig` | `job_printer_configs` | per-printer profile/filament assignments |
| `GcodeFile` | `gcode_files` | path to downloaded gcode on disk |
| `QueueConfig` | `queue_config` | singleton (id=1) |
| `SpoolmanConfig` | `spoolman_config` | singleton (id=1) |
| `Project` | `projects` | source_app, source_user, source_layout_id for Ordinus integration |
| `ProjectItem` | `project_items` | quantity, filament_profile_uuid, color_hex |
| `Order` | `orders` | parts JSON, derived status computed at API layer |
| `Tag` / `FileTag` | `tags` / `file_tags` | many-to-many file tagging |

---

## Key Code Paths

### 1. Job lifecycle (queue engine)

```
queue_engine.py :: _run_loop()
  → queries jobs WHERE status IN ('queued', 'blocked') ORDER BY queue_position
  → for each idle printer:
       → pre-flight Laminus health check
         ├─ unreachable → _block_job("Laminus unreachable") → continue
         └─ OK → claim job (status=slicing)
              → slicer_service.slice(job)
                   → resolve profile names → UUIDs (via Laminus catalog)
                   → POST /api/slice/start to Laminus
                   → poll /api/slice/status/{job_id} until complete|failed
                   → GET /api/slice/download/{job_id} → save gcode
              → upload gcode to printer (printer_manager)
              → job status=printing
  → sleeps until Event triggered (plate-clear, job-created, job-cancelled, etc.)
```

### 2. Slicing flow

```
slicer_service.py :: slice(request: SliceRequest)
  → get_cached_catalog() → resolve profile name → UUID
  → POST /api/slice/start to Laminus (uploads STL, passes profile UUIDs)
  → poll GET /api/slice/status/{job_id} (2s interval, up to SLICE_TIMEOUT)
  → GET /api/slice/download/{job_id} → bytes
  → save to <filecache_dir>/<job_id>.gcode
```

### 3. File upload and library scan

```
POST /api/v1/files/upload
  → files.py route handler
  → save multipart file to THEMIS_LIBRARY_DIR/<folder>/
  → library_scanner.py :: scan_single(path)
       → hash file (content_hash for dedup)
       → parse 3MF plates → PlateInfo[]
       → generate plate thumbnails (OrcaSlicer headless if available)
       → insert/update uploaded_files row
```

### 4. Ordinus → Themis project creation

When Ordinus sends a layout to Themis:
- Ordinus uploads each bin STL: `POST /api/v1/files/upload` (folder: `/ordinus/layouts/<layout_id>/`)
- Ordinus creates a project: `POST /api/v1/projects` with `source_app="ordinus"`, `source_layout_id`
- Ordinus adds items: `POST /api/v1/projects/:id/items` per STL

The `project.source_layout_id` and `project.source_app` fields enable bidirectional linking.

### 5. WebSocket broadcast

```
printer_manager.py :: _broadcast(msg)
  → websocket.py :: ConnectionManager.broadcast(msg)
  → pushes to all connected /ws clients

Message types:
  "queue_update"   → full jobs array
  "job_update"     → single job delta
  "printer_state"  → single printer state delta
```

Frontend hooks (`useQueue`, `useFleetData`, `useOrders`) maintain separate `/ws` connections and merge server-pushed updates into local state.

---

## Adding a New Route

1. Create or edit a file in `backend/app/api/routes/`.
2. Define an `APIRouter`:
```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from ...database import get_session

router = APIRouter(prefix="/api/v1/widgets", tags=["widgets"])

@router.get("/")
async def list_widgets(session: AsyncSession = Depends(get_session)):
    ...
```
3. Register in `backend/app/main.py`:
```python
from .api.routes.widgets import router as widgets_router
app.include_router(widgets_router)
```
4. Add frontend API client in `frontend/src/api/widgets.ts`.

---

## Adding a New Printer Type

1. Create `backend/app/services/my_printer_client.py` implementing `AbstractPrinterClient` (see `abstract_printer_client.py` for the full interface and `docs/printer-interface.md` for the protocol).
2. Register in `printer_client_factory.py`:
```python
_REGISTRY = {
    "bambu": "...",
    "elegoo_centauri": "...",
    "my_printer": "app.services.my_printer_client.MyPrinterClient",
}
```
3. Add connection form fields by implementing `get_connection_fields() -> list[ConnectionField]`.

---

## DB Migrations

### How it works

- `schema_migrations` table tracks applied versions (version, name, applied_at)
- Runner in `backend/app/migrations/runner.py` applies pending migrations in version order
- `init_db()` in `database.py` calls `run_migrations(conn)` at every startup
- Each migration module exports `version`, `name`, `up(conn)`, `down(conn)`

### Adding a migration

1. Create `backend/app/migrations/v002_my_change.py`:
```python
from sqlalchemy import text

version = 2
name = "my_change"

async def up(conn) -> None:
    await conn.execute(text("ALTER TABLE jobs ADD COLUMN priority INTEGER DEFAULT 0"))

async def down(conn) -> None:
    # SQLite 3.35+: ALTER TABLE jobs DROP COLUMN priority
    pass
```

2. Register in `backend/app/migrations/runner.py`:
```python
from . import v001_initial, v002_my_change
_MIGRATIONS = sorted([v001_initial, v002_my_change], key=lambda m: m.version)
```

3. Run:
```bash
cd backend && python -m app.migrations.migrate up    # apply pending
cd backend && python -m app.migrations.migrate down  # roll back latest (dev only)
```

Migrations auto-apply at startup via `init_db()` — no manual step needed.

### Rules
- Never edit an applied migration file — add a new one instead
- `up()` must be idempotent where possible (check PRAGMA table_info before ADD COLUMN)
- `down()` exists for dev rollback only; production rollbacks are manual
- The `conn` argument is an `AsyncConnection` from `engine.begin()` — use `await conn.execute(text(...))`

---

## Environment Variables

Resolved in `backend/app/config.py`:

| Variable | Default | Purpose |
|---|---|---|
| `THEMIS_DATA_DIR` | `<repo-root>/data` | Root for DB, library, filecache, gcode |
| `THEMIS_LIBRARY_DIR` | `<data>/library` | Uploaded print files |
| `LAMINUS_SIDECAR_URL` | *(unset)* | Laminus base URL; sidecar disabled if unset |
| `ORCA_CONFIG_DIR` | `%APPDATA%/OrcaSlicer` (Win) | OrcaSlicer profile dir (local dev only) |
| `ORCA_EXECUTABLE` | searches Program Files | OrcaSlicer binary (local dev only) |
| `FFMPEG_EXECUTABLE` | `ffmpeg` | For RTSP camera proxy |

---

## Frontend Notes

### API client pattern

All nine API client files in `frontend/src/api/` use raw `fetch`. Imperative async functions for mutations, `useXxx()` React hooks for data that needs polling or WebSocket integration.

**Important:** `frontend/src/api/orca.ts` calls `/api/v1/orca/...` routes — this is a stale name from before the Laminus rename. The actual backend routes are at `/api/v1/laminus/...`. This mismatch exists and the frontend orca.ts functions are currently broken. When touching profile/catalog code, use `/api/v1/laminus/...` endpoints.

### Screen structure

13 screen components in `frontend/src/screens/`. Each screen fetches its own data via the API hooks — no global frontend store. WebSocket updates (via `useQueue`, `useFleetData`, `useOrders`) trigger re-renders without polling.

### WebSocket

Three independent `/ws` connections are opened (one per hook). All receive the same broadcast messages; each hook filters for its relevant message type. Don't open a fourth `/ws` connection for new features — add a new message type to the existing broadcast in `printer_manager.py` and handle it in the relevant existing hook.

---

## Known Gotchas

- **No auth.** All API routes are unauthenticated. Do not add auth-dependent logic.
- **`orca.ts` naming mismatch.** `api/orca.ts` hits `/api/v1/orca/...` which doesn't exist. Use `/api/v1/laminus/...` when adding profile/catalog features.
- **`awaiting_plate_clear` persisted to DB.** This boolean on each Printer row survives restarts. If a printer is stuck, clear it via `POST /api/v1/printers/{id}/plate-cleared`.
- **Laminus pre-flight.** `queue_engine.py` checks Laminus health before claiming each job. If Laminus is unreachable, jobs are `blocked` (not failed) and will auto-retry.
- **SQLite single-writer.** All write operations go through `engine.begin()` transactions. WAL mode is on. Don't open additional raw connections alongside SQLAlchemy.
- **All timestamps are strings.** The `VARCHAR(32)` ISO string format (e.g. `2026-07-10T14:30:00`) is used throughout. No Python `datetime` objects in the DB layer.
