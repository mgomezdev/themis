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

> **Windows venv gotcha:** create the venv from the **python.org** interpreter, not the Microsoft Store Python. The Store build runs in an AppContainer sandbox that hides `C:\Program Files` (so OrcaSlicer isn't found) and redirects the bytecode cache, and `--reload` spawns a worker through it — which manifests as "code changes don't take effect" and `[WinError 2]` when slicing. `py -0` lists installed interpreters; build the venv with the python.org one.

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

Python (FastAPI) backend + React/Vite/TypeScript frontend, single Docker container. FastAPI serves the built React app as static files in production (`THEMIS_STATIC_DIR=/frontend/dist`); in development, Vite's dev server proxies `/api` to the FastAPI process on port 8001.

### Key design patterns

**Printer integration:** `AbstractPrinterClient` ABC with capability flags, a plain `dict[str, type]` registry in `printer_client_factory.py`, and a `PrinterManager` singleton. See `docs/printer-interface.md` for the full pattern. Adding a vendor = add one class + one registry entry, nothing else changes.

**Slicing & inventory providers:** Laminus (slicing) sits behind `SlicingProvider` (sync, runs in the queue thread pool) in `backend/app/services/providers/` — an ABC + capability flags + registry/accessor (`get_slicing_provider()`, `get_format_provider()`), neutral DTOs (`Catalog`/`Preset`) with a `raw` field so routes keep their legacy response shapes. **Filament inventory is a plugin kind** (BIZ-202): the ABC/DTOs are in `backend/app/plugins/kinds/filament_inventory.py`, Spoolman is the bundled plugin `backend/app/plugins/spoolman/`, and core reaches the active provider only through the plugin host (`app/plugins/host.py`: active rule, contained `host.call`) via `app/services/inventory/` and branches on capabilities, never on a plugin id. Core never imports the vendor clients: `tests/test_provider_boundary.py` and `tests/test_no_provider_in_core.py` enforce it. Adding a slicing provider = one adapter + one registry entry; adding an inventory provider = one plugin package under `backend/app/plugins/` (discovered automatically; core names no plugin). Plugins can also be **installed** (upload / public GitHub repo → `<data>/plugins/<id>/<version>/`, admin session only, applied by an admin-triggered restart; `docs/plugins.md`). See `docs/provider-interfaces.md`.

**Queue engine:** Single asyncio background task (`queue_loop`) woken by an `asyncio.Event`. A printer is eligible for a new job only when `is_idle == True` AND `awaiting_plate_clear == False`. Slicing runs in a `ThreadPoolExecutor` to avoid blocking the event loop.

**Ready-for-work gate:** `awaiting_plate_clear` is set `True` the moment a job *starts printing* (not just on completion), so a printer never auto-claims the next job onto an uncleared plate even if a completion event is missed. The user clears it via `POST /printers/{id}/plate-cleared` (the Fleet "Ready for new work" button — also the REST hook for a QR code / home-automation trigger).

**Slicing failure recovery:** each `job_printer_configs` row has a `slice_failed` flag. A slice failure marks that row and sets the job `blocked` (never `failed`); a later cycle can still rescue it on another eligible printer whose config isn't `slice_failed`, and if none remain it stays blocked until someone unblocks it. `failed` is terminal and only set for an upload/start error after slicing. Unblocking a job (`POST /jobs/{id}/unblock`) clears `slice_failed` so it actually re-slices.

**Cancel ↔ stop:** cancelling a running job stops its printer; stopping a printer reconciles (cancels) the job it was running — the two are linked so neither side gets stuck.

**OrcaSlicer profiles:** in Docker, `/root/.config/OrcaSlicer` is bind-mounted read-only from the host. For local dev `app.config` resolves the config dir and executable per-platform (Windows → `%APPDATA%\OrcaSlicer` and `…\Program Files\OrcaSlicer\orca-slicer.exe`), so no env vars are needed; `ORCA_CONFIG_DIR` / `ORCA_EXECUTABLE` still override. Production slicing resolves presets through the Laminus sidecar's catalog (`catalog_service`, cached Themis-side); compatibility is the preset's `compatible_printers` list against the printer's `current_orca_printer_profile` (`SlicingProvider.compatible_presets`). `ProfileIndex`/`PresetResolver` are the dead pre-sidecar local pipeline.

### Database
SQLite (WAL mode) via async SQLAlchemy 2.0 + aiosqlite. Tables: `printers`, `uploaded_files`, `orders`, `jobs`, `job_printer_configs`, `job_model_targets`, `gcode_files`, `sliced_versions`, `queue_config`, `spoolman_config`, `customers`, `admin_account`, `installed_plugins`, `audit_log`. A job links to at most one order via `jobs.order_id`. Versioned Flyway-style migrations live in `backend/app/migrations/` (v001–v039); `runner.py` applies them in order on startup. To add a migration: create `vNNN_<name>.py` with `version`, `name`, `up(conn)` (and optionally `down(conn)`), then import and register it in `runner.py`.

### Volumes (Docker)
- `/data` — SQLite file + uploaded 3MF files + sliced gcode cache
- `/root/.config/OrcaSlicer` — bind-mounted read-only from `%APPDATA%\OrcaSlicer` on Windows host (set `APPDATA` in `.env`)
- Static frontend files served from `THEMIS_STATIC_DIR` (default `/frontend/dist` in container)

## Testing

Details and recipes live in `docs/agent/conventions.md` (§ Tests) and the two review checklists; the rules
that have already bitten this project:

- **Every behavior change gets a test that fails without it.** A test you can't make fail by breaking the
  code (mutate the line and re-run) isn't a test. Assert state (re-read the row / response body), not just
  a status code or "didn't raise".
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
  `frontend/vitest.config.ts`, both ~2 points under measured): raise them when coverage grows, never lower
  one to make a change pass.
