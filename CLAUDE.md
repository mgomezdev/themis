# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
- On a long-lived branch, merge `develop` in whenever it moves (merge, don't rebase a pushed branch).
  PR CI runs on the merge result: `develop` drift turns it red for reasons your diff didn't cause, and a
  merge conflict stops it running at all. Check CI on the PR, not just locally.

## Commands

### Backend
```bash
cd backend
python -m venv .venv && .venv\Scripts\activate  # first time
pip install -e ".[dev]"

# Run dev server (auto-reload)
uvicorn app.main:app --reload --port 8001

# Run all tests (CI runs `pytest -v -ra --cov`, which enforces the coverage floor)
pytest -v

# Run a single test
pytest tests/test_models.py::test_create_printer -v
```

> **Windows venv:** build it from the **python.org** interpreter (`py -0` lists them), not the Microsoft Store Python. The Store build's AppContainer sandbox hides `C:\Program Files` (OrcaSlicer not found) and `--reload` spawns its worker through it, so code changes don't take effect and slicing fails with `[WinError 2]`.

### Frontend
```bash
cd frontend
npm install          # first time
npm run dev          # dev server on :5173, proxies /api to :8001
npm run build        # production build → frontend/dist/ (`tsc -b` is the real type-check)
npx vitest run       # unit tests (CI: `npm run test:cov`, enforces the coverage floor)
npm run test:e2e     # Playwright specs against a mocked API
```

### Docker
```bash
docker build -t themis:dev .
docker compose up            # uses .env for APPDATA
docker compose up --build    # rebuild image first
```

## Architecture

Python (FastAPI) backend + React/Vite/TypeScript frontend, single Docker container. FastAPI serves the built React app as static files in production (`THEMIS_STATIC_DIR=/frontend/dist`); in development, Vite's dev server proxies `/api` to the FastAPI process on port 8001. Per-area detail lives in `docs/agent/` (`backend.md`, `frontend.md`, `printers.md`, `data-model.md`); the invariants below are the ones not obvious from the code.

**Vendor/provider boundaries:** printers are one **plugin per vendor** (`backend/app/plugins/{bambu,elegoo_centauri,snapmaker,mock}/`) behind `AbstractPrinterClient`, resolved by `printer_client_factory` from the printer's `plugin_id`; a disabled/removed plugin makes its printers dormant. Filament inventory is a core capability (`inventory.filament`) reached only through the plugin host (`app/plugins/host.py`); Spoolman is the bundled provider. Slicing (Laminus) sits behind `SlicingProvider` in `backend/app/services/providers/`. Core names no vendor and never imports a vendor client or branches on a plugin id — `tests/test_vendor_extraction_boundary.py`, `tests/test_provider_boundary.py` and `tests/test_no_provider_in_core.py` enforce it. Adding a vendor, inventory provider, or slicing adapter = one package/adapter, nothing else changes. See `docs/printer-interface.md`, `docs/provider-interfaces.md`, `docs/plugins.md`.

**Queue engine:** single asyncio background task (`queue_loop`) woken by an `asyncio.Event`. A printer is eligible for a new job only when `is_idle == True` AND `awaiting_plate_clear == False`. Slicing runs in a `ThreadPoolExecutor` so it never blocks the event loop.

**Ready-for-work gate:** `awaiting_plate_clear` is set `True` the moment a job *starts printing* (not just on completion), so a printer never auto-claims the next job onto an uncleared plate even if a completion event is missed. The user clears it via `POST /printers/{id}/plate-cleared` (the Fleet "Ready for new work" button — also the REST hook for a QR code / home-automation trigger).

**Slicing failure recovery:** a slice failure sets the row's `job_printer_configs.slice_failed` and the job `blocked` (never `failed`); a later cycle can rescue it on another eligible printer whose config isn't `slice_failed`, and if none remain it stays blocked until unblocked. `failed` is terminal, set only for an upload/start error after slicing. `POST /jobs/{id}/unblock` clears `slice_failed` so it actually re-slices.

**Cancel ↔ stop:** cancelling a running job stops its printer; stopping a printer reconciles (cancels) the job it was running — the two are linked so neither side gets stuck.

**OrcaSlicer profiles:** in Docker, `/root/.config/OrcaSlicer` is bind-mounted read-only from the host. For local dev `app.config` resolves the config dir and executable per-platform, so no env vars are needed; `ORCA_CONFIG_DIR` / `ORCA_EXECUTABLE` override. Presets resolve through the Laminus sidecar's catalog (`catalog_service`); compatibility is the preset's `compatible_printers` against the printer's `current_orca_printer_profile` (`SlicingProvider.compatible_presets`).

### Database
SQLite (WAL mode) via async SQLAlchemy 2.0 + aiosqlite; schema in `docs/agent/data-model.md`. A job links to at most one order via `jobs.order_id`. Flyway-style migrations live in `backend/app/migrations/`; `runner.py` applies them in order on startup. To add one: create `vNNN_<name>.py` with `version`, `name`, `up(conn)` (and optionally `down(conn)`), then import and register it in `runner.py`.

### Volumes (Docker)
- `/data` — SQLite file + uploaded 3MF files + sliced gcode cache
- `/root/.config/OrcaSlicer` — bind-mounted read-only from `%APPDATA%\OrcaSlicer` on Windows host (set `APPDATA` in `.env`)
- Static frontend files served from `THEMIS_STATIC_DIR` (default `/frontend/dist` in container)

## Testing

Recipes live in `docs/agent/conventions.md` (§ Tests) and the two review checklists; the rules that have already bitten this project:

- **Every behavior change gets a test that fails without it.** A test you can't make fail by breaking the
  code (mutate the line and re-run) isn't a test. Assert state (re-read the row / response body), not just
  a status code or "didn't raise".
- **Run a real check before reporting done:** the tests, type-check, or build that exercises your change
  (see Commands). Install missing dependencies with the project's package manager; if a check can't run
  here, say which one and why instead of reporting the change complete.
- **Test DB:** the shared `session_factory` (`backend/tests/conftest.py`) is a per-test SQLite *file* with
  the app's production pragmas (foreign keys ON, a connection per session). Never use `sqlite+aiosqlite:///:memory:`
  (one shared connection, no FKs). Seed parent rows (`create_printer`, `create_job`, `upload_3mf`, …) —
  orphan rows fail. Wait with `tests/waiting.py:wait_until`, never `sleep`.
- **Contracts:** `contracts/response-keys.json` lists the response keys the frontend reads; the backend
  (`tests/api/test_response_contracts.py`) and frontend (`src/api/responseKeys.contract.test.ts`) both check
  it, and `src/api/contract.test.ts` checks every API URL against `openapi.json`. Update the JSON when you
  rename or add a consumed field; regenerate `openapi.json` (`python scripts/export_openapi.py`, from the repo root) when routes
  or params change (CI diffs it).
- **Coverage floors are ratchets** (`fail_under` in `backend/pyproject.toml`, thresholds in
  `frontend/vitest.config.ts`): raise them when coverage grows, never lower one to make a change pass.
- **Hardware-dependent protocols:** implement against the *documented* protocol, drive the gate/action tests with a
  virtual printer (`backend/tests/virtual_printers/`), and add a manual check in `backend/protocol_verification/`
  that asserts each assumption the virtual printer encodes against a real device (read-only by default, writes
  opt-in via env, never part of the gates — see its README). `tests/test_verification_suite_against_virtual_printers.py`
  runs those checks against the virtual printers so the suite and the fakes can't drift.
- **Frontend tests:** `src/test/fetchStub.ts` (`stubFetch` + `Reply`) for API-level tests; don't fake
  `setInterval`/`setTimeout` around Testing Library `waitFor` (it hangs) — spy on them instead; flush
  effects (`await act(async () => {})`) before firing window key events.

## Development workflow

Plan and implement directly in one session — no backend/frontend overseer split, no subagent per task, even
for cross-cutting work. For non-trivial changes: design → document → implement → review → commit.

- **Design + document**: `brainstorming` then `writing-plans`, plan saved to `docs/superpowers/plans/`; use `themis-planning` to scope against `docs/agent/` first.
- **Implement**: execute the plan in the main session, running the real test/build commands (see Commands) before handoff.
- **Review**: exactly one fresh, non-fork subagent, one pass. Hand over by reference, not paste:
  base/head SHA, the plan file path, and `docs/agent/backend-review.md` / `docs/agent/frontend-review.md`
  as applicable. It also reviews the tests — a weak or unfalsifiable test is a defect, so a test-only diff still gets a review.
- **Commit**: main session, after addressing whatever the reviewer flags.

**PR gate:** a `PreToolUse` hook (`.claude/hooks/gate-pr-review.js`, wired in `.claude/settings.json`)
blocks `gh pr create` and `mcp__github__create_pull_request` unless `.claude/review-state.json` (gitignored)
records `{"sha": "<current HEAD>", "verdict": "clean", "checks": "pass"}` (`checks`: the Commands-section
suites ran green at that sha; `"n/a"` only when the diff touches nothing they cover). If the marker matches
`HEAD` with `verdict: "clean"`, skip the reviewer and create the PR; if it is missing, stale, or not clean,
review first. Write the marker yourself once Critical/Important findings are addressed and the suites are
green; any later commit invalidates it. The hook is a forgetting-guard, not a security boundary — raw
`gh api ... pulls` calls bypass it but are still against this policy.

## Review guidelines

Review a change against the checklist(s) for what it touches — `docs/agent/backend-review.md` (backend),
`docs/agent/frontend-review.md` (frontend) — covering this codebase's systemic gotchas (recurring ID
confusions, hand-duplicated contracts with no codegen, the queue loop's blocking-I/O constraint). For a
cross-cutting change, verify a shared API field's key matches byte-for-byte between the backend route that
returns it and the frontend code that reads it. `contracts/response-keys.json` catches drift only in the
top-level keys it lists, not nested or unlisted ones, so still grep both sides.

## Spec & Plans
- Design spec: `docs/superpowers/specs/2026-05-20-themis-print-farm-manager-design.md`
- Implementation plans: `docs/superpowers/plans/`
