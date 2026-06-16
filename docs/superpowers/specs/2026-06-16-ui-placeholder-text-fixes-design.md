# UI Placeholder Text Fixes Design

## Context

A full-UI audit found static text blocks standing in for data that doesn't exist anywhere in the app:

- `frontend/src/components/Sidebar.tsx` footer: hardcoded avatar `"LR"`, name `"Lev Romero"`, and subtitle `"Workshop · 3 printers"` — Themis has no auth/user system at all (confirmed: no `currentUser`/`useAuth`/`/api/v1/user` anywhere), and the printer count is the only hardcoded count in the codebase (every other screen, e.g. `FleetScreen`, computes it live).
- `frontend/src/screens/SettingsScreen.tsx` About page: `Version "0.7.2"`, `Released "2026-05-22"`, `Channel "Stable"` — none wired to anything; `package.json`'s actual version is `"0.0.0"`, so these don't even agree with each other.

Two other candidates (`SettingsScreen.tsx`'s `syncLocation` default of `"Workshop"`, `NewJobScreen.tsx`'s `saveFolder` default of `"/Job Uploads"`) were reviewed and excluded from this design — they're real default values for functional, user-editable fields, not fake displayed data.

## Decisions

Resolved by walking through each finding with the user:

1. **Sidebar name/avatar** → replace with a settings-driven operator/instance display name.
2. **Sidebar printer count** → wire to the live fleet count instead of a hardcoded number.
3. **Sidebar location label** ("Workshop") → drop entirely; no new field for it.
4. **Empty-name fallback** → if no display name has been set yet, hide the identity row entirely (don't show a generic placeholder).
5. **About version/released/channel** → wire `Version` to `package.json`, delete `Released` and `Channel` outright (no release-channel concept exists in this app).
6. **Seed version** → bump `package.json` from `"0.0.0"` to `"0.1.0"`.

## Architecture

### Backend: `operator_name` setting

`QueueConfig` (`backend/app/models.py`) is the existing singleton settings row (currently just `check_interval_minutes`, used for queue-engine timing). It gains one nullable column:

```python
class QueueConfig(Base):
    __tablename__ = "queue_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    check_interval_minutes: Mapped[int] = mapped_column(default=5)
    operator_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
```

`backend/app/database.py`'s idempotent `_migrate()` gets a new guarded block, following the existing pattern (guard on `PRAGMA table_info`, skip if the table doesn't exist yet so a fresh DB just uses `create_all`):

```python
qc_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(queue_config)"))).fetchall()}
if qc_cols and "operator_name" not in qc_cols:
    await conn.execute(text("ALTER TABLE queue_config ADD COLUMN operator_name VARCHAR(120)"))
```

`backend/app/api/routes/settings.py`'s `/api/v1/settings/queue` GET/PUT routes expose it with **partial-update** semantics — the same pattern `SpoolmanConfigIn` already uses (`if body.field is not None: row.field = ...`). This matters because the existing check-interval save (`saveQueueConfig({ check_interval_minutes: v })`) must not silently clobber a previously-set `operator_name`, and a future operator-name save must not reset the interval back to a default.

```python
class QueueConfigOut(BaseModel):
    check_interval_minutes: int
    operator_name: str | None


class QueueConfigIn(BaseModel):
    check_interval_minutes: int | None = None
    operator_name: str | None = None
```

