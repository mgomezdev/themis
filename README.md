# 🏛️ Themis — 3D Print Farm Manager

Themis is a self-hosted control plane for a workshop of 3D printers. Upload a model, pick which
printers may run it, and drop it into one shared queue — Themis watches printer availability,
**slices each job with your real native OrcaSlicer presets** for the exact target machine, uploads
the result, and starts the print. Live telemetry and camera stream to the browser; filament
inventory can sync from [Spoolman](https://github.com/Donkie/Spoolman).

It runs as a **single Docker container** (FastAPI + a built React SPA), backed by SQLite — no
external services required.

> 📐 **Agent/LLM architecture reference:** [`docs/agent/`](docs/agent/) — backend, frontend, data model, printer protocols, recipes, and conventions.

> 🤖 **This is also an exercise in full agentic coding with human oversight.** Themis is a real tool
> being built end-to-end this way, as a practical testbed for prompt design, multi-agent workflows, and
> harnessing techniques as they mature — not a toy demo.

---

## ✨ Features

| | |
|---|---|
| **One auto-claiming queue** | A background engine assigns queued jobs to idle, eligible printers — no manual dispatch. |
| **Real native slicing** | Slices with your own printer/process/filament presets via a companion OrcaSlicer sidecar (Laminus) — no GUI, no manually duplicating profiles. |
| **Multi-vendor fleet** | Bambu Lab (MQTT/FTPS), Elegoo Centauri (SDCP/WebSocket), and Snapmaker U1 Extended (Moonraker/Klipper); new vendors = one client class + one registry entry. |
| **Multi-plate & multi-color** | Each plate of a 3MF becomes its own job; AMS / multi-tool colour slots are preserved. |
| **Filament-aware gating** | A job won't start on a printer whose loaded filament (type **and** colour) doesn't match. |
| **Ready-for-work gate** | A printer holds after finishing a job until you mark it ready (clear the plate) — via the Fleet button or a `POST /printers/{id}/plate-cleared` hook (wireable to a QR code / home automation). |
| **Orders & Projects** | Group jobs under a customer/internal order with a parts checklist; Projects add a BOM builder that packs multiple parts/quantities across plates and generates the jobs + order in one step. |
| **Maintenance tracking** | Per-printer or per-model maintenance items on calendar / job-count / job-time triggers; due items surface on the Fleet screen. |
| **Spoolman integration** | Source filament choices from your Spoolman catalog; store per-filament OrcaSlicer profile mappings back to Spoolman; a job bound to a spool gets a low-stock warning if it won't have enough filament left. |
| **Per-color filament assignment** | For multi-material jobs, map each model-filament color to a specific printer tool/slot; stored as `filament_map` and rewritten into the sliceable 3MF before slicing. |
| **Notifications** | Job complete/failed/blocked events fire to a generic signed webhook and/or built-in ntfy, Discord, and email channels. |
| **API-key auth** | Every route requires a scoped API key, managed from Settings; a first-run bootstrap flow mints the first key. |
| **Live camera & telemetry** | MJPEG passthrough or RTSP→MJPEG transcode, plus temps, fans, progress over WebSocket. |
| **Capability-driven UI** | Every control renders from a printer's capability flags — never a hard-coded vendor check. |

---

## 🖥️ The app

| Screen | What it's for |
|---|---|
| **Queue** | The shared job list with active / pending / blocked badges, plate thumbnails, a per-job detail panel (incl. low-stock warnings), and inline block/slice-error surfacing. |
| **New Job** | Upload a model and configure each plate: eligible printers, print profile, filament, and order link. |
| **Job detail** | Full per-job view (file, plate, slicing config per printer); edit settings & re-queue, unblock, or cancel a blocked/failed/queued job. |
| **Orders** | Customer/internal orders with a parts checklist; create, edit, hold, and see the jobs filling each. |
| **Projects** | Build a BOM (parts, quantities, filament requirements, printer eligibility) and reference links, then **Generate** to pack plates and create the jobs + a linked order in one step. Detail view tracks linked-job status and a parts checklist. |
| **History** | Completed / failed / cancelled job history, with an outcome note recorded on failure. |
| **Fleet** | Printer cards with live camera + telemetry; queue-off cue + **Ready for new work** button; loaded-filament + OrcaSlicer filament-profile picker; edit a printer via a make → model → nozzle picker; due-maintenance indicator. |
| **Files** | 3MF/STL model library with folder tree, search, tagging, rename, and download. |
| **Settings** | Workshop defaults, queue check interval, **Rescan profiles**, tag management, Spoolman integration + per-model profile mappings, maintenance items, API keys, notification channels, and a fleet config backup/import. |

---

## 🔁 How a job flows

```
Upload .3mf/.stl ─▶ pick eligible printers + profile + filament ─▶ enqueue
                                                                      │
        ┌─────────────────────────────────────────────────────────────┘
        ▼
Queue engine: a printer goes idle (queue_on) ─▶ is it eligible?
        │                                            │
        │  filament/slice mismatch ─▶ BLOCKED ◀──────┘  (stays in queue, retried)
        ▼
   slice for THIS machine (OrcaSlicer) ─▶ upload ─▶ start print ─▶ printing ─▶ complete
        │
        └─ slice fails ─▶ retry geometry-only ─▶ still fails ─▶ BLOCKED (another printer may rescue)
```

- **Blocked ≠ failed.** A filament/slice issue *blocks* a job (transient — re-evaluated every cycle, so
  loading the right spool unblocks it). Only a post-slice upload/start error *fails* it (terminal).
- **Head-of-line:** if the first eligible job can't run on a printer, that printer waits rather than
  skipping ahead.
- **Plate-clear hold:** once a job starts printing, its printer is flagged not-ready and won't claim
  the next job until you mark it **Ready for new work** — so it can't print onto an uncleared plate.

See [`docs/agent/backend.md`](docs/agent/backend.md) for the full state machine and slicing pipeline.

---

## 🚀 Getting started

### Prerequisites
- **OrcaSlicer** installed, with your printer/process/filament presets configured.
- **A Laminus sidecar** — the separate OrcaSlicer-sidecar service Themis slices through. `docker
  compose up` builds it from a sibling `../laminus` checkout next to this repo. Themis will run
  without it, but every slice fails until `LAMINUS_SIDECAR_URL` points at a live one.
- **Docker** (for the container) *or* Python 3.11+ + Node 18+ (for local dev).

### Docker (production)
```bash
# .env must define APPDATA so your host OrcaSlicer config is bind-mounted in
docker compose up --build
```
Builds and starts two services: `orca` (the Laminus sidecar, from `../laminus`) and `themis` (this
app), wired together via `LAMINUS_SIDECAR_URL` in `docker-compose.yml`. Your `%APPDATA%\OrcaSlicer` is
mounted read-only into both — the sidecar for slicing, `themis` itself for regenerating plate
thumbnails after upload.

### Local development
```bash
# Sidecar (once, from the repo root — needs a sibling ../laminus checkout)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up orca

# Backend (FastAPI on :8001) — set LAMINUS_SIDECAR_URL or slicing will fail
cd backend
python -m venv .venv && .venv\Scripts\activate   # use the python.org interpreter (see note)
pip install -e ".[dev]"
$env:LAMINUS_SIDECAR_URL = "http://localhost:5000"   # PowerShell; export on bash
uvicorn app.main:app --reload --port 8001

# Frontend (Vite on :5173, proxies /api and /ws → :8001)
cd frontend
npm install
npm run dev
```
Open <http://localhost:5173>. (On Windows, `/themis-start` — a Claude Code skill in this repo — does
all three steps and sets `LAMINUS_SIDECAR_URL` for you.)

