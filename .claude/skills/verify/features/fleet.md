# Fleet and the Ready-for-new-work gate

## Sub-features
- Printer cards/rows with live state, temps, camera tile.
- **Ready for new work** button: clears `awaiting_plate_clear` (`POST /printers/{id}/plate-cleared`).
- Queue toggle, add printer, resync.

## How to get to it (user POV)
Sidebar → **Fleet** (`/fleet`). A printer that started a job shows a yellow "Ready for new work — Clear the plate, then mark ready" button. Until it is clicked the queue will not give that printer another job.

## Driving it with Playwright (`drive.mjs`)
```
node .claude/skills/verify/scripts/instance.mjs up
node .claude/skills/verify/scripts/instance.mjs seed --job     # wait ~5s: job printing, flag set
node .claude/skills/verify/scripts/drive.mjs plate-clear       # click, then re-read /fleet
```
Pass = `journey.log` shows `awaiting_plate_clear=true` before, `false` after, plus `plate-clear-1-before.png` / `-2-after.png`. API-only equivalent: `curl -X POST http://127.0.0.1:8011/api/v1/printers/1/plate-cleared`.

## Gotchas
- The mock printer's card shows "Idle" even while the job is "printing"; trust `GET /fleet` (`awaiting_plate_clear`) and `GET /queue` (`status`), not the badge.
- A fresh printer is not eligible until `POST /printers/{id}/test` succeeds (`configured=true`); `up` does this.
- With the flag set, a second job stays `queued` for that printer — a good negative check for queue changes.
