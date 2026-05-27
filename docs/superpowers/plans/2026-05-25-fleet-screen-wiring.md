# Fleet Screen Live Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded mock `PRINTERS` array in `FleetScreen.tsx` with live data from a new `GET /api/v1/fleet` endpoint, updated in real time via the existing WebSocket at `/ws`.

**Architecture:** A new backend endpoint merges each printer's static config (name, type, loaded filaments) with its live normalized state (status, progress, temperatures, layer). The frontend adds a `useFleetData()` hook in `api/fleet.ts` that fetches on mount and applies `printer_state` WebSocket events as they arrive. `FleetScreen.tsx` consumes the hook and maps `FleetPrinter` objects to the existing `Printer` UI type — all internal component props and rendering stay unchanged.

**Tech Stack:** FastAPI, SQLAlchemy async, React 18, TypeScript, native `WebSocket` API, Vitest + Testing Library.

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `backend/app/api/routes/fleet.py` | `GET /api/v1/fleet` — merged static + live state |
| Modify | `backend/app/main.py` | Register fleet router |
| Create | `backend/tests/test_fleet.py` | Backend integration tests |
| Create | `frontend/src/api/fleet.ts` | `FleetPrinter` type, fetch, `toFleetPrinter` mapper, `useFleetData` hook |
| Create | `frontend/src/api/fleet.test.ts` | Unit tests for `toFleetPrinter` mapper |
| Modify | `frontend/src/screens/FleetScreen.tsx` | Replace `PRINTERS` mock with `useFleetData()` |
| Create | `frontend/src/screens/FleetScreen.test.tsx` | Integration tests for wired screen |

---

### Task 1: Create branch feat/fleet-screen-wiring

**Files:** git only

- [ ] **Step 1: Create and check out the feature branch**

```bash
git checkout -b feat/fleet-screen-wiring
```

- [ ] **Step 2: Verify you are on the new branch**

Run: `git branch --show-current`
Expected output: `feat/fleet-screen-wiring`

---

### Task 2: Backend — fleet endpoint (TDD)

**Files:**
- Create: `backend/tests/test_fleet.py`
- Create: `backend/app/api/routes/fleet.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_fleet.py`:

```python
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_fleet_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/fleet")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_fleet_returns_printer_with_offline_state(client: AsyncClient) -> None:
    await client.post("/api/v1/printers", json={
        "name": "Forge",
        "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "192.168.1.100"},
    })

    resp = await client.get("/api/v1/fleet")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    p = data[0]
    assert p["name"] == "Forge"
    assert p["printer_type"] == "elegoo_centauri"
    assert p["connected"] is False
    assert p["state"] == "unknown"
    assert p["progress"] == 0
    assert p["remaining_time"] == 0
    assert p["temperatures"] == {}
    assert p["layer_num"] is None
    assert p["total_layers"] is None
    assert p["current_print"] is None
    assert p["loaded_filaments"] == []


async def test_fleet_includes_loaded_filaments(client: AsyncClient) -> None:
    filament = {
        "slot": 0,
        "filament_id": None,
        "name": "Bambu PA-CF",
        "type": "PA-CF",
        "color": "#0c0c0c",
    }
    await client.post("/api/v1/printers", json={
        "name": "Forge",
        "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "192.168.1.100"},
        "loaded_filaments": [filament],
    })

    resp = await client.get("/api/v1/fleet")
    assert resp.status_code == 200
    assert resp.json()[0]["loaded_filaments"] == [filament]


async def test_fleet_awaiting_plate_clear_field_present(client: AsyncClient) -> None:
    await client.post("/api/v1/printers", json={
        "name": "Atlas",
        "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "192.168.1.10"},
    })

    resp = await client.get("/api/v1/fleet")
    assert resp.status_code == 200
    assert "awaiting_plate_clear" in resp.json()[0]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend
pytest tests/test_fleet.py -v
```

Expected: `FAILED` — `404 Not Found` for `/api/v1/fleet`.

- [ ] **Step 3: Create the fleet endpoint**

