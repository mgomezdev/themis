# Settings → Plugins

## Sub-features
- Plugin list with enable/disable; per-plugin pages (`/plugins/:id/:tab`) with settings and connection test.
- Install a plugin package; disabled plugin → its printers go dormant.

## How to get to it (user POV)
Sidebar → **Settings** → **Plugins** (`/settings/plugins`); plugin tabs at `/plugins/<id>`.

## Driving it with the API + Playwright
```
node .claude/skills/verify/scripts/instance.mjs up
curl -s http://127.0.0.1:8011/api/v1/plugins
node .claude/skills/verify/scripts/drive.mjs shot settings/plugins plugins
```
Pass = the `mock` plugin is listed enabled (because of `THEMIS_MOCK_PRINTERS=1`) and the screenshot renders the list. Dormant check: disable `mock` via the UI/API, then `GET /fleet` shows the printer `dormant: true` and the queue never claims onto it.

## Gotchas
- Proven: `GET /plugins` lists `mock` enabled; the page renders (plugins.png). Disable/dormant and install flows were not driven.
- Plugin tests with real install/uninstall write under the scratch `data/plugins/`; `down` wipes it.
- With no Laminus sidecar the footer dot is red and Laminus slicing is unavailable; the plugin still shows Active.
