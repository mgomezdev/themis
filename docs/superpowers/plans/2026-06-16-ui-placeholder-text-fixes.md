# UI Placeholder Text Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Sidebar's hardcoded fake identity ("Lev Romero" / "LR" / "Workshop · 3 printers") with a settings-driven operator name and a live fleet count, and replace the Settings About page's disconnected version/release info with the real build version, per the approved design at `docs/superpowers/specs/2026-06-16-ui-placeholder-text-fixes-design.md`.

**Architecture:** Add a nullable `operator_name` column to the existing `QueueConfig` singleton settings row (same pattern as `SpoolmanConfig`), expose it through a partial-update PUT route, and wire it into the Sidebar via a new `useQueueConfig()` hook plus the existing `useFleetData()` hook for the live printer count. Inject the app version at build time via a Vite `define` constant read from `package.json`.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + aiosqlite (backend), React 19 + TypeScript + Vite 6 + Vitest (frontend).

---

## Grounding notes for the implementer

- The singleton-settings-row pattern (`_get_or_create_queue`, partial-update `if body.field is not None:`) already exists for `SpoolmanConfig` in `backend/app/api/routes/settings.py` — copy its shape exactly for the new `operator_name` field.
- `backend/app/database.py`'s `_migrate()` only matters for upgrading an existing on-disk DB. Backend tests use the `client` fixture in `backend/tests/conftest.py`, which calls `Base.metadata.create_all` on a fresh in-memory DB — so once the model has the column, tests see it immediately regardless of the migration code. `backend/tests/test_migrations.py` tests the migration block anyway (idempotency-on-call, not the actual pre-existing-table ALTER path) because that's the established convention for every prior column addition in that file — follow it for consistency, don't skip it.
- **Important Vite gotcha, not in the design spec:** this project has *two* separate Vite config files — `frontend/vite.config.ts` (used for `npm run dev` / `npm run build`) and `frontend/vitest.config.ts` (used for `npm test`). They do **not** share config; `vitest.config.ts` does not import or merge `vite.config.ts`. A `define` block added only to `vite.config.ts` will leave `__APP_VERSION__` undefined when Vitest runs, causing a `ReferenceError` the moment any test renders the About page. **Both files need the `define` block.** This is called out explicitly in Task 6.
- `frontend/src/App.test.tsx`'s global `fetch` mock returns `'[]'` (a JSON array) for every URL. `useQueueConfig()` will call `getQueueConfig()` against that mock and get back `[]` instead of `{check_interval_minutes, operator_name}` — this doesn't throw (it's valid JSON), it just means `queueConfig?.operator_name` evaluates to `undefined` (falsy), so the Sidebar's identity row stays hidden. No change to `App.test.tsx` is needed.
- No dedicated unit test is added for the new `useQueueConfig()` hook itself (Task 3) — there's no existing test for the structurally-identical `useSpoolmanConfig()` hook either (`frontend/src/api/spoolman.ts`), and the design spec's Testing section doesn't call for one. The hook is exercised indirectly through the Sidebar wiring in Task 4.

---

### Task 1: Backend — `operator_name` column on `QueueConfig` + migration

**Files:**
- Modify: `backend/app/models.py:117-121`
- Modify: `backend/app/database.py` (inside `_migrate()`, after the `uploaded_files` block, i.e. after line 109)
- Test: `backend/tests/test_migrations.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_migrations.py`:

```python
@pytest.mark.asyncio
async def test_migrate_adds_operator_name_to_queue_config():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate(conn)
        await _migrate(conn)  # idempotent — second run must not raise
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(queue_config)"))).fetchall()}
    assert "operator_name" in cols
    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_migrations.py::test_migrate_adds_operator_name_to_queue_config -v`
Expected: FAIL with `AssertionError: assert 'operator_name' in {'id', 'check_interval_minutes'}` (the column doesn't exist on the model yet).

- [ ] **Step 3: Add the column to the model**

In `backend/app/models.py`, replace the current `QueueConfig` class (lines 117-121):

```python
class QueueConfig(Base):
    __tablename__ = "queue_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    check_interval_minutes: Mapped[int] = mapped_column(default=5)
```

with:

```python
class QueueConfig(Base):
    __tablename__ = "queue_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    check_interval_minutes: Mapped[int] = mapped_column(default=5)
    operator_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
```

(`Optional` and `String` are already imported at the top of `models.py` — no import changes needed.)

- [ ] **Step 4: Add the migration block**

In `backend/app/database.py`, inside `_migrate()`, immediately after the `uploaded_files` block (after the `if "missing" not in uf_cols:` line, currently line 109, before the blank line that precedes `async def get_session`), add:

```python
    qc_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(queue_config)"))).fetchall()}
    if qc_cols and "operator_name" not in qc_cols:
        await conn.execute(text("ALTER TABLE queue_config ADD COLUMN operator_name VARCHAR(120)"))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_migrations.py -v`
Expected: PASS (all tests in the file, including the new one and the two pre-existing ones).

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/database.py backend/tests/test_migrations.py
git commit -m "feat(backend): add operator_name column to QueueConfig"
```

---

### Task 2: Backend — `operator_name` in settings routes (partial update) + new route test file

**Files:**
- Modify: `backend/app/api/routes/settings.py:14-45`
- Test: Create `backend/tests/api/test_settings_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/api/test_settings_routes.py`:

```python
from httpx import AsyncClient


async def test_get_queue_config_operator_name_null_on_fresh_row(client: AsyncClient):
    resp = await client.get("/api/v1/settings/queue")

    assert resp.status_code == 200
    body = resp.json()
    assert body["operator_name"] is None
    assert body["check_interval_minutes"] == 5


async def test_put_operator_name_only_leaves_check_interval_untouched(client: AsyncClient):
    await client.put("/api/v1/settings/queue", json={"check_interval_minutes": 10})

    resp = await client.put("/api/v1/settings/queue", json={"operator_name": "Workshop Lead"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["operator_name"] == "Workshop Lead"
    assert body["check_interval_minutes"] == 10


async def test_put_check_interval_only_leaves_operator_name_untouched(client: AsyncClient):
    await client.put("/api/v1/settings/queue", json={"operator_name": "Workshop Lead"})

    resp = await client.put("/api/v1/settings/queue", json={"check_interval_minutes": 15})

    assert resp.status_code == 200
    body = resp.json()
    assert body["check_interval_minutes"] == 15
    assert body["operator_name"] == "Workshop Lead"


async def test_put_empty_operator_name_clears_it_to_null(client: AsyncClient):
    await client.put("/api/v1/settings/queue", json={"operator_name": "Workshop Lead"})

    resp = await client.put("/api/v1/settings/queue", json={"operator_name": ""})

    assert resp.status_code == 200
    assert resp.json()["operator_name"] is None
```

No `@pytest.mark.asyncio` decorators needed — `backend/pyproject.toml` sets `asyncio_mode = "auto"`, matching the existing convention in `backend/tests/api/test_spoolman_patch.py`. The `client` fixture comes from `backend/tests/conftest.py` (no import needed beyond the type hint).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/api/test_settings_routes.py -v`
Expected: FAIL — `test_get_queue_config_operator_name_null_on_fresh_row` fails with `KeyError: 'operator_name'` (the response model doesn't have the field yet); the PUT-based tests fail the same way once that's fixed first, or with a 422 since `QueueConfigIn` currently requires `check_interval_minutes` and rejects an `operator_name`-only body with no `check_interval_minutes`.

- [ ] **Step 3: Update the Pydantic models and PUT handler**

In `backend/app/api/routes/settings.py`, replace lines 14-19:

```python
class QueueConfigOut(BaseModel):
    check_interval_minutes: int


class QueueConfigIn(BaseModel):
    check_interval_minutes: int
```

with:

```python
class QueueConfigOut(BaseModel):
    check_interval_minutes: int
    operator_name: str | None


class QueueConfigIn(BaseModel):
    check_interval_minutes: int | None = None
    operator_name: str | None = None
```

Then replace the `update_queue_config` handler (lines 36-45):

```python
@router.put("/queue", response_model=QueueConfigOut)
async def update_queue_config(
    body: QueueConfigIn,
    session: AsyncSession = Depends(get_session),
):
    row = await _get_or_create_queue(session)
    row.check_interval_minutes = max(1, body.check_interval_minutes)
    await session.commit()
    await session.refresh(row)
    return row
```

with:

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

`_get_or_create_queue` (lines 22-28) is unchanged — `QueueConfig(id=1, check_interval_minutes=5)` already gets `operator_name=None` for free from the column default.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/api/test_settings_routes.py -v`
Expected: PASS (4/4).

Then run the full backend suite to confirm no regressions (existing callers of `saveQueueConfig({ check_interval_minutes: v })` from the frontend send a body without `operator_name`, which is now optional, so this must not break anything that already depends on `update_queue_config`):

Run: `pytest -v`
Expected: PASS (all tests, including `backend/tests/api/test_queue_wiring.py` and `backend/tests/api/test_queue_api.py` if they touch `/settings/queue`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/settings.py backend/tests/api/test_settings_routes.py
git commit -m "feat(backend): partial-update operator_name via /api/v1/settings/queue"
```

---

### Task 3: Frontend — `QueueConfig` interface + `useQueueConfig()` hook

**Files:**
- Modify: `frontend/src/api/queue.ts:170-182`

- [ ] **Step 1: Update the interface and add the hook**

In `frontend/src/api/queue.ts`, replace line 170:

```ts
export interface QueueConfig { check_interval_minutes: number; }
```

with:

```ts
export interface QueueConfig { check_interval_minutes: number; operator_name: string | null; }
```

`operator_name` is non-optional on the interface (it always comes back from the backend — `null` when unset). This means the existing `saveQueueConfig(body: QueueConfig)` signature, which takes the *full* interface, would now reject the existing call site in `PrintDefaultsPage`'s `commitInterval` (`saveQueueConfig({ check_interval_minutes: v })`) as missing `operator_name` — and Task 5 needs to save `operator_name` on its own without resending `check_interval_minutes`. Widen the parameter type so both partial-save shapes typecheck. Replace lines 176-182:

```ts
export async function saveQueueConfig(body: QueueConfig): Promise<QueueConfig> {
  return request('/api/v1/settings/queue', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
```

with:

```ts
export async function saveQueueConfig(body: Partial<QueueConfig>): Promise<QueueConfig> {
  return request('/api/v1/settings/queue', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
```

`getQueueConfig` (lines 172-174) needs no changes — it already returns the full `QueueConfig` type.

Immediately after `saveQueueConfig` (before `export async function getQueue()`), add:

```ts
export function useQueueConfig(): { config: QueueConfig | null; refetch: () => void } {
  const [config, setConfig] = useState<QueueConfig | null>(null);
  const [tick, setTick] = useState(0);

  const refetch = useCallback(() => setTick(t => t + 1), []);

  useEffect(() => {
    let alive = true;
    getQueueConfig()
      .then(data => { if (alive) setConfig(data); })
      .catch(console.error);
    return () => { alive = false; };
  }, [tick]);

  return { config, refetch };
}
```

This mirrors `useSpoolmanConfig()` in `frontend/src/api/spoolman.ts:113-128` exactly. `useState`/`useEffect`/`useCallback` are already imported at the top of `queue.ts` (line 1).

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npm run build`
Expected: succeeds with no TypeScript errors. The existing `commitInterval` call site (`saveQueueConfig({ check_interval_minutes: v })`) still compiles because `saveQueueConfig` now takes `Partial<QueueConfig>`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/queue.ts
git commit -m "feat(frontend): add operator_name to QueueConfig + useQueueConfig hook"
```

---

### Task 4: Frontend — Sidebar operator identity + live printer count

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx` (full file, 88 lines)
- Modify: `frontend/src/App.tsx:1-7,19-27,61` (imports, `AppShell`, the `<Sidebar>` call)
- Test: `frontend/src/components/Sidebar.test.tsx`

This task combines the Sidebar component change with its `App.tsx` wiring in one task (rather than two, as the design's architecture section lays out) because `SidebarProps.operatorName`/`printerCount` are non-optional — splitting them into separate commits would leave the build broken (TypeScript error at the `<Sidebar queueCounts={queueCounts} ordersOpen={ordersOpen} />` call site in `App.tsx`) between commits.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/Sidebar.test.tsx`, replace the two helper functions (lines 7-22):

```tsx
// Renders on /fleet so the Job Queue nav item is NOT active, giving unambiguous badge colors.
function renderOnFleet(
  active: number, pending: number, blocked: number, ordersOpen = 0,
  operatorName: string | null = null, printerCount = 0,
) {
  return render(
    <MemoryRouter initialEntries={['/fleet']}>
      <Sidebar queueCounts={{ active, pending, blocked }} ordersOpen={ordersOpen}
               operatorName={operatorName} printerCount={printerCount} />
    </MemoryRouter>
  );
}

// Renders on /queue so the Job Queue nav item IS active (accent-color override in CSS).
function renderOnQueue(
  active: number, pending: number, blocked: number,
  operatorName: string | null = null, printerCount = 0,
) {
  return render(
    <MemoryRouter initialEntries={['/queue']}>
      <Sidebar queueCounts={{ active, pending, blocked }} ordersOpen={0}
               operatorName={operatorName} printerCount={printerCount} />
    </MemoryRouter>
  );
}
```

All existing call sites in the file (e.g. `renderOnFleet(0, 0, 0)`, `renderOnQueue(2, 3, 1)`) keep working unchanged because the new params are optional with defaults.

Then append a new describe block at the end of the file (after the closing `});` of `Queue badge status semantics`):

```tsx
// ─── Sidebar identity + live printer count ───────────────────────────────────

describe('Sidebar identity + printer count', () => {
  it('hides the identity row when operatorName is null', () => {
    const { container } = renderOnFleet(0, 0, 0, 0, null, 3);
    expect(container.querySelector('.user-chip')).toBeNull();
  });

  it('still renders the printer count line when operatorName is null', () => {
    renderOnFleet(0, 0, 0, 0, null, 3);
    expect(screen.getByText('3 printers')).toBeTruthy();
  });

  it('shows the identity row with single-word initials', () => {
    renderOnFleet(0, 0, 0, 0, 'Maria', 1);
    expect(screen.getByText('Maria')).toBeTruthy();
    expect(screen.getByText('M')).toBeTruthy();
  });

  it('shows the identity row with two-word initials', () => {
    renderOnFleet(0, 0, 0, 0, 'Maria Gomez', 1);
    expect(screen.getByText('Maria Gomez')).toBeTruthy();
    expect(screen.getByText('MG')).toBeTruthy();
  });

  it('uses singular "printer" for a count of 1', () => {
    renderOnFleet(0, 0, 0, 0, null, 1);
    expect(screen.getByText('1 printer')).toBeTruthy();
  });

  it('uses plural "printers" for a count other than 1', () => {
    renderOnFleet(0, 0, 0, 0, null, 0);
    expect(screen.getByText('0 printers')).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `cd frontend && npx vitest run src/components/Sidebar.test.tsx`
Expected: the pre-existing tests FAIL to even compile/run cleanly (TypeScript error: `Property 'operatorName' is missing in type '{ queueCounts: ...; ordersOpen: number; }'`) since `Sidebar` doesn't accept these props yet. If running under Vitest's looser transform this surfaces as a runtime prop-mismatch rather than a hard compile error — either way, the six new tests in `Sidebar identity + printer count` FAIL (no `.user-chip`/text found, since the component doesn't render initials/printer-count yet).

- [ ] **Step 3: Update `Sidebar.tsx`**

Replace the entire contents of `frontend/src/components/Sidebar.tsx` with:

```tsx
import { NavLink } from 'react-router-dom';
import { Icons } from './icons';

interface QueueCounts { active: number; pending: number; blocked: number; }

interface SidebarProps {
  queueCounts: QueueCounts;
  ordersOpen: number;
  operatorName: string | null;
  printerCount: number;
}

function initials(name: string): string {
  return name.trim().split(/\s+/).slice(0, 2).map(w => w[0]?.toUpperCase() ?? '').join('');
}

function QueueBadges({ counts }: { counts: QueueCounts }) {
  const { active, pending, blocked } = counts;
  if (active === 0 && pending === 0 && blocked === 0) return null;
  return (
    <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
      {active > 0 && (
        <span data-testid="badge-active" className="count num"
              style={{ marginLeft: 0, background: 'var(--ok-bg)', color: 'var(--ok)', borderColor: 'rgba(34,197,94,0.25)' }}>
          {active}
        </span>
      )}
      {pending > 0 && (
        <span data-testid="badge-pending" className="count num" style={{ marginLeft: 0 }}>
          {pending}
        </span>
      )}
      {blocked > 0 && (
        <span data-testid="badge-blocked" className="count num"
              style={{ marginLeft: 0, background: 'rgba(239,68,68,0.12)', color: 'var(--err)', borderColor: 'rgba(239,68,68,0.3)' }}>
          {blocked}
        </span>
      )}
    </div>
  );
}

export function Sidebar({ queueCounts, ordersOpen, operatorName, printerCount }: SidebarProps) {
  const items = [
    { to: '/queue',     label: 'Job queue',   icon: Icons.queue },
    { to: '/fleet',     label: 'Fleet',       icon: Icons.fleet },
    { to: '/orders',    label: 'Orders',      icon: Icons.orders,   count: ordersOpen },
    { to: '/files',     label: 'Files',       icon: Icons.files },
  ];

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark" />
        <div className="brand-name">themis<span className="dim">.farm</span></div>
      </div>

      <div className="nav-section">
        <div className="nav-section-label">Workshop</div>
        {items.map(it => (
          <NavLink key={it.to} to={it.to}
                   className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            {it.icon}
            <span className="label">{it.label}</span>
            {it.to === '/queue'
              ? <QueueBadges counts={queueCounts} />
              : it.count != null && it.count > 0 && <span className="count num">{it.count}</span>
            }
          </NavLink>
        ))}
      </div>

      <div className="nav-section">
        <div className="nav-section-label">Account</div>
        <NavLink to="/settings"
                 className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
          {Icons.settings}
          <span className="label">Settings</span>
        </NavLink>
      </div>

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
    </aside>
  );
}
```

The only changes from the current file: `SidebarProps` gains `operatorName`/`printerCount`; the new `initials()` helper; the destructured props in `Sidebar(...)`; and the `footer` block (old hardcoded `LR`/`Lev Romero`/`Workshop · 3 printers` replaced with the conditional identity row + always-on printer-count line). Everything else is byte-for-byte unchanged.

- [ ] **Step 4: Wire `App.tsx`**

In `frontend/src/App.tsx`, update the import on line 6:

```tsx
import { useQueue } from './api/queue';
```

to:

```tsx
import { useQueue, useQueueConfig } from './api/queue';
```

Add a new import after line 7 (`import { useOrders } from './api/orders';`):

```tsx
import { useFleetData } from './api/fleet';
```

Inside `AppShell()`, after line 20 (`const { jobs } = useQueue();`), add:

```tsx
  const { config: queueConfig } = useQueueConfig();
  const [printers] = useFleetData();
```

Then replace line 61:

```tsx
      <Sidebar queueCounts={queueCounts} ordersOpen={ordersOpen} />
```

with:

```tsx
      <Sidebar queueCounts={queueCounts} ordersOpen={ordersOpen}
               operatorName={queueConfig?.operator_name ?? null} printerCount={printers.length} />
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/Sidebar.test.tsx`
Expected: PASS (all tests, old and new).

Run: `cd frontend && npx vitest run src/App.test.tsx`
Expected: PASS (unaffected — see the grounding note on `App.test.tsx`'s fetch mock above).

Run: `cd frontend && npm run build`
Expected: succeeds with no TypeScript errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/components/Sidebar.test.tsx frontend/src/App.tsx
git commit -m "feat(frontend): drive Sidebar identity + printer count from real data"
```

---

### Task 5: Frontend — Settings "Display name" field

**Files:**
- Modify: `frontend/src/screens/SettingsScreen.tsx:388-449` (`PrintDefaultsPage`)
- Modify: `frontend/src/screens/SettingsScreen.test.tsx`

- [ ] **Step 1: Write the failing test**

In `frontend/src/screens/SettingsScreen.test.tsx`, update the `beforeEach` fetch mock on line 14:

```ts
    if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5 }), { status: 200 });
```

to:

```ts
    if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
```

Then append a new test inside the `describe('SettingsScreen', ...)` block, after the existing "Print defaults shows the wired queue-check-interval control" test:

```tsx
  it('Print defaults Display name field loads, saves on blur, and clears to null when blanked', async () => {
    const user = userEvent.setup();
    const putBodies: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes('/api/v1/tags')) return new Response('[]', { status: 200 });
      if (url.includes('/settings/spoolman')) return new Response(JSON.stringify({ enabled: false, url: null, api_key: null }), { status: 200 });
      if (url.includes('/settings/queue') && init?.method === 'PUT') {
        putBodies.push(JSON.parse(init.body as string));
        return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: null }), { status: 200 });
      }
      if (url.includes('/settings/queue')) return new Response(JSON.stringify({ check_interval_minutes: 5, operator_name: 'Workshop Lead' }), { status: 200 });
      return new Response('{}', { status: 200 });
    }));

    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /print defaults/i }));

    const input = await screen.findByPlaceholderText('e.g. Workshop Lead') as HTMLInputElement;
    await waitFor(() => expect(input.value).toBe('Workshop Lead'));

    await user.clear(input);
    input.blur();

    await waitFor(() => expect(putBodies).toContainEqual({ operator_name: null }));
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/screens/SettingsScreen.test.tsx`
Expected: FAIL — `screen.findByPlaceholderText('e.g. Workshop Lead')` times out (no such field exists in `PrintDefaultsPage` yet).

- [ ] **Step 3: Add the Display name field**

In `frontend/src/screens/SettingsScreen.tsx`, inside `PrintDefaultsPage()` (currently lines 388-449), after the existing `checkInterval`/`savingInterval` state and `commitInterval` function (after line 402, before the `// Rescan OrcaSlicer presets` comment on line 404), add:

```tsx
  // Operator display name — shown in the Sidebar footer. Blank hides it entirely.
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

Then, inside the returned JSX, add a new `FieldRow` right after the "Queue check interval" `FieldRow` closes (after line 435, before the "OrcaSlicer profiles" `FieldRow` starting at line 437):

```tsx
      <FieldRow label="Display name" hint="Shown in the sidebar. Leave blank to hide it.">
        <input className="input" value={operatorName}
               onChange={e => setOperatorName(e.target.value)}
               onBlur={e => commitOperatorName(e.target.value)}
               placeholder="e.g. Workshop Lead" style={{ width: '100%' }} />
        {savingName && <span className="muted small">saving…</span>}
      </FieldRow>
```

`saveQueueConfig({ operator_name: name.trim() || null })` sends a body without `check_interval_minutes` — this already typechecks because Task 3 widened `saveQueueConfig`'s parameter type to `Partial<QueueConfig>`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/screens/SettingsScreen.test.tsx`
Expected: PASS (all tests in the file).

Run: `cd frontend && npm run build`
Expected: succeeds with no TypeScript errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/screens/SettingsScreen.tsx frontend/src/screens/SettingsScreen.test.tsx frontend/src/api/queue.ts
git commit -m "feat(frontend): add Display name field to Settings > Print defaults"
```

---

### Task 6: Frontend — version injection (About page)

**Files:**
- Modify: `frontend/package.json:4`
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/vitest.config.ts`
- Modify: `frontend/src/vite-env.d.ts`
- Modify: `frontend/src/screens/SettingsScreen.tsx:757-761` (`AboutPage`)
- Modify: `frontend/src/screens/SettingsScreen.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to the `describe('SettingsScreen', ...)` block in `frontend/src/screens/SettingsScreen.test.tsx`:

```tsx
  it('About page renders the injected app version and no Released/Channel tiles', async () => {
    const user = userEvent.setup();
    render(<SettingsScreen />, { wrapper });
    await user.click(screen.getByRole('button', { name: /about/i }));
    await waitFor(() => expect(screen.getByText('0.1.0')).toBeTruthy());
    expect(screen.queryByText('Released')).toBeNull();
    expect(screen.queryByText('Channel')).toBeNull();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/screens/SettingsScreen.test.tsx`
Expected: FAIL with `ReferenceError: __APP_VERSION__ is not defined` — the source doesn't reference it yet, and even once it does, Vitest won't have a value for it until `vitest.config.ts` is updated in Step 4 below. (If the source change in Step 3 lands before the config changes, the failure mode shifts from "no such text '0.1.0'" to this `ReferenceError`; both are expected intermediate failures, not a sign something is wrong with the test.)

- [ ] **Step 3: Bump the version and update `AboutPage`**

In `frontend/package.json`, change line 4:

```json
  "version": "0.0.0",
```

to:

```json
  "version": "0.1.0",
```

In `frontend/src/screens/SettingsScreen.tsx`, replace lines 757-761:

```tsx
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        <AboutTile k="Version"      v="0.7.2" mono />
        <AboutTile k="Released"     v="2026-05-22" mono />
        <AboutTile k="Channel"      v="Stable" />
      </div>
```

with:

```tsx
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        <AboutTile k="Version" v={__APP_VERSION__} mono />
      </div>
```

- [ ] **Step 4: Inject the build-time constant in both Vite configs**

In `frontend/vite.config.ts`, replace the full file:

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,                 // listen on all interfaces (LAN / Tailscale), not just localhost
    allowedHosts: ['dionysus'],
    headers: {
      'Cache-Control': 'no-cache, must-revalidate',
    },
    proxy: {
      '/api': 'http://localhost:8001',
      '/ws': { target: 'ws://localhost:8001', ws: true },
    },
  },
})
```

with:

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import pkg from './package.json'

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  server: {
    host: true,                 // listen on all interfaces (LAN / Tailscale), not just localhost
    allowedHosts: ['dionysus'],
    headers: {
      'Cache-Control': 'no-cache, must-revalidate',
    },
    proxy: {
      '/api': 'http://localhost:8001',
      '/ws': { target: 'ws://localhost:8001', ws: true },
    },
  },
})
```

In `frontend/vitest.config.ts`, replace the full file:

```ts
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
  },
});
```

with:

```ts
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import pkg from './package.json';

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
  },
});
```

This second edit is the one the design spec omitted — `vitest.config.ts` is a separate config from `vite.config.ts` (confirmed: it does not import or merge it), so without this the new test from Step 1 fails with `__APP_VERSION__ is not defined` even after Step 3's source change.

- [ ] **Step 5: Add the ambient type declaration**

`frontend/src/vite-env.d.ts` currently contains exactly:

```ts
/// <reference types="vite/client" />
```

Append a line so the file reads:

```ts
/// <reference types="vite/client" />
declare const __APP_VERSION__: string;
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/screens/SettingsScreen.test.tsx`
Expected: PASS (all tests in the file).

Run: `cd frontend && npm run build`
Expected: succeeds with no TypeScript errors, and the built `dist/assets/*.js` contains the literal string `0.1.0` where `__APP_VERSION__` was referenced (sanity check, not a hard requirement to verify by hand).

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/vite.config.ts frontend/vitest.config.ts frontend/src/vite-env.d.ts frontend/src/screens/SettingsScreen.tsx frontend/src/screens/SettingsScreen.test.tsx
git commit -m "feat(frontend): inject real app version into About page, drop Released/Channel"
```

---

## Final verification (after all tasks complete)

- [ ] Run the full backend suite: `cd backend && pytest -v` — expect PASS.
- [ ] Run the full frontend suite: `cd frontend && npx vitest run` — expect PASS.
- [ ] Run the frontend production build: `cd frontend && npm run build` — expect success.
- [ ] Manually verify in a browser (per project convention for UI changes): start `uvicorn app.main:app --reload --port 8001` and `npm run dev`, then in the running app:
  - Confirm the Sidebar footer shows only the printer count (no identity row) on a fresh DB.
  - Set a Display name in Settings → Print defaults, confirm the Sidebar footer immediately reflects it after navigating back (or on next mount) with correct initials.
  - Blank the Display name field, confirm the identity row disappears again.
  - Confirm Settings → About shows "Version 0.1.0" and no "Released"/"Channel" tiles.