Create `backend/app/api/routes/fleet.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Printer
from ...services.printer_manager import printer_manager

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"])

_OFFLINE_STATE: dict = {
    "connected": False,
    "state": "unknown",
    "progress": 0,
    "remaining_time": 0,
    "layer_num": None,
    "total_layers": None,
    "temperatures": {},
    "capabilities": {},
    "current_print": None,
}


def _fleet_dict(p: Printer) -> dict:
    base = {
        "id": p.id,
        "name": p.name,
        "printer_type": p.printer_type,
        "enabled": p.enabled,
        "awaiting_plate_clear": p.awaiting_plate_clear,
        "loaded_filaments": p.loaded_filaments or [],
    }
    client = printer_manager._clients.get(p.id)
    if client and client.connected:
        try:
            live = printer_manager.get_normalized_state(p.id)
        except Exception:
            live = dict(_OFFLINE_STATE)
    else:
        live = dict(_OFFLINE_STATE)
    return {**base, **live}


@router.get("")
async def list_fleet(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(select(Printer))
    return [_fleet_dict(p) for p in result.scalars().all()]
```

- [ ] **Step 4: Register the router in main.py**

In `backend/app/main.py`, add after the existing router imports:

```python
from .api.routes.fleet import router as fleet_router
```

And after `app.include_router(printers_router)`:

```python
app.include_router(fleet_router)
```

The relevant section of `main.py` will look like:

```python
from .api.routes.files import router as files_router
from .api.routes.fleet import router as fleet_router        # ← add this line
from .api.routes.jobs import router as jobs_router
from .api.routes.printers import router as printers_router
from .api.routes.projects import router as projects_router
from .api.routes.queue import router as queue_router
# ...
app.include_router(printers_router)
app.include_router(fleet_router)                             # ← add this line
app.include_router(files_router)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend
pytest tests/test_fleet.py -v
```

Expected: all 4 tests `PASSED`.

- [ ] **Step 6: Run full backend suite to check for regressions**

```bash
cd backend
pytest -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/routes/fleet.py backend/app/main.py backend/tests/test_fleet.py
git commit -m "feat(backend): add GET /api/v1/fleet endpoint with merged static + live state"
```

---

### Task 3: Frontend — FleetPrinter type, mapper, fetch hook (TDD)

**Files:**
- Create: `frontend/src/api/fleet.ts`
- Create: `frontend/src/api/fleet.test.ts`

- [ ] **Step 1: Write the failing mapper tests**

