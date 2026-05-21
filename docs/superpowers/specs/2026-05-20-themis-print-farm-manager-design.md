# Themis Print Farm Manager — Design Spec

**Date:** 2026-05-20
**Stack:** Python (FastAPI) backend · React + Vite + TypeScript frontend · SQLite · Docker

---

## 1. System Architecture

Single Docker container. FastAPI serves the REST API, WebSocket hub, and the built React app as static files via `StaticFiles`. SQLite (WAL mode) is the database. A single asyncio background task runs the queue engine loop. Slicing jobs are dispatched to a `ThreadPoolExecutor` so they don't block the event loop. OrcaSlicer and ffmpeg are baked into the image.

**Request flow:**
```
Browser → FastAPI (REST/WS) → PrinterManager → AbstractPrinterClient → Printer
                             ↘ QueueEngine (asyncio loop)
                                  └→ SlicerService (ThreadPoolExecutor)
                                       └→ OrcaSlicer subprocess
```

**Real-time updates:** Single WebSocket connection per browser tab. Printer status changes, job state transitions, and queue mutations all push events to clients.

**Mounted volumes:**

| Mount point (container) | Purpose |
|---|---|
| `/data` | SQLite file, uploaded 3MF files, sliced gcode cache |
| `/root/.config/OrcaSlicer` | Bind-mounted from `%APPDATA%\OrcaSlicer` on the Windows host (read-only) |

The profiles bind-mount means printer/filament profiles edited in the host OrcaSlicer installation are immediately available to the containerized slicer. Docker Desktop on Windows handles the WSL2 path translation automatically.

**docker-compose volume entry:**
```yaml
volumes:
  - "${APPDATA}/OrcaSlicer:/root/.config/OrcaSlicer:ro"
  - themis-data:/data
```

---

## 2. Data Model

### `printers`
Registered printers. `printer_type` keys into the factory registry. `connection_config` is a JSON blob of vendor-specific credentials. `awaiting_plate_clear` is a boolean gate that must be explicitly cleared by the user before the printer can accept a new job.

OrcaSlicer preset fields:
- `orca_printer_profiles` — JSON array of OrcaSlicer printer preset name strings configured for this printer (e.g. `["Bambu Lab X1 Carbon 0.4", "Bambu Lab X1 Carbon 0.2"]`)
- `current_orca_printer_profile` — the currently active preset (one of the values in `orca_printer_profiles`); switching this is how the user reflects a nozzle swap without re-adding the printer

`ProfileService` always filters against `current_orca_printer_profile` when returning compatible print and filament profiles for a printer.

### `uploaded_files`
A stored 3MF file. `plates` is a JSON array populated at upload time by `ThreeMFParser`:
```json
[
  { "plate_number": 1, "thumbnail_path": "...", "estimated_time": 3600, "filament_g": 42.1 },
  { "plate_number": 2, "thumbnail_path": "...", "estimated_time": 1800, "filament_g": 21.5 }
]
```

### `projects`
Named container for jobs. Optional — jobs do not require a project.

### `jobs`
One print job corresponding to one plate from one file. Key fields:
- `uploaded_file_id`, `plate_number`
- `project_id` (nullable)
- `assigned_printer_id` (null until claimed)
- `queue_position` (float — allows reorder without updating all rows)
- `status`: `queued | slicing | uploading | printing | paused | complete | failed | cancelled`

**State machine:**
```
queued → slicing → uploading → printing → complete
                                        ↘ paused → printing
           ↘ failed (all printer configs exhausted)
(cancelled reachable from any pre-complete state)
```

### `job_printer_configs`
Per-printer slice settings, one row per eligible printer per job:
- `job_id`, `printer_id`
- `print_profile`, `filament_profile` (exact OrcaSlicer preset name strings)
- `slice_failed` (bool, default false)
- `slice_error` (text, stores OrcaSlicer stderr on failure)

When slicing fails for a printer, `slice_failed` is set to `True` and the error is recorded. The queue engine excludes rows where `slice_failed = True` when finding the next eligible printer for a job.

### `gcode_files`
Tracks sliced output: `(job_id, printer_id, path)`. Deleted after successful upload to the printer.

---

## 3. Printer Integration Layer

Direct port of the GroundsKeeper `AbstractPrinterClient` pattern (see `docs/printer-interface.md`).