- **Hardware-dependent protocols:** implement against the *documented* protocol, drive the gate/action tests with a
  virtual printer (`backend/tests/virtual_printers/`), and add a manual check in `backend/protocol_verification/`
  that asserts each assumption the virtual printer encodes against a real device (read-only by default, writes
  opt-in via env, never part of the gates — see its README). `tests/test_verification_suite_against_virtual_printers.py`
  runs those checks against the virtual printers so the suite and the fakes can't drift.
- **Frontend tests:** `src/test/fetchStub.ts` (`stubFetch` + `Reply`) for API-level tests; don't fake
  `setInterval`/`setTimeout` around Testing Library `waitFor` (it hangs) — spy on them instead; flush
  effects (`await act(async () => {})`) before firing window key events.

## Cross-cutting changes

No backend/frontend overseer-agent split for planning or implementation, even for feature-sized or
cross-cutting requests — plan and implement directly in one session (use `themis-planning` to scope
against `docs/agent/` first for anything non-trivial). The dual-overseer workflow this project used
before 2026-08-31 is retired; its design doc is kept for history at
`docs/superpowers/specs/2026-08-13-dual-overseer-agent-workflow-design.md` but no longer reflects
current practice.

## Development workflow

For non-trivial changes: design → document → implement → review → commit, staying in the main session
for everything except review.

- **Design + document**: `brainstorming` then `writing-plans`, plan saved to `docs/superpowers/plans/`.
  No subagent — this runs on the session's own context, not a cold start.
- **Implement**: execute the plan directly in the main session (`executing-plans` style), not a fresh
  subagent per task. Per-task subagent fan-out (implementer + 2 reviewers × N tasks) and multi-agent
  negotiation are token multipliers this project has already paid for and cut — see the retired
  dual-overseer workflow above. Run the real test/build commands (see Commands) before handoff so review
  isn't spent catching regressions a local run would've caught for free.
- **Review**: exactly one fresh, non-fork subagent, one pass. Handover by reference, not paste:
  base/head SHA, the plan file path, and `docs/agent/backend-review.md` / `docs/agent/frontend-review.md`
  as applicable (see Review guidelines below) — it reads what it needs itself.
- **Commit**: main session, after addressing whatever the reviewer flags.

**Enforcement:** a `PreToolUse` hook (`.claude/hooks/gate-pr-review.js`, wired in `.claude/settings.json`)
blocks `gh pr create` and `mcp__github__create_pull_request` (Bash and PowerShell both covered) unless
`.claude/review-state.json` (gitignored) records `{"sha": "<current HEAD>", "verdict": "clean", "checks": "pass"}`
(`checks`: the Commands-section suites ran green at that sha; `"n/a"` only when the diff touches nothing they cover). This
makes review the default for every PR, not just non-trivial ones — accepted deliberately: a review of a
trivial change is quick by nature, and the gate is what turns "should review" into "hard to skip by
forgetting." It's a forgetting-guard, not a security boundary — the agent it gates is the same one that
writes the marker, and a raw `gh api ... pulls` call isn't mechanically caught (though it's still
against the policy this section describes).

Before dispatching a reviewer, check `.claude/review-state.json` against current `HEAD` yourself — if it
already matches with `verdict: "clean"` and `checks` set, nothing changed since the last review, skip straight
to `gh pr create`. Only spawn a reviewer when the marker is missing, stale (SHA mismatch), or not clean.
Write the marker yourself once Critical/Important findings are addressed and the suites are green; a new
commit after that naturally invalidates it and requires a fresh review, which is correct, not a duplicate.
The reviewer covers the tests too: a test-only diff still gets a review (a weak or unfalsifiable test is a
defect), per the test sections of the two review checklists.

## Review guidelines

Before calling a change done, review it against the domain-specific checklist(s) for whatever it
touches:
- `docs/agent/backend-review.md` — backend changes
- `docs/agent/frontend-review.md` — frontend changes

Both cover systemic gotchas specific to this codebase's shape (recurring ID confusions, hand-duplicated
contracts with no codegen, the queue loop's blocking-I/O constraint), not generic advice — read them
once in full, they're short. For a cross-cutting change, the single highest-value check in both is the
same one: verify a shared API field's key matches byte-for-byte between the backend route that returns
it and the frontend code that reads it — don't trust that a plan or negotiated contract was actually
implemented as agreed. `contracts/response-keys.json` (see Testing) catches drift in the top-level keys
it lists, but not nested keys or anything not listed there, so still grep both sides.

## Spec & Plans
- Design spec: `docs/superpowers/specs/2026-05-20-themis-print-farm-manager-design.md`
- Implementation plans: `docs/superpowers/plans/`