Create `frontend/src/api/fleet.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { toFleetPrinter } from './fleet';
import type { FleetPrinter } from './fleet';

const BASE: FleetPrinter = {
  id: 1,
  name: 'Forge',
  printer_type: 'elegoo_centauri',
  enabled: true,
  connected: true,
  awaiting_plate_clear: false,
  loaded_filaments: [],
  state: 'IDLE',
  progress: 0,
  remaining_time: 0,
  layer_num: null,
  total_layers: null,
  temperatures: {},
  capabilities: {},
  current_print: null,
};

describe('toFleetPrinter', () => {
  it('converts numeric id to string', () => {
    expect(toFleetPrinter({ ...BASE, id: 42 }).id).toBe('42');
  });

  it('maps RUNNING state to printing status', () => {
    expect(toFleetPrinter({ ...BASE, state: 'RUNNING' }).status).toBe('printing');
  });

  it('maps disconnected printer to offline regardless of state', () => {
    expect(toFleetPrinter({ ...BASE, connected: false, state: 'RUNNING' }).status).toBe('offline');
  });

  it('maps awaiting_plate_clear to claiming status', () => {
    expect(toFleetPrinter({ ...BASE, awaiting_plate_clear: true }).status).toBe('claiming');
  });

  it('maps PAUSE state to paused status', () => {
    expect(toFleetPrinter({ ...BASE, state: 'PAUSE' }).status).toBe('paused');
  });

  it('maps FAILED state to error status', () => {
    expect(toFleetPrinter({ ...BASE, state: 'FAILED' }).status).toBe('error');
  });

  it('maps IDLE state to idle status', () => {
    expect(toFleetPrinter({ ...BASE, state: 'IDLE' }).status).toBe('idle');
  });

  it('uses first loaded filament as material', () => {
    const p = toFleetPrinter({
      ...BASE,
      loaded_filaments: [{ slot: 0, filament_id: null, name: 'PA-CF Black', type: 'PA-CF', color: '#0c0c0c' }],
    });
    expect(p.material).toEqual({ name: 'PA-CF Black', type: 'PA-CF', color: '#0c0c0c' });
  });

  it('uses placeholder material when loaded_filaments is empty', () => {
    const p = toFleetPrinter({ ...BASE, loaded_filaments: [] });
    expect(p.material).toEqual({ name: '—', type: '—', color: '#475472' });
  });

  it('extracts nozzle, bed, chamber temperatures', () => {
    const p = toFleetPrinter({ ...BASE, temperatures: { nozzle: 285, bed: 95, chamber: 58 } });
    expect(p.nozzleTemp).toBe(285);
    expect(p.bedTemp).toBe(95);
    expect(p.chamberTemp).toBe(58);
  });

  it('defaults missing temps to 0 and null', () => {
    const p = toFleetPrinter({ ...BASE, temperatures: {} });
    expect(p.nozzleTemp).toBe(0);
    expect(p.bedTemp).toBe(0);
    expect(p.chamberTemp).toBeNull();
  });

  it('maps layer_num + total_layers to layer object', () => {
    const p = toFleetPrinter({ ...BASE, layer_num: 88, total_layers: 312 });
    expect(p.layer).toEqual({ now: 88, total: 312 });
  });

  it('sets layer to null when layer_num is null', () => {
    expect(toFleetPrinter({ ...BASE, layer_num: null, total_layers: null }).layer).toBeNull();
  });

  it('rounds fractional progress', () => {
    expect(toFleetPrinter({ ...BASE, progress: 28.6 }).progress).toBe(29);
  });

  it('uses ECC badge for elegoo_centauri', () => {
    expect(toFleetPrinter({ ...BASE, printer_type: 'elegoo_centauri' }).badge).toBe('ECC');
  });

  it('uses P1S badge for bambu', () => {
    expect(toFleetPrinter({ ...BASE, printer_type: 'bambu' }).badge).toBe('P1S');
  });

  it('uses accent color for elegoo_centauri', () => {
    expect(toFleetPrinter({ ...BASE, printer_type: 'elegoo_centauri' }).accent).toBe('#22d3ee');
  });

  it('maps current_print to currentJobId', () => {
    expect(toFleetPrinter({ ...BASE, current_print: 'arm.gcode' }).currentJobId).toBe('arm.gcode');
  });

  it('maps null current_print to null currentJobId', () => {
    expect(toFleetPrinter({ ...BASE, current_print: null }).currentJobId).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend
npm test -- fleet.test.ts
```

Expected: `FAIL` — `Cannot find module './fleet'`.

- [ ] **Step 3: Create the fleet API module**

Create `frontend/src/api/fleet.ts`:

