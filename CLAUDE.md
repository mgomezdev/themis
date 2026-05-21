# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend
```bash
cd backend
python -m venv .venv && .venv\Scripts\activate  # first time
pip install -e ".[dev]"

# Run dev server (auto-reload)
uvicorn app.main:app --reload --port 8000

# Run all tests
pytest -v

# Run a single test
pytest tests/test_models.py::test_create_printer -v
```

### Frontend
```bash
cd frontend
npm install          # first time
npm run dev          # dev server on :5173, proxies /api to :8000
npm run build        # production build → frontend/dist/
```

### Docker
```bash
docker build -t themis:dev .
docker compose up            # uses .env for APPDATA
docker compose up --build    # rebuild image first
```

## Architecture

Python (FastAPI) backend + React/Vite/TypeScript frontend, single Docker container. FastAPI serves the built React app as static files in production (`THEMIS_STATIC_DIR=/frontend/dist`); in development, Vite's dev server proxies `/api` to the FastAPI process on port 8000.

### Key design patterns

**Printer integration:** `AbstractPrinterClient` ABC with capability flags, factory + registry, `PrinterManager` singleton. See `docs/printer-interface.md` for the full pattern (ported from GroundsKeeper). Adding a vendor = add one class + one registry entry, nothing else changes.

**Queue engine:** Single asyncio background task (`queue_loop`) woken by an `asyncio.Event`. A printer is eligible for a new job only when `is_idle == True` AND `awaiting_plate_clear == False`. Slicing runs in a `ThreadPoolExecutor` to avoid blocking the event loop.

**Slicing failure recovery:** each `job_printer_configs` row has a `slice_failed` flag. On failure, the row is marked and the job requeues if any eligible printers remain; transitions to `failed` only when all configs are exhausted.

**OrcaSlicer profiles:** the `/root/.config/OrcaSlicer` directory is bind-mounted read-only from the host. `ProfileService` parses preset JSONs and filters by `compatible_printers` against the printer's `current_orca_printer_profile`.

### Database
SQLite (WAL mode) via async SQLAlchemy 2.0 + aiosqlite. Six tables: `printers`, `uploaded_files`, `projects`, `jobs`, `job_printer_configs`, `gcode_files`. No migration tool — `Base.metadata.create_all` on startup.

### Volumes (Docker)
- `/data` — SQLite file + uploaded 3MF files + sliced gcode cache
- `/root/.config/OrcaSlicer` — bind-mounted read-only from `%APPDATA%\OrcaSlicer` on Windows host (set `APPDATA` in `.env`)
- Static frontend files served from `THEMIS_STATIC_DIR` (default `/frontend/dist` in container)

## Spec & Plans
- Design spec: `docs/superpowers/specs/2026-05-20-themis-print-farm-manager-design.md`
- Implementation plans: `docs/superpowers/plans/`
