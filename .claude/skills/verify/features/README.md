# Feature map

User-facing features and how to prove each against the isolated instance (see [`../SKILL.md`](../SKILL.md)). Derived from routes in `frontend/src/App.tsx` and `docs/agent/`. Only **plate-clear** has a scripted journey; the rest use the API plus `drive.mjs shot <route-no-leading-slash> <name>`.

| Feature | Route | File | Status |
|---|---|---|---|
| Job queue (create → claim → printing) | `/queue`, `/queue/new` | [queue.md](queue.md) | API path + /queue screenshot proven; /queue/new UI not driven |
| Fleet + Ready-for-new-work gate | `/fleet` | [fleet.md](fleet.md) | journey proven (`drive.mjs plate-clear`) |
| Files library + upload | `/files` | [files.md](files.md) | upload via API + page screenshot proven |
| Settings → Plugins | `/settings/plugins` | [plugins.md](plugins.md) | list + page screenshot proven; toggles not driven |

Not mapped yet (add when touched): orders, projects, customers/portal, history, analytics, filament library, camera wall, alarms, printer console.