```typescript
import { useState, useEffect } from 'react';
import type { LoadedFilament } from './printers';
import type { Printer } from '../data/types';

export interface FleetPrinter {
  id: number;
  name: string;
  printer_type: string;
  enabled: boolean;
  connected: boolean;
  awaiting_plate_clear: boolean;
  loaded_filaments: LoadedFilament[];
  state: string;
  progress: number;
  remaining_time: number;
  layer_num: number | null;
  total_layers: number | null;
  temperatures: { nozzle?: number; bed?: number; chamber?: number };
  capabilities: Record<string, boolean>;
  current_print: string | null;
}

const ACCENT: Record<string, string> = {
  elegoo_centauri: '#22d3ee',
  bambu: '#3b82f6',
};

const BADGE: Record<string, string> = {
  elegoo_centauri: 'ECC',
  bambu: 'P1S',
};

function mapStatus(p: FleetPrinter): Printer['status'] {
  if (!p.connected) return 'offline';
  if (p.awaiting_plate_clear) return 'claiming';
  switch (p.state) {
    case 'RUNNING': return 'printing';
    case 'PAUSE': return 'paused';
    case 'FAILED': return 'error';
    default: return 'idle';
  }
}

export function toFleetPrinter(p: FleetPrinter): Printer {
  const mat = p.loaded_filaments[0];
  return {
    id: String(p.id),
    name: p.name,
    nickname: p.name,
    model: p.printer_type,
    badge: BADGE[p.printer_type] ?? p.printer_type.slice(0, 3).toUpperCase(),
    buildVolume: '',
    capabilities: Object.entries(p.capabilities ?? {})
      .filter(([, v]) => v)
      .map(([k]) => k.replace(/_/g, ' ')),
    chamber: false,
    status: mapStatus(p),
    progress: Math.round(p.progress ?? 0),
    timeRemaining: p.remaining_time ?? 0,
    timeElapsed: 0,
    layer:
      p.layer_num != null && p.total_layers != null
        ? { now: p.layer_num, total: p.total_layers }
        : null,
    nozzleTemp: p.temperatures?.nozzle ?? 0,
    bedTemp: p.temperatures?.bed ?? 0,
    chamberTemp: p.temperatures?.chamber ?? null,
    material: mat
      ? { name: mat.name || '—', type: mat.type || '—', color: mat.color || '#475472' }
      : { name: '—', type: '—', color: '#475472' },
    currentJobId: p.current_print ?? null,
    accent: ACCENT[p.printer_type] ?? '#888888',
  };
}

async function fetchFleetPrinters(): Promise<FleetPrinter[]> {
  const resp = await fetch('/api/v1/fleet');
  if (!resp.ok) throw new Error(`${resp.status}`);
  return resp.json();
}

export function useFleetData(): Printer[] {
  const [raw, setRaw] = useState<FleetPrinter[]>([]);

  useEffect(() => {
    fetchFleetPrinters().then(setRaw).catch(console.error);

    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${window.location.host}/ws`);
    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as { type: string; data: FleetPrinter };
        if (event.type === 'printer_state') {
          setRaw(prev =>
            prev.map(p => (p.id === event.data.id ? { ...p, ...event.data } : p)),
          );
        }
      } catch {
        // ignore malformed frames
      }
    };
    return () => ws.close();
  }, []);

  return raw.map(toFleetPrinter);
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd frontend
npm test -- fleet.test.ts
```

Expected: all 18 tests `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/fleet.ts frontend/src/api/fleet.test.ts
git commit -m "feat(frontend): add FleetPrinter type, mapper, and useFleetData hook"
```

---

### Task 4: Wire FleetScreen + tests (TDD)

**Files:**
- Create: `frontend/src/screens/FleetScreen.test.tsx`
- Modify: `frontend/src/screens/FleetScreen.tsx`

- [ ] **Step 1: Write the failing FleetScreen tests**

Create `frontend/src/screens/FleetScreen.test.tsx`:

```typescript
import { render, screen, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { FleetScreen } from './FleetScreen';
import type { FleetPrinter } from '../api/fleet';

// ── Mock WebSocket ──────────────────────────────────────────────────────────
class MockWS {
  static instances: MockWS[] = [];
  onmessage: ((e: { data: string }) => void) | null = null;
  close = vi.fn();
  constructor() {
    MockWS.instances.push(this);
  }
}

// ── Fixtures ────────────────────────────────────────────────────────────────
const PRINTER_1: FleetPrinter = {
  id: 1,
  name: 'Forge',
  printer_type: 'elegoo_centauri',
  enabled: true,
  connected: true,
  awaiting_plate_clear: false,
  loaded_filaments: [{ slot: 0, filament_id: null, name: 'Bambu PA-CF', type: 'PA-CF', color: '#0c0c0c' }],
  state: 'RUNNING',
  progress: 28,
  remaining_time: 312,
  layer_num: 88,
  total_layers: 312,
  temperatures: { nozzle: 285, bed: 95, chamber: 58 },
  capabilities: {},
  current_print: 'arm_bracket.gcode',
};

function mockFetch(data: FleetPrinter[]) {
  vi.spyOn(global, 'fetch').mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(data),
  } as Response);
}

