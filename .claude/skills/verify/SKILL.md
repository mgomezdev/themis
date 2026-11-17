---
name: verify
description: Launch an isolated Themis instance (FastAPI + built React SPA, mock printer, scratch data dir) and drive it with Playwright to prove a backend or UI change works end to end. Use after any change to queue, fleet, files, jobs, or screens, before reporting done.
---

# Verify Themis changes against the real app

Themis = FastAPI backend + React SPA, SQLite. This skill runs a **separate** instance (port 8011, scratch data dir, mock printer) so it never touches the user's dev servers (:8001/:5173, `<repo>/data`). Feature walkthroughs live in [`features/`](features/README.md); read the one for what you changed.

All commands run from the repo root, using Node (any shell):

```
node .claude/skills/verify/scripts/instance.mjs <up|doctor|seed [--job]|down>
node .claude/skills/verify/scripts/drive.mjs <shot <route-no-leading-slash> <name> | plate-clear>
```

## Launch

`instance.mjs up` — builds `frontend/dist` if missing, wipes `.claude/verify-run/data`, starts `uvicorn app.main:app` on **127.0.0.1:8011** from `backend/.venv` with `THEMIS_MOCK_PRINTERS=1`, `THEMIS_DATA_DIR`, `THEMIS_STATIC_DIR=frontend/dist`, `THEMIS_LOCAL_NETWORKS=127.0.0.0/8,::1/128`, then seeds one configured mock printer ("Verify Mock") and uploads `verify.gcode`. Ready = `/api/v1/health` answers and migrations ran from empty. Override the port with `VERIFY_PORT`.

- The backend serves the **built** SPA. After changing `frontend/src`, run `npm run build` in `frontend/` and restart (`down`, `up`), or the browser shows old code. `doctor` flags a stale dist.
- After changing backend code, `down` then `up` (no `--reload`).
- No Laminus/Orca sidecar is needed: seeded files are pre-sliced `.gcode`, which the queue never slices.
- If `:8011` answers but state.json has no live pid, it isn't ours — pick another `VERIFY_PORT`, don't kill it.

## Doctor

`instance.mjs doctor` — read-only. Checks pid alive, health, git_sha vs HEAD, SPA served, keyless loopback admin, a configured printer exists, dist newer than last `frontend/src` commit. Run it first whenever anything looks off; exit code 1 on any FAIL.

## Drive

- **API** (fast, exact state): `curl http://127.0.0.1:8011/api/v1/...` with no key (loopback is admin). Routes: `openapi.json` at repo root. Reads: `/queue`, `/fleet`, `/printers`, `/files`, `/jobs/{id}`.
- **Seed a running job**: `instance.mjs seed --job` creates a job on the mock printer; the queue claims it within ~5 s (`status: printing`, printer `awaiting_plate_clear: true`). Needs `confirm_unknown_eligibility: true` (the script sets it).
- **Browser**: `drive.mjs shot queue name` screenshots any route; `drive.mjs plate-clear` is a full journey (Fleet "Ready for new work" click). For a new journey, copy `drive.mjs`'s shape: Playwright `chromium` from `frontend/node_modules`, role/text locators (the app has few `data-testid`s), assert by re-reading the API afterwards.
- Auth is irrelevant on loopback. To test the sign-in path, drop `THEMIS_LOCAL_NETWORKS` from the launch env in `instance.mjs`.

## Evidence

Written to `.claude/verify-run/evidence/` (gitignored): screenshots plus `journey.log` (timestamped PASS/FAIL lines with API state before/after). `FAILURE.png` is saved on a failed journey. Proof standards:

- Exercise the real user path (UI click or the public API route), not DB edits or test-only endpoints.
- Capture the action **and** the resulting state; re-read it from the API or DB, don't trust the UI alone.
- Check side effects too (queue status, printer flags, rows), and `backend.log` for tracebacks.
- Console errors are logged by `drive.mjs`; a lone `404 (Not Found)` resource error appeared on a clean run (not yet traced) — don't treat it as a regression without checking the network tab.
- The mock printer is a production boundary (`app/plugins/mock`); real vendor protocols are verified separately (`backend/protocol_verification/`).

## Cleanup

`instance.mjs down` — kills only the pid recorded in state.json (never by name), removes the scratch data dir and state; **keeps** `evidence/` and `backend.log`. Run it after every attempt, including failed ones. Confirm with `netstat -ano | findstr :8011` (Windows) that nothing listens.

Do not use `/themis-start` for verification: it kills whatever holds :8001 and opens the user's dev servers.

## Helpers

- `scripts/instance.mjs` — lifecycle + seed + doctor (usage above).
- `scripts/drive.mjs` — `shot`, `plate-clear` (usage above). Needs `up` first; reads `.claude/verify-run/state.json` for the port.
- Keep the map honest with `/maintain-verification-skill`.