**Unchanged from GroundsKeeper:**
- `AbstractPrinterClient` ABC with `PrinterCapabilities`, `StartPrintOptions`, `PrinterFile`, `ConnectionField`
- Factory + registry (`printer_type` string → class, lazy `importlib` import)
- `PrinterManager` singleton with `asyncio.run_coroutine_threadsafe` callback dispatch
- `_STATUS_SERIALIZERS` dict keyed by `printer_type`
- Capability gate pattern on route handlers (422 for unsupported features)
- `_awaiting_plate_clear` gate persisted to DB

**Changes for Themis:**

`PrinterCapabilities` gets one new flag:
```python
camera: bool = False
```

`on_print_complete` callback triggers the queue engine to wake and find the next eligible job for that printer.

`is_idle` (already on ABC) is the readiness signal. The queue engine requires **both** `is_idle == True` AND `awaiting_plate_clear == False` before assigning a job.

**Vendors in scope for v1:** Bambu (MQTT), Moonraker/Klipper (HTTP poll), Elegoo Centauri (SDCP/WebSocket), Snapmaker U1 (extends Moonraker).

**Elegoo camera:** `ElegooCentauriClient.connection_fields()` gains an optional `camera_url` field (MJPEG stream URL). If set, `capabilities.camera = True`; if blank, `False`.

---

## 4. Queue Engine

Single asyncio background task (`queue_loop`) driven by an `asyncio.Event`. Wakes on: a printer becomes ready (idle + plate cleared), or a new job is enqueued.

**Claim logic (each wake):**
1. Load all printers where `is_idle == True` and `awaiting_plate_clear == False`.
2. For each ready printer (printer-ID order as tiebreak), find the lowest `queue_position` job whose `job_printer_configs` has a row for that printer with `slice_failed == False`.
3. Claim via DB transaction: `SELECT ... WHERE status = 'queued'` + immediate `UPDATE status = 'slicing'` + set `assigned_printer_id`. The transaction prevents double-assignment.
4. Dispatch to `SlicerService` in the thread pool.

**After slicing:**
- **Success:** transition to `uploading`, upload gcode via client, call `start_print()`, transition to `printing`.
- **Failure:** set `job_printer_configs.slice_failed = True` + store error. Check if any configs remain with `slice_failed == False`:
  - **Yes:** transition job back to `queued`, wake queue engine (another printer may claim it).
  - **No:** transition job to `failed` (all options exhausted).

On any post-slicing failure (upload or start_print), transition to `failed` and release the printer (`awaiting_plate_clear = False`).

When a job reaches `complete`, set `awaiting_plate_clear = True` for the printer and push a `plate_clear_required` WebSocket event. The user acknowledges via UI, which clears the flag and wakes the queue engine.

---

## 5. Slicing Pipeline

`SlicerService` runs in a `ThreadPoolExecutor` thread. Receives `(job_id, printer_id)`, loads the job's file path, plate number, and `job_printer_configs` row for that printer.

**OrcaSlicer CLI invocation:**
```bash
orcaslicer \
  --export-gcode \
  --plate <plate_number> \
  --printer-profile "<print_profile>" \
  --filament-profile "<filament_profile>" \
  --output /data/gcode/<job_id>/ \
  /data/uploads/<file_uuid>/model.3mf
```

> **Implementation note:** The exact CLI flags must be verified against `orcaslicer --help` during development — the flags above are illustrative. OrcaSlicer's headless mode is documented in its GitHub wiki.

Stdout/stderr are captured. Non-zero exit code → failure, stderr stored as `slice_error` on the config row.

**Profile resolution:** `print_profile` and `filament_profile` are exact preset name strings from OrcaSlicer (matching the bind-mounted profiles directory). The frontend populates these from `GET /api/v1/printers/{printer_id}/profiles`.

`ProfileService` reads the bind-mounted OrcaSlicer config directory and parses process and filament preset JSONs. Each preset has a `compatible_printers` array. The service filters to presets whose `compatible_printers` includes the printer's `current_orca_printer_profile` and returns two lists: `print_profiles` and `filament_profiles`. This means the profile dropdowns in the queue drawer only show presets that OrcaSlicer considers valid for that machine's current nozzle configuration — no manual curation needed.

---

## 6. 3MF Multi-plate Handling

`ThreeMFParser` inspects the uploaded ZIP archive at upload time without invoking OrcaSlicer:
- Extracts plate thumbnails from `Metadata/plate_<n>.png`
- Reads estimated time and filament usage from the metadata XML where present
- Populates the `plates` JSON array on `uploaded_files`

