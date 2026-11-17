# Files library

## Sub-features
- Upload (`.3mf`, `.gcode`, `.gcode.3mf`), folders, tags, plate thumbnails, rescan.
- File → "new job" hand-off; pre-sliced file handling and eligibility.

## How to get to it (user POV)
Sidebar → **Files** (`/files`); also "Pick from library" inside `/queue/new`.

## Driving it with the API + Playwright
```
node .claude/skills/verify/scripts/instance.mjs up            # uploads verify.gcode
curl -s http://127.0.0.1:8011/api/v1/files
node .claude/skills/verify/scripts/drive.mjs shot files files
```
Pass = response lists `verify.gcode` with `kind: "gcode"`, `plate_count: 1`, and the screenshot shows it in `Job Uploads`. Files land under `.claude/verify-run/data/library/`.

## Gotchas
- Upload is multipart field `file` to `POST /api/v1/files/upload`.
- A `.3mf` needs real archive contents for plates/thumbnails; the seeded `verify.gcode` is 15 bytes of fake gcode (fine for the queue, useless for slicing).
- Proven: API upload lists `verify.gcode` (`kind: gcode`) and `/files` renders (files.png). The folder/tag/rescan UI was not driven.
