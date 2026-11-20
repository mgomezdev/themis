# Contributing to Themis

This repository contains a FastAPI backend, a React/TypeScript browser app, a separate MCP server package, bundled plugins, and a generated OpenAPI contract. Source code and generated contracts define behavior; design docs and dated plans are historical unless explicitly identified as current references.

## Development setup

Use Python 3.11 for the backend and Node.js 20 for the frontend. Slicing and project generation need a reachable Laminus sidecar; other app features can be developed without one.

```powershell
# Terminal 1: backend
cd backend
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8001
```

```powershell
# Terminal 2: frontend
cd frontend
npm ci
npm run dev
```

Vite runs at `http://localhost:5173` and proxies `/api` and `/ws` to the backend at `:8001`. To run Laminus locally, use the sibling `../laminus` checkout and the root Compose dev overlay described in [operations](docs/operations.md).

## Repository map

| Path | Responsibility |
|---|---|
| `backend/app/api/routes/` | Versioned HTTP routes and authorization dependencies. |
| `backend/app/services/` | Queue, printer management, slicing orchestration, inventory, events, cameras, and domain services. |
| `backend/app/plugins/` | Bundled plugin implementations, capability contracts, plugin host, loader, and installer. |
| `backend/app/models.py`, `backend/app/migrations/` | Core SQLAlchemy models and ordered schema migrations. |
| `backend/app/eventing/` | Event registry, envelopes, durable/best-effort hub, and payload definitions. |
| `frontend/src/screens/`, `components/`, `api/` | Routed app screens, shared UI, and typed API clients/hooks. |
| `mcp-server/` | Independently installable MCP server that calls the REST API. |
| `openapi.json`, `contracts/response-keys.json` | Generated API schema and frontend-consumed response-key contract. |
| `docs/` | User guides, operations, integrations, current architecture notes, and historical design records. |

For deeper code maps, see [the architecture index](docs/agent/README.md). The SQLite schema is versioned and migrations run during startup. Add a migration for changes to existing tables; do not rely on model metadata alone to update deployed databases.

## Useful commands

```sh
# Backend (from backend/)
pytest -n auto                    # whole suite, one worker per core
pytest tests/services/test_queue_engine.py -q

# Frontend (from frontend/)
npm run build       # TypeScript project check and production build
npm run test:run
npm run test:e2e

# MCP server (from mcp-server/)
pip install -e ".[dev]"
pytest

# Regenerate API schema (from repository root)
python scripts/export_openapi.py
```

Run the checks affected by a change before reporting it complete. Hardware protocol verification under `backend/protocol_verification/` is a separate manual suite and is not part of the ordinary backend tests; its README explains its read/write safeguards.

### Real-backend browser acceptance

`frontend/e2e-real` runs Chromium against the production frontend served by FastAPI, with a fresh temporary SQLite,
library and artifact directory on each run. It does not intercept HTTP routes. Install the backend dependencies in a
virtual environment first, then from `frontend/` run:

```sh
npm ci
npx playwright install chromium
npm run build
THEMIS_TEST_PYTHON=/absolute/path/to/backend-venv/bin/python npm run test:e2e:real
```

If `python` already has the backend dependencies, omit `THEMIS_TEST_PYTHON`. The harness owns loopback port 18071
and refuses to reuse another server. It overrides deployment paths and uses a fixture-only bootstrap key. On normal
shutdown it deletes its temporary data. No configured production DB, external inventory, slicer or printer is used.
All plugins boot disabled; the spec enables the bundled mock printer and sets inventory providers through the real API.
The Spoolman starting state uses an unreachable loopback URL, then the browser switches to Local inventory.
Printer dispatch is disabled so the test can inspect matching without starting work.

The existing `frontend-e2e` CI job runs both the route-mocked specs and this real-backend project. BIZ-307 covers
provider switching, material/spool create/edit/archive/read, persisted weight/location, Fleet spool binding,
matching/mismatching filament tuples and removal of disabled plugin navigation/direct-route content.

## Change guidance

- Keep printer-specific protocol code inside printer plugins. Core services should depend on the printer client and capability contracts, not a vendor implementation.
- Keep optional feature access capability-based. For filament inventory, use the neutral provider interface and handle unavailable capability results rather than importing a concrete provider into core code.
- Keep blocking slicing/provider calls off the asyncio event loop. Queue behavior and retry semantics are described in [backend architecture](docs/agent/backend.md) and [slicing flow](docs/slicing-flow.md).
- Every API route needs an explicit scope or a documented intentional public/session exception. Use the scope registry and existing auth dependencies.
- Update `openapi.json` when the API contract changes and `contracts/response-keys.json` when a backend response field consumed by the frontend changes.
- Update current guides or architecture references when behavior changes. Dated specs and plans under `docs/superpowers/` record past decisions; do not treat them as promises about the current implementation.

## Local verification

Backend tests use a per-test SQLite file and fixtures in `backend/tests/conftest.py`. Frontend tests use Vitest and Testing Library; E2E tests use Playwright with the mocked API in `frontend/e2e/mock-api.ts`. Protocol verification requires a real device and is opt-in. Do not commit generated local state, credentials, or database files.