**Enqueue flow:**
1. User uploads `.3mf` → parser runs → plate list + thumbnails returned in upload response.
2. **Multi-plate:** user sees a plate picker (checkboxes with thumbnail + estimated time per plate). Each selected plate becomes a separate job, optionally grouped into the same project.
3. **Single-plate:** one job created automatically, no picker shown.
4. For each job: user selects eligible printers + per-printer `(print_profile, filament_profile)` via the queue drawer UI.

Each plate-job enters the queue independently and can be claimed by different printers in parallel.

---

## 7. Camera Feed

`CameraProxy` service normalizes per-vendor streams into a single MJPEG endpoint per printer:

```
GET /api/v1/printers/{printer_id}/camera
```

| Vendor | Approach |
|---|---|
| Bambu | Decrypt RTSP via ffmpeg (handles Bambu stream cipher), re-encode as MJPEG |
| Moonraker | Proxy existing MJPEG endpoint directly |
| Elegoo Centauri | Proxy MJPEG endpoint from `camera_url` connection field |
| No camera | `capabilities.camera == False`, endpoint returns 404 |

Frontend renders the feed as `<img src="/api/v1/printers/{id}/camera">` — no WebSocket or WebRTC needed. ffmpeg is only spawned when a feed is actively viewed.

---

## 8. API Design

All routes under `/api/v1/`. REST for CRUD and commands; WebSocket at `/ws` for real-time push.

**Key REST routes:**

| Resource | Notable endpoints |
|---|---|
| `GET /printers/types` | Returns `ConnectionField` descriptors per vendor for dynamic add-printer form |
| `POST /printers/{id}/plate-cleared` | Clears `awaiting_plate_clear`, wakes queue engine |
| `POST /files/upload` | Stores 3MF, runs parser, returns plate list |
| `GET /files/{id}/plates` | Returns plate metadata + thumbnail URLs |
| `POST /jobs` | Create job(s) from file + plate selection + printer configs |
| `POST /jobs/{id}/cancel` | Cancel from any pre-complete state |
| `GET /jobs/{id}/slice-failures` | Per-printer slice error details |
| `GET /queue` | Ordered job list |
| `PATCH /queue/reorder` | Update `queue_position` values after drag |
| `GET /printers/{id}/profiles` | Print + filament presets compatible with printer's `current_orca_printer_profile` |
| `PATCH /printers/{id}/active-preset` | Switch `current_orca_printer_profile` (nozzle swap) |
| `GET /orca/printer-presets` | All OrcaSlicer printer preset names from mounted config dir (used when adding/editing a printer) |
| `GET /printers/{id}/camera` | MJPEG stream |

**WebSocket events (server → client):**

| Event | Payload |
|---|---|
| `printer_state` | Normalized status object (same shape as GroundsKeeper) |
| `job_update` | Job ID + new status + assigned printer |
| `queue_update` | Full ordered queue list |
| `plate_clear_required` | Printer ID |

---

## 9. Frontend Architecture

React + Vite + TypeScript. TanStack Query for server state. Zustand for local UI state (drag state, drawer open/close). React Router for navigation. Single WebSocket context dispatches events directly into TanStack Query cache — no separate polling.

**Views:**

- **Dashboard** — printer card grid. Each card: status, current job + progress bar, temperatures, camera feed (`<img>` stream if `capabilities.camera`), pause/resume/cancel controls, prominent "Plate Cleared" button when status is `complete`.
- **Queue** — drag-to-reorder job list. Add-to-queue drawer: file picker → plate selector → printer checkboxes with per-printer profile dropdowns (populated from `/api/v1/printers/{id}/profiles`).
- **Projects** — project list with aggregate job status per project.
- **Printers** — manage registered printers. Add-printer form dynamically rendered from `GET /api/v1/printers/types` — no frontend hardcoding of per-vendor fields. Printer setup includes a multi-select of OrcaSlicer printer presets (from `GET /api/v1/orca/printer-presets`) and designation of the current active preset. Editing a printer exposes a "Switch Active Preset" control for nozzle swaps — a single click updates `current_orca_printer_profile` without touching connection config.
- **Files** — uploaded 3MF library with plate thumbnails.

**Capability discipline:** all controls rendered conditionally from `capabilities` flags on each printer's state object. No `printer_type` string comparisons anywhere in the UI.