Note: `check_interval_minutes` becomes optional on the `In` model too (it wasn't before), since a future operator-name-only save (`saveQueueConfig({ operator_name: v })`) must not be forced to also resend the interval. `update_queue_config` updates each field only when present, mirroring `update_spoolman_config`:

```python
@router.put("/queue", response_model=QueueConfigOut)
async def update_queue_config(
    body: QueueConfigIn,
    session: AsyncSession = Depends(get_session),
):
    row = await _get_or_create_queue(session)
    if body.check_interval_minutes is not None:
        row.check_interval_minutes = max(1, body.check_interval_minutes)
    if body.operator_name is not None:
        row.operator_name = body.operator_name or None
    await session.commit()
    await session.refresh(row)
    return row
```

An empty string for `operator_name` is stored as `None` (cleared), same as `SpoolmanConfigIn`'s `row.url = body.url or None` pattern. `_get_or_create_queue`'s default-row construction (`QueueConfig(id=1, check_interval_minutes=5)`) is unchanged — `operator_name` defaults to `None` via the column definition.

### Frontend: data flow

`frontend/src/api/queue.ts`'s `QueueConfig` interface gains `operator_name: string | null`. A new `useQueueConfig()` hook is added there, mirroring `useSpoolmanConfig()` in `spoolman.ts` (fetch on mount, `refetch()` bumps a tick state).

`frontend/src/App.tsx`'s `AppShell` already calls `useQueue()` and `useOrders()` to compute `queueCounts`/`ordersOpen` for the Sidebar. It gains two more calls:

```tsx
const { config: queueConfig } = useQueueConfig();
const [printers] = useFleetData();
```

and passes them down:

```tsx
<Sidebar
  queueCounts={queueCounts}
  ordersOpen={ordersOpen}
  operatorName={queueConfig?.operator_name ?? null}
  printerCount={printers.length}
/>
```

`useFleetData()` already exists (`frontend/src/api/fleet.ts`) and is already called independently by `FleetScreen` — calling it a second time here duplicates one fetch + one WebSocket connection while the Fleet screen is open, which is the same duplication pattern `useQueue()` already has between `AppShell` and `QueueScreen`. Consistent with existing precedent, not a new concern.

### Frontend: `Sidebar.tsx`

```tsx
interface SidebarProps {
  queueCounts: QueueCounts;
  ordersOpen: number;
  operatorName: string | null;
  printerCount: number;
}

function initials(name: string): string {
  return name.trim().split(/\s+/).slice(0, 2).map(w => w[0]?.toUpperCase() ?? '').join('');
}
```

Footer block:

```tsx
<div className="footer">
  {operatorName && (
    <div className="user-chip">
      <div className="avatar">{initials(operatorName)}</div>
      <div className="user-meta">
        <div className="name">{operatorName}</div>
      </div>
    </div>
  )}
  <div className="muted small">{printerCount} {printerCount === 1 ? 'printer' : 'printers'}</div>
</div>
```

Note: `.sub` (`frontend/src/styles/app.css:279`) is scoped to `.user-meta .sub`, so it only applies inside the name row. The printer-count line sits outside `.user-meta` (it must render even when there's no name), so it uses the global `.muted`/`.small` utility classes (`app.css:573`, `:575`) instead — the same classes `FleetScreen.tsx` already uses for equivalent secondary text.

The printer-count line always renders (independent of whether a name is set); the name/avatar row only renders when `operatorName` is truthy. The old combined `"Workshop · 3 printers"` `.sub` div is split: the printer count becomes its own always-visible line, "Workshop" is dropped, and the name row's `.sub` class is removed since it no longer carries the count.

### Frontend: Settings — "Display name" field

`SettingsScreen.tsx`'s `PrintDefaultsPage` (the page already backing `check_interval_minutes` via `getQueueConfig`/`saveQueueConfig`) gets one more `FieldRow`, following the exact same fetch-on-mount + commit-on-blur pattern as `checkInterval`:

```tsx
const [operatorName, setOperatorName] = useState<string>('');
const [savingName, setSavingName] = useState(false);
useEffect(() => {
  getQueueConfig().then(c => setOperatorName(c.operator_name ?? '')).catch(console.error);
}, []);
async function commitOperatorName(name: string) {
  setSavingName(true);
  try { await saveQueueConfig({ operator_name: name.trim() || null }); }
  finally { setSavingName(false); }
}
```

```tsx
<FieldRow label="Display name" hint="Shown in the sidebar. Leave blank to hide it.">
  <input className="input" value={operatorName}
         onChange={e => setOperatorName(e.target.value)}
         onBlur={e => commitOperatorName(e.target.value)}
         placeholder="e.g. Workshop Lead" style={{ width: '100%' }} />
  {savingName && <span className="muted small">saving…</span>}
</FieldRow>
```

### Frontend: version injection

`frontend/package.json` version: `"0.0.0"` → `"0.1.0"`.

`frontend/vite.config.ts` reads it and injects a build-time global:

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import pkg from './package.json'

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  server: { /* ...unchanged... */ },
})
```

A new ambient declaration (`frontend/src/vite-env.d.ts` — check if this file already exists from the Vite template; if not, create it) declares the global for TypeScript:

```ts
declare const __APP_VERSION__: string;
```

`SettingsScreen.tsx`'s About page:

```tsx
<div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
  <AboutTile k="Version" v={__APP_VERSION__} mono />
</div>
```

`Released` and `Channel` tiles are deleted, not replaced.

## Testing

- **Backend** (`backend/tests/api/test_settings_routes.py`, new file — no route-level test currently covers `/api/v1/settings/queue`):
  - GET returns `operator_name: null` on a fresh row.
  - PUT with only `operator_name` set persists it and leaves `check_interval_minutes` at its prior value (proves partial update doesn't clobber).
  - PUT with only `check_interval_minutes` set leaves a previously-set `operator_name` untouched.
  - PUT with `operator_name: ""` clears it back to `null`.

- **Frontend** (`frontend/src/components/Sidebar.test.tsx`): extend the two render helpers (`renderOnFleet`, `renderOnQueue`) with optional `operatorName`/`printerCount` params defaulted so existing calls keep working unchanged. New cases:
  - No identity row when `operatorName` is `null`/empty; printer-count line still renders.
  - Identity row renders with correct initials (single word, two words) when `operatorName` is set.
  - Printer-count text is singular for `1` and plural otherwise, and reflects the live `printerCount` prop rather than any fixed number.

- **Frontend** (`frontend/src/screens/SettingsScreen.test.tsx`, existing file): the `beforeEach` fetch mock's `/settings/queue` response (currently `{ check_interval_minutes: 5 }`, `SettingsScreen.test.tsx:14`) needs `operator_name: null` added so the typed `QueueConfig` response stays valid. New test: Display name field loads existing value, saves on blur, clears to `null` when blanked — following the same `userEvent` + `waitFor` pattern as the existing "Print defaults shows the wired queue-check-interval control" test. No existing test asserts on the old `Released`/`Channel` tiles or `"0.7.2"` (confirmed via grep), so nothing to remove there — just add a case confirming the About page renders the injected `__APP_VERSION__` value.

- **Frontend ambient types**: `frontend/src/vite-env.d.ts` already exists (currently just `/// <reference types="vite/client" />`) — append the `declare const __APP_VERSION__: string;` line rather than overwriting it.
