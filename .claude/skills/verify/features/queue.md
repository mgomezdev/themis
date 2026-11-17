# Job queue

## Sub-features
- New job (`/queue/new`): pick a file from the library, pick printers or model targets, print profile, add to queue.
- Queue list: status pills (`queued`, `slicing`, `printing`, `blocked`, `failed`, `cancelled`), reorder, cancel, unblock.
- Job detail (`/jobs/:id`) and edit (`/jobs/:id/edit`).

## How to get to it (user POV)
Sidebar → **Job Queue** (`/queue`); "New job" → `/queue/new`.

## Driving it with the API + Playwright
```
node .claude/skills/verify/scripts/instance.mjs up
node .claude/skills/verify/scripts/instance.mjs seed --job
curl -s http://127.0.0.1:8011/api/v1/queue          # job status -> "printing", assigned_printer_id 1
node .claude/skills/verify/scripts/drive.mjs shot queue queue
```
Pass = the job is `printing` on printer 1 within ~5 s and the screenshot shows it. Cancel path: `POST /api/v1/jobs/{id}/cancel`, then re-read `/queue` and `/fleet` (cancelling a running job stops its printer).

## Gotchas
- Raw `POST /jobs` for a `.gcode` returns 422 "no recorded machine eligibility" unless `confirm_unknown_eligibility: true` (the UI shows `GcodeWarning` instead).
- Pre-sliced gcode skips slicing entirely. Anything that exercises slicing (blocked/`slice_failed` recovery, profiles) needs the Laminus sidecar and is **not** coverable here — use the pytest suite (`tests/`) for it.
- The `/queue/new` UI flow was not driven in this run (only the API path); expect to find selectors by role/text (e2e specs in `frontend/e2e/new-job.spec.ts` show working locators against a mocked API).