describe('FleetScreen', () => {
  beforeEach(() => {
    MockWS.instances = [];
    Object.defineProperty(global, 'WebSocket', { value: MockWS, writable: true, configurable: true });
    vi.restoreAllMocks();
  });

  it('shows printer name loaded from API', async () => {
    mockFetch([PRINTER_1]);
    render(<FleetScreen />);
    await waitFor(() => expect(screen.getByText('Forge')).toBeInTheDocument());
  });

  it('shows correct printer count in header', async () => {
    mockFetch([PRINTER_1]);
    render(<FleetScreen />);
    await waitFor(() => expect(screen.getByText(/1 printers online/i)).toBeInTheDocument());
  });

  it('shows 0 printers when API returns empty list', async () => {
    mockFetch([]);
    render(<FleetScreen />);
    await waitFor(() => expect(screen.getByText(/0 printers online/i)).toBeInTheDocument());
  });

  it('reflects WebSocket printer_state update', async () => {
    mockFetch([PRINTER_1]);
    render(<FleetScreen />);
    await waitFor(() => expect(screen.getByText('Forge')).toBeInTheDocument());

    act(() => {
      MockWS.instances[0].onmessage?.({
        data: JSON.stringify({
          type: 'printer_state',
          data: { ...PRINTER_1, state: 'IDLE', progress: 0, remaining_time: 0 },
        }),
      });
    });

    // After update to IDLE, timeRemaining becomes 0 — verify no crash and Forge still shows
    expect(screen.getByText('Forge')).toBeInTheDocument();
  });

  it('ignores non-printer_state WebSocket events', async () => {
    mockFetch([PRINTER_1]);
    render(<FleetScreen />);
    await waitFor(() => expect(screen.getByText('Forge')).toBeInTheDocument());

    act(() => {
      MockWS.instances[0].onmessage?.({
        data: JSON.stringify({ type: 'plate_clear_required', data: { printer_id: 1 } }),
      });
    });

    // Component should still render without crashing
    expect(screen.getByText('Forge')).toBeInTheDocument();
  });

  it('closes WebSocket on unmount', async () => {
    mockFetch([PRINTER_1]);
    const { unmount } = render(<FleetScreen />);
    await waitFor(() => expect(MockWS.instances.length).toBe(1));
    unmount();
    expect(MockWS.instances[0].close).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend
npm test -- FleetScreen.test.tsx
```

Expected: tests fail — `FleetScreen` still uses mock data.

- [ ] **Step 3: Wire FleetScreen.tsx**

Apply these changes to `frontend/src/screens/FleetScreen.tsx`:

**3a. Replace the mock import and add fleet import:**

```typescript
// REMOVE this line:
import { PRINTERS, JOBS } from '../data/mock';

// ADD these lines:
import { JOBS } from '../data/mock';
import { useFleetData } from '../api/fleet';
```

**3b. Add `printers: Printer[]` prop to `FleetGrid`, `FleetList`, `FleetFocus`:**

Change the prop type of each component from:

```typescript
// FleetGrid
function FleetGrid({
  expandedId,
  onToggle,
}: {
  expandedId: string | null;
  onToggle: (id: string) => void;
})
```

To:

```typescript
function FleetGrid({
  printers,
  expandedId,
  onToggle,
}: {
  printers: Printer[];
  expandedId: string | null;
  onToggle: (id: string) => void;
})
```

Apply the same `printers: Printer[]` prop addition to `FleetList` and `FleetFocus`.

**3c. Inside `FleetGrid` — replace `PRINTERS.map` with `printers.map`:**

```typescript
// BEFORE:
{PRINTERS.map(p => {

// AFTER:
{printers.map(p => {
```

**3d. Inside `FleetList` — replace `PRINTERS.map` with `printers.map`:**

```typescript
// BEFORE:
{PRINTERS.map(p => {

// AFTER:
{printers.map(p => {
```

**3e. Inside `FleetFocus` — fix initialization and replace all `PRINTERS` references:**

```typescript
// BEFORE:
const [focusId, setFocusId] = useState(PRINTERS[0].id);
const hero = PRINTERS.find(p => p.id === focusId) ?? PRINTERS[0];

// AFTER:
const [focusId, setFocusId] = useState<string | null>(null);
const hero = printers.find(p => p.id === (focusId ?? printers[0]?.id)) ?? printers[0] ?? null;
```

Inside `FleetFocus` return statement:

```typescript
// BEFORE:
<PrinterExpandedCard printer={hero} onCollapse={() => {}} />
{PRINTERS.map(p => (

// AFTER:
{hero && <PrinterExpandedCard printer={hero} onCollapse={() => {}} />}
{printers.map(p => (
```

**3f. In `FleetScreen` (the main export) — add the hook and update header stats:**

```typescript
// BEFORE:
export function FleetScreen() {
  const [layout, setLayout] = useState<Layout>('grid');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const toggle = (id: string) =>
    setExpandedId(expandedId === id ? null : id);

  const printingCount = PRINTERS.filter(p => p.status === 'printing').length;
  const idleCount = PRINTERS.filter(p => p.status === 'idle').length;

// AFTER:
export function FleetScreen() {
  const printers = useFleetData();
  const [layout, setLayout] = useState<Layout>('grid');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const toggle = (id: string) =>
    setExpandedId(expandedId === id ? null : id);

  const printingCount = printers.filter(p => p.status === 'printing').length;
  const idleCount = printers.filter(p => p.status === 'idle').length;
```

**3g. In the header, update printer count display:**

```typescript
// BEFORE:
{PRINTERS.length} printers online

// AFTER:
{printers.length} printers online
```

**3h. Pass `printers` to layout components:**

```typescript
// BEFORE:
{layout === 'list' && (
  <FleetList expandedId={expandedId} onToggle={toggle} />
)}
{layout === 'focus' && (
  <FleetFocus expandedId={expandedId} onToggle={toggle} />
)}
{layout === 'grid' && (
  <FleetGrid expandedId={expandedId} onToggle={toggle} />
)}

// AFTER:
{layout === 'list' && (
  <FleetList printers={printers} expandedId={expandedId} onToggle={toggle} />
)}
{layout === 'focus' && (
  <FleetFocus printers={printers} expandedId={expandedId} onToggle={toggle} />
)}
{layout === 'grid' && (
  <FleetGrid printers={printers} expandedId={expandedId} onToggle={toggle} />
)}
```

- [ ] **Step 4: Run TypeScript check**

```bash
cd frontend
npm run build 2>&1 | head -40
```

Expected: no TypeScript errors. If there are errors, fix them before proceeding.

- [ ] **Step 5: Run FleetScreen tests to verify they pass**

```bash
cd frontend
npm test -- FleetScreen.test.tsx
```

Expected: all 6 tests `PASSED`.

- [ ] **Step 6: Run full frontend test suite to check for regressions**

```bash
cd frontend
npm test
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/screens/FleetScreen.tsx frontend/src/screens/FleetScreen.test.tsx
git commit -m "feat(frontend): wire FleetScreen to live API + WebSocket updates"
```

---

### Task 5: Open PR

**Files:** git/GitHub only

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/fleet-screen-wiring
```

- [ ] **Step 2: Open a PR targeting main**

```bash
gh pr create \
  --title "feat: wire fleet screen to live API + WebSocket" \
  --body "$(cat <<'EOF'
## Summary

- Adds `GET /api/v1/fleet` endpoint that merges static printer config with live normalized state (status, progress, temperatures, layer info)
- Adds `useFleetData()` hook in `frontend/src/api/fleet.ts` that fetches on mount and applies `printer_state` WebSocket events in real time
- Wires `FleetScreen.tsx` — replaces the static `PRINTERS` mock array with live data; all three layouts (grid, list, focus) now reflect actual printers
- `toFleetPrinter()` maps `FleetPrinter` API shape to the existing `Printer` UI type so all existing components are unchanged

## Test plan

- [ ] Backend: `pytest tests/test_fleet.py -v` — 4 tests pass
- [ ] Frontend mapper: `npm test -- fleet.test.ts` — 18 tests pass
- [ ] Frontend screen: `npm test -- FleetScreen.test.tsx` — 6 tests pass
- [ ] Manual: start `docker compose up`, add a printer on the Printers screen, navigate to Fleet — printer appears with live status

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Record the PR URL**

Run: `gh pr view --json url -q .url`
Note the URL — leave the PR open for manual review.