> On Windows the backend auto-resolves your OrcaSlicer config (`%APPDATA%\OrcaSlicer`) and the
> `orca-slicer.exe` under Program Files for the thumbnail-regen path — no env vars needed there. Set
> `ORCA_CONFIG_DIR` / `ORCA_EXECUTABLE` only to override.
>
> **Build the venv from the python.org Python, not the Microsoft Store Python.** The Store build is
> sandboxed (hides `C:\Program Files`, redirects its bytecode cache) and breaks `--reload` and
> subprocess slicing (`[WinError 2]`). `py -0` lists your interpreters.

---

## 🧱 Architecture at a glance

- **Backend** — one FastAPI process: REST API + WebSocket hub + static SPA host. Three in-process
  subsystems: `PrinterManager` (vendor clients + state), `QueueEngine` (asyncio claim loop), and
  `SlicerService` (delegates the actual slice to a companion sidecar — see below).
- **Slicing** — Themis resolves machine/process/filament preset *names* to UUIDs against a cached
  profile catalog, then hands the whole slice — 3MF assembly, profile resolution, gcode generation —
  to **Laminus**, a separate OrcaSlicer sidecar process reached over HTTP (`LAMINUS_SIDECAR_URL`).
  Themis itself no longer invokes OrcaSlicer for production slicing; it still shells out to it
  directly for one narrower job, regenerating a 3MF's thumbnail after upload.
- **Persistence** — SQLite (WAL) via async SQLAlchemy, 22 tables (printers, jobs, orders, projects,
  maintenance, API keys, webhook/notification config, ...) — see
  [`docs/agent/data-model.md`](docs/agent/data-model.md) for the full schema.
- **Frontend** — React + Vite + TypeScript, React Router. No global store; per-screen hooks fetch on
  mount and merge live WebSocket events.

