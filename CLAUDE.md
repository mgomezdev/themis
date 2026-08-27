# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Communication style
When reporting information, be extremely concise and sacrifice grammar for the sake of concision.

## Git workflow

Simplified Gitflow: `main` (releases only) + `develop` (integration) + `feature/*` (per-change).

- Branch new work off `develop`, not `main`: `feature/biz-NN-description` (keep the tracker ID from
  the linked issue when there is one).
- PR feature branches back into `develop`. Delete the feature branch once merged — don't leave merged
  branches lying around.
- `develop` merges to `main` only to cut a release. No `release/*` or `hotfix/*` branches — this repo
  doesn't carry enough concurrent in-flight work to justify them; revisit if that changes.
- Before deleting any branch, confirm it's actually merged (`git merge-base --is-ancestor <branch>
  develop` or `...main`) — never force-delete an unmerged branch without the user confirming first.

## Commands

### Backend
```bash
cd backend
python -m venv .venv && .venv\Scripts\activate  # first time
pip install -e ".[dev]"

# Run dev server (auto-reload)
uvicorn app.main:app --reload --port 8001

# Run all tests
pytest -v

# Run a single test
pytest tests/test_models.py::test_create_printer -v
```

> **Windows venv gotcha:** create the venv from the **python.org** interpreter, not the Microsoft Store Python. The Store build runs in an AppContainer sandbox that hides `C:\Program Files` (so OrcaSlicer isn't found) and redirects the bytecode cache, and `--reload` spawns a worker through it — which manifests as "code changes don't take effect" and `[WinError 2]` when slicing. `py -0` lists installed interpreters; build the venv with the python.org one.

### Frontend
```bash
cd frontend
npm install          # first time
npm run dev          # dev server on :5173, proxies /api to :8001
npm run build        # production build → frontend/dist/
```

### Docker
```bash
docker build -t themis:dev .
docker compose up            # uses .env for APPDATA
docker compose up --build    # rebuild image first
```

## Architecture

Python (FastAPI) backend + React/Vite/TypeScript frontend, single Docker container. FastAPI serves the built React app as static files in production (`THEMIS_STATIC_DIR=/frontend/dist`); in development, Vite's dev server proxies `/api` to the FastAPI process on port 8001.

### Key design patterns

**Printer integration:** `AbstractPrinterClient` ABC with capability flags, a plain `dict[str, type]` registry in `printer_client_factory.py`, and a `PrinterManager` singleton. See `docs/printer-interface.md` for the full pattern. Adding a vendor = add one class + one registry entry, nothing else changes.

**Queue engine:** Single asyncio background task (`queue_loop`) woken by an `asyncio.Event`. A printer is eligible for a new job only when `is_idle == True` AND `awaiting_plate_clear == False`. Slicing runs in a `ThreadPoolExecutor` to avoid blocking the event loop.

**Ready-for-work gate:** `awaiting_plate_clear` is set `True` the moment a job *starts printing* (not just on completion), so a printer never auto-claims the next job onto an uncleared plate even if a completion event is missed. The user clears it via `POST /printers/{id}/plate-cleared` (the Fleet "Ready for new work" button — also the REST hook for a QR code / home-automation trigger).

**Slicing failure recovery:** each `job_printer_configs` row has a `slice_failed` flag. On failure, the row is marked and the job requeues if any eligible printers remain; transitions to `failed` only when all configs are exhausted. Unblocking a job (`POST /jobs/{id}/unblock`) clears `slice_failed` so it actually re-slices.

**Cancel ↔ stop:** cancelling a running job stops its printer; stopping a printer reconciles (cancels) the job it was running — the two are linked so neither side gets stuck.

**OrcaSlicer profiles:** in Docker, `/root/.config/OrcaSlicer` is bind-mounted read-only from the host. For local dev `app.config` resolves the config dir and executable per-platform (Windows → `%APPDATA%\OrcaSlicer` and `…\Program Files\OrcaSlicer\orca-slicer.exe`), so no env vars are needed; `ORCA_CONFIG_DIR` / `ORCA_EXECUTABLE` still override. `ProfileIndex` resolves preset inheritance and filters by `compatible_printers` against the printer's `current_orca_printer_profile`.

### Database
SQLite (WAL mode) via async SQLAlchemy 2.0 + aiosqlite. Tables: `printers`, `uploaded_files`, `orders`, `jobs`, `job_printer_configs`, `gcode_files`, `queue_config`, `spoolman_config`. A job links to at most one order via `jobs.order_id`. Versioned Flyway-style migrations live in `backend/app/migrations/` (v001–v009); `runner.py` applies them in order on startup. To add a migration: create `vNNN_<name>.py` with `version`, `name`, `up(conn)` (and optionally `down(conn)`), then import and register it in `runner.py`.

### Volumes (Docker)
- `/data` — SQLite file + uploaded 3MF files + sliced gcode cache
- `/root/.config/OrcaSlicer` — bind-mounted read-only from `%APPDATA%\OrcaSlicer` on Windows host (set `APPDATA` in `.env`)
- Static frontend files served from `THEMIS_STATIC_DIR` (default `/frontend/dist` in container)

## Dual-overseer workflow for cross-cutting changes

For feature-sized or cross-cutting requests (plausibly touches both `backend/` and `frontend/`, or the
shape is ambiguous), spawn two named agents instead of planning solo:

```
Agent({ subagent_type: "backend-lead", name: "backend-lead", prompt: "<request>. Your counterpart is named ui-lead." })
Agent({ subagent_type: "ui-lead", name: "ui-lead", prompt: "<request>. Your counterpart is named backend-lead." })
```

Spawn both in the same message. They negotiate directly with each other via `SendMessage` (shared
contracts, sequencing, file ownership) and each report back their half of a joint plan, escalating any
single topic that goes past 5 rounds of back-and-forth instead of resolving it themselves. If either
report contains an "Escalated items" section, resolve those with the user before presenting the plan.

Trivial, single-file, obviously single-domain changes skip this entirely — plan and implement them
directly as usual. This is a judgment call each time, not a hard rule.

Present the combined joint plan (both reports) to the user and wait for approval before implementation.
Once approved, tell both agents to proceed — they each dispatch their own worker subagents into one
shared worktree/branch (via `using-git-worktrees`) and serialize their commits with each other.

Full design: `docs/superpowers/specs/2026-08-13-dual-overseer-agent-workflow-design.md`.

## Spec & Plans
- Design spec: `docs/superpowers/specs/2026-05-20-themis-print-farm-manager-design.md`
- Implementation plans: `docs/superpowers/plans/`