```
backend/app
├── main.py              # app, lifespan wiring, static host
├── models.py            # SQLAlchemy tables (22 — see docs/agent/data-model.md)
├── auth.py              # SCOPES registry + API-key auth dependency
├── migrations/          # versioned schema migrations (v001…)
├── api/routes/          # api_keys, files, fleet, jobs, laminus, maintenance, orders,
│                         # printers, projects, queue, settings, spoolman, tags
└── services/
    ├── printer_manager.py        queue_engine.py
    ├── abstract_printer_client.py printer_client_factory.py
    ├── bambu_mqtt.py             elegoo_centauri_client.py
    ├── snapmaker_client.py       mock_printer_client.py   # test/E2E fake driver
    ├── slicer_service.py         laminus_sidecar_client.py  # sidecar delegation
    ├── catalog_utils.py          override_inspector.py
    ├── three_mf_parser.py        project_pack_builder.py
    ├── spool_check.py            spoolman_service.py
    ├── webhook_service.py        notification_service.py
    ├── maintenance_service.py    thumbnail_regen.py
    ├── library_scanner.py        camera_proxy.py
    ├── api_key_service.py
    └── snapmaker/                # remap.py, paint_remap.py (AGPL-isolated tool remap)
frontend/src
├── App.tsx              # shell, routes, queue badges
├── screens/             # Queue, NewJob, EditJob, JobDetail, Orders, Projects (×3), History,
│                         # Fleet, Printers, Files, Settings
├── components/          # Sidebar, Topbar, PerPrinterConfig, SlotSpoolPicker, MachinePicker,
│                         # OverridePanel, MaintenanceItemForm, ui, icons, ...
└── api/                 # fleet, printers, queue, orders, projects, files, tags, spoolman,
                          # settings, notifications, maintenance, apiKeys, laminus, orca
                          # (typed clients + hooks)
```

See [`docs/agent/backend.md`](docs/agent/backend.md) and [`docs/agent/frontend.md`](docs/agent/frontend.md)
for what's actually live vs. superseded-but-not-yet-deleted code from the pre-sidecar architecture.

---

## 🛠️ Commands

```bash
# Backend tests
cd backend && pytest -v
pytest tests/services/test_queue_engine.py -q   # a single file

# Frontend
cd frontend && npm run build      # production build → frontend/dist/
npm test                          # Vitest unit tests
npx playwright install chromium   # first time only
npm run test:e2e                  # Playwright E2E (headless, no backend needed)
npm run test:e2e:ui               # interactive

# Docker
docker build -t themis:dev .
docker compose up --build
```

---

## 📚 Documentation

| Doc | Contents |
|---|---|
| [`docs/agent/`](docs/agent/) | **As-built** architecture reference — backend routes/services, frontend screens/hooks, data model, printer protocols, recipes, conventions, and a backend/frontend review checklist. LLM-facing; load before making changes. |
| [`docs/slicing-flow.md`](docs/slicing-flow.md) | Sequence diagrams for the queue-engine → sidecar slicing flow, including error paths. |
| [`docs/printer-interface.md`](docs/printer-interface.md) | The `AbstractPrinterClient` / capability / factory pattern (narrative version). |
| [`docs/elegoo-centauri-client.md`](docs/elegoo-centauri-client.md) | SDCP protocol notes for the Elegoo Centauri client. |
| [`docs/superpowers/specs/`](docs/superpowers/specs/) | Original design specs, including the pre-sidecar architecture (historical). |
| [`CLAUDE.md`](CLAUDE.md) | Repo conventions & quick command reference. |

---

## ⚙️ Configuration

| Variable | Default | Purpose |
|---|---|---|
| `THEMIS_DATA_DIR` | `/data` | SQLite DB, uploads, gcode cache |
| `THEMIS_LIBRARY_DIR` | `<data dir>/library` | Uploaded-model library root, if you want it split from `THEMIS_DATA_DIR` |
| `LAMINUS_SIDECAR_URL` | *unset* | Base URL of the Laminus OrcaSlicer sidecar. **Required** — every slice raises `SliceError` without it |
| `ORCA_CONFIG_DIR` | platform-aware¹ | OrcaSlicer preset directory — only read for thumbnail regeneration now; slicing itself goes through the sidecar |
| `ORCA_EXECUTABLE` | platform-aware¹ | OrcaSlicer CLI path — same, thumbnail regeneration only |
| `FFMPEG_EXECUTABLE` | `ffmpeg` | RTSP→MJPEG camera transcode |
| `THEMIS_STATIC_DIR` | `../frontend/dist` | Built SPA assets (production) |
| `THEMIS_BOOTSTRAP_KEY` | *unset* | Optional fixed full-scope API key, always accepted — recovery path if you lock yourself out of the auth system |

¹ Defaults to the Docker/Linux paths (`/root/.config/OrcaSlicer`, `orcaslicer`); on Windows local dev,
resolves `%APPDATA%\OrcaSlicer` and `…\Program Files\OrcaSlicer\orca-slicer.exe` automatically.
