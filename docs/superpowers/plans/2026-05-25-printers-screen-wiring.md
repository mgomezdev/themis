# Printers Screen Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Printers screen to the live backend API — replacing mock data, redesigning the add-printer wizard to be data-driven, and adding a `test-connection` endpoint.

**Architecture:** Inline state with `useState`/`useEffect` in `PrintersScreen.tsx`, a new `frontend/src/api/printers.ts` typed fetch module, and two backend changes: a `create_client_from_config` factory helper and a `POST /test-connection` endpoint that exercises the ABC's connect/connected/disconnect interface.

**Tech Stack:** FastAPI, SQLAlchemy async, pytest-asyncio, httpx (backend); React, TypeScript, Vitest, @testing-library/react (frontend)

**Spec:** `docs/superpowers/specs/2026-05-25-printers-screen-wiring-design.md`

---

### Task 1: Create feature branch

**Files:** none

- [ ] **Step 1: Create and check out the branch**

```bash
git checkout -b feat/issue-9-printers-screen-wiring
```

- [ ] **Step 2: Verify you're on the right branch**

```bash
git branch --show-current
```

Expected output: `feat/issue-9-printers-screen-wiring`

---

### Task 2: Backend — `create_client_from_config` helper + test infrastructure

**Files:**
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_printer_factory.py`
- Modify: `backend/app/services/printer_client_factory.py`

- [ ] **Step 1: Create the tests directory and empty `__init__.py`**

```bash
mkdir backend/tests
touch backend/tests/__init__.py
```

- [ ] **Step 2: Write `conftest.py` with a minimal test client**

Create `backend/tests/conftest.py`:

```python
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.api.routes.printers import router as printers_router
from app.database import Base, get_session


@pytest_asyncio.fixture
async def client():
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_session():
        async with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(printers_router)
    test_app.dependency_overrides[get_session] = override_get_session

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        yield c

    await test_engine.dispose()
```

- [ ] **Step 3: Write the failing test for `create_client_from_config`**

Create `backend/tests/test_printer_factory.py`:

```python
import pytest
from app.services.printer_client_factory import create_client_from_config, REGISTRY


def test_create_client_from_config_bambu():
    client = create_client_from_config(
        "bambu",
        {"host": "192.168.1.100", "access_code": "12345678", "serial": "ABCD1234"},
    )
    assert client is not None


def test_create_client_from_config_unknown_type():
    with pytest.raises(ValueError, match="Unknown printer type"):
        create_client_from_config("unknown_type", {})


def test_create_client_from_config_ignores_extra_fields():
    # Extra fields in connection_config must not cause a TypeError
    client = create_client_from_config(
        "bambu",
        {"host": "192.168.1.100", "access_code": "12345678", "serial": "X1", "extra": "ignored"},
    )
    assert client is not None
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_printer_factory.py -v
```

Expected: `FAILED` — `create_client_from_config` does not exist yet.

- [ ] **Step 5: Add `create_client_from_config` to `printer_client_factory.py`**

Open `backend/app/services/printer_client_factory.py` and add after the existing `create_client` function:

```python
def create_client_from_config(printer_type: str, connection_config: dict) -> AbstractPrinterClient:
    cls = _load_class(printer_type)
    accepted = {f.name for f in cls.connection_fields()}
    kwargs = {k: v for k, v in connection_config.items() if k in accepted}
    return cls(**kwargs)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_printer_factory.py -v
```

Expected: all 3 tests `PASSED`.

- [ ] **Step 7: Commit**

```bash
git add backend/tests/__init__.py backend/tests/conftest.py backend/tests/test_printer_factory.py backend/app/services/printer_client_factory.py
git commit -m "feat(backend): add create_client_from_config factory helper"
```

---

### Task 3: Backend — `POST /api/v1/printers/test-connection` endpoint

**Files:**
- Create: `backend/tests/test_printers.py`
- Modify: `backend/app/api/routes/printers.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_printers.py`:

```python
import pytest


async def test_test_connection_unknown_type(client):
    resp = await client.post(
        "/api/v1/printers/test-connection",
        json={"printer_type": "not_a_real_type", "connection_config": {}},
    )
    assert resp.status_code == 422


async def test_test_connection_known_type_returns_ok_field(client):
    # With a real printer type but no actual hardware, connect() will fail gracefully.
    # The endpoint must return a JSON object with an "ok" key regardless.
    resp = await client.post(
        "/api/v1/printers/test-connection",
        json={
            "printer_type": "bambu",
            "connection_config": {"host": "192.168.1.1", "access_code": "00000000", "serial": "TEST"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "ok" in body
    assert isinstance(body["ok"], bool)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && python -m pytest tests/test_printers.py -v
```

Expected: `FAILED` — route `/api/v1/printers/test-connection` does not exist yet.

- [ ] **Step 3: Add the endpoint to `printers.py`**

Open `backend/app/api/routes/printers.py`. Add the following import at the top of the file if not already present:

```python
import asyncio
```

Add this import alongside the existing factory imports:

```python
from ...services.printer_client_factory import REGISTRY, get_printer_types_for_ui, create_client_from_config
```

Then add the request model and endpoint **before** the `@router.get("/{printer_id}")` route (to avoid path collision):

```python
class TestConnectionRequest(BaseModel):
    printer_type: str
    connection_config: dict


@router.post("/test-connection")
async def test_connection(body: TestConnectionRequest) -> dict:
    if body.printer_type not in REGISTRY:
        raise HTTPException(422, f"Unknown printer_type: {body.printer_type!r}")
    client = create_client_from_config(body.printer_type, body.connection_config)
    try:
        client.connect()
        await asyncio.sleep(5)
        ok = client.connected
        if ok:
            return {"ok": True}
        return {"ok": False, "error": "Could not connect"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        client.disconnect()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_printers.py -v
```

Expected: both tests `PASSED`. (The `test_connection_known_type` test will return `{"ok": false}` with an error since there's no real hardware — that's correct.)

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_printers.py backend/app/api/routes/printers.py
git commit -m "feat(backend): add POST /api/v1/printers/test-connection endpoint"
```

---

### Task 4: Backend — augment `_to_dict` with live `connected` status

**Files:**
- Modify: `backend/app/api/routes/printers.py`
- Modify: `backend/tests/test_printers.py`

- [ ] **Step 1: Add the failing test to `test_printers.py`**

Append to `backend/tests/test_printers.py`:

```python
async def test_list_printers_includes_connected_field(client):
    # Create a printer via the API
    resp = await client.post(
        "/api/v1/printers",
        json={
            "name": "Test Printer",
            "printer_type": "bambu",
            "connection_config": {"host": "192.168.1.10", "access_code": "12345678", "serial": "SN001"},
        },
    )
    assert resp.status_code == 201

    # List printers — must include connected field
    resp = await client.get("/api/v1/printers")
    assert resp.status_code == 200
    printers = resp.json()
    assert len(printers) == 1
    assert "connected" in printers[0]
    # No live client in tests, so connected must be False
    assert printers[0]["connected"] is False
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && python -m pytest tests/test_printers.py::test_list_printers_includes_connected_field -v
```

Expected: `FAILED` — `connected` key missing from response.

- [ ] **Step 3: Update `_to_dict` in `printers.py`**

Find the `_to_dict` function in `backend/app/api/routes/printers.py` and replace it with:

```python
def _to_dict(p: Printer) -> dict:
    client = printer_manager._clients.get(p.id)
    return {
        "id": p.id,
        "name": p.name,
        "printer_type": p.printer_type,
        "connection_config": p.connection_config,
        "awaiting_plate_clear": p.awaiting_plate_clear,
        "orca_printer_profiles": p.orca_printer_profiles,
        "current_orca_printer_profile": p.current_orca_printer_profile,
        "enabled": p.enabled,
        "connected": client.connected if client else False,
    }
```

- [ ] **Step 4: Run all backend tests**

```bash
cd backend && python -m pytest tests/ -v
```

Expected: all tests `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/printers.py backend/tests/test_printers.py
git commit -m "feat(backend): include connected status in printer list response"
```

---

### Task 5: Frontend — `api/printers.ts` module

**Files:**
- Create: `frontend/src/api/printers.ts`

- [ ] **Step 1: Create the API module**

Create `frontend/src/api/printers.ts`:

```typescript
const BASE = '/api/v1/printers';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

export interface ConnectionField {
  name: string;
  label: string;
  field_type: 'text' | 'password' | 'number';
  required: boolean;
  default: string | number | null;
  placeholder: string;
  help_text: string;
}

export interface PrinterType {
  printer_type: string;
  display_name: string;
  connection_fields: ConnectionField[];
}

export interface ApiPrinter {
  id: number;
  name: string;
  printer_type: string;
  connection_config: Record<string, unknown>;
  awaiting_plate_clear: boolean;
  orca_printer_profiles: string[];
  current_orca_printer_profile: string | null;
  enabled: boolean;
  connected: boolean;
}

export interface CreatePrinterBody {
  name: string;
  printer_type: string;
  connection_config: Record<string, unknown>;
  orca_printer_profiles?: string[];
  current_orca_printer_profile?: string | null;
}

export interface UpdatePrinterBody {
  name?: string;
  connection_config?: Record<string, unknown>;
  orca_printer_profiles?: string[];
  current_orca_printer_profile?: string | null;
  enabled?: boolean;
}

export function fetchPrinterTypes(): Promise<PrinterType[]> {
  return request(`${BASE}/types`);
}

export function fetchPrinters(): Promise<ApiPrinter[]> {
  return request(BASE);
}

export function createPrinter(body: CreatePrinterBody): Promise<ApiPrinter> {
  return request(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function updatePrinter(id: number, body: UpdatePrinterBody): Promise<ApiPrinter> {
  return request(`${BASE}/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function deletePrinter(id: number): Promise<void> {
  return request(`${BASE}/${id}`, { method: 'DELETE' });
}

export function testConnection(body: {
  printer_type: string;
  connection_config: Record<string, unknown>;
}): Promise<{ ok: boolean; error?: string }> {
  return request(`${BASE}/test-connection`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/printers.ts
git commit -m "feat(frontend): add api/printers.ts typed fetch module"
```

---

### Task 6: Frontend — wire printers table to API

**Files:**
- Modify: `frontend/src/screens/PrintersScreen.tsx`
- Modify: `frontend/src/screens/PrintersScreen.test.tsx`

- [ ] **Step 1: Update the test file first (TDD)**

Replace the contents of `frontend/src/screens/PrintersScreen.test.tsx` with:

```tsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { PrintersScreen } from './PrintersScreen';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <MemoryRouter>{children}</MemoryRouter>
);

const mockPrinters = [
  {
    id: 1,
    name: 'Forge',
    printer_type: 'bambu',
    connection_config: { host: '192.168.1.100', access_code: '12345678', serial: 'SN001' },
    awaiting_plate_clear: false,
    orca_printer_profiles: [],
    current_orca_printer_profile: null,
    enabled: true,
    connected: true,
  },
];

const mockTypes = [
  {
    printer_type: 'bambu',
    display_name: 'Bambu Lab',
    connection_fields: [
      { name: 'host', label: 'IP Address', field_type: 'text', required: true, default: null, placeholder: '192.168.1.x', help_text: '' },
      { name: 'access_code', label: 'Access Code', field_type: 'password', required: true, default: null, placeholder: '', help_text: '' },
      { name: 'serial', label: 'Serial Number', field_type: 'text', required: true, default: null, placeholder: '', help_text: '' },
    ],
  },
];

function makeFetch(url: string) {
  if (url.includes('/types')) return Promise.resolve({ ok: true, json: () => Promise.resolve(mockTypes) });
  if (url === '/api/v1/printers') return Promise.resolve({ ok: true, json: () => Promise.resolve(mockPrinters) });
  return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => makeFetch(url)));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('PrintersScreen', () => {
  it('renders fetched printer name', async () => {
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => expect(screen.getByText('Forge')).toBeTruthy());
  });

  it('shows Add printer button', async () => {
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => expect(screen.getByRole('button', { name: /add printer/i })).toBeTruthy());
  });

  it('shows online count in header subtitle', async () => {
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => expect(screen.getByText(/1 connected/i)).toBeTruthy());
  });

  it('clicking Add printer shows wizard step 1 with type tiles', async () => {
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => screen.getByRole('button', { name: /add printer/i }));
    await user.click(screen.getByRole('button', { name: /add printer/i }));
    await waitFor(() => expect(screen.getByText('Bambu Lab')).toBeTruthy());
  });

  it('wizard advances to step 2', async () => {
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => screen.getByRole('button', { name: /add printer/i }));
    await user.click(screen.getByRole('button', { name: /add printer/i }));
    await waitFor(() => screen.getByText('Bambu Lab'));
    await user.click(screen.getByRole('button', { name: /next/i }));
    await waitFor(() => expect(screen.getByText(/IP Address/i)).toBeTruthy());
  });
});
```

- [ ] **Step 2: Run to verify tests fail**

```bash
cd frontend && npx vitest run src/screens/PrintersScreen.test.tsx
```

Expected: several tests `FAILED` (screen still shows mock data).

- [ ] **Step 3: Rewrite `PrintersScreen.tsx`**

Replace the entire contents of `frontend/src/screens/PrintersScreen.tsx` with:

```tsx
import { useState, useEffect, Fragment } from 'react';
import { Icons } from '../components/icons';
import { StatusPill, SectionHeader } from '../components/ui';
import {
  fetchPrinters,
  fetchPrinterTypes,
  createPrinter,
  updatePrinter,
  deletePrinter,
  testConnection,
  type ApiPrinter,
  type PrinterType,
  type ConnectionField,
} from '../api/printers';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ConnStatus = 'idle' | 'testing' | 'success' | 'error';

interface WizardData {
  printerType: PrinterType | null;
  nickname: string;
  connectionConfig: Record<string, string>;
}

// ---------------------------------------------------------------------------
// EditForm — inline edit for an existing printer
// ---------------------------------------------------------------------------

function EditForm({
  printer,
  types,
  onSave,
  onCancel,
}: {
  printer: ApiPrinter;
  types: PrinterType[];
  onSave: () => void;
  onCancel: () => void;
}) {
  const ptype = types.find(t => t.printer_type === printer.printer_type);
  const [name, setName] = useState(printer.name);
  const [config, setConfig] = useState<Record<string, string>>(
    Object.fromEntries(
      Object.entries(printer.connection_config).map(([k, v]) => [k, String(v)])
    )
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await updatePrinter(printer.id, { name, connection_config: config });
      onSave();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card" style={{ padding: 16, marginTop: 8 }}>
      <div className="col gap-3">
        <div>
          <label className="label">Nickname</label>
          <input className="input" value={name} onChange={e => setName(e.target.value)} />
        </div>
        {ptype?.connection_fields.map((f: ConnectionField) => (
          <div key={f.name}>
            <label className="label">{f.label}</label>
            <input
              className="input"
              type={f.field_type === 'password' ? 'password' : 'text'}
              placeholder={f.placeholder}
              value={config[f.name] ?? ''}
              onChange={e => setConfig({ ...config, [f.name]: e.target.value })}
            />
          </div>
        ))}
        {error && <div style={{ color: 'var(--err)', fontSize: 13 }}>{error}</div>}
        <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
          <button className="btn ghost sm" onClick={onCancel}>Cancel</button>
          <button className="btn primary sm" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PrinterAddForm — 3-step wizard
// ---------------------------------------------------------------------------

function PrinterAddForm({
  types,
  onCancel,
  onCreated,
}: {
  types: PrinterType[];
  onCancel: () => void;
  onCreated: () => void;
}) {
  const [step, setStep] = useState(1);
  const [data, setData] = useState<WizardData>({
    printerType: types[0] ?? null,
    nickname: '',
    connectionConfig: {},
  });
  const [connStatus, setConnStatus] = useState<ConnStatus>('idle');
  const [connError, setConnError] = useState<string | null>(null);
  const [finishing, setFinishing] = useState(false);
  const [finishError, setFinishError] = useState<string | null>(null);

  async function handleTestConnection() {
    if (!data.printerType) return;
    setConnStatus('testing');
    setConnError(null);
    try {
      const result = await testConnection({
        printer_type: data.printerType.printer_type,
        connection_config: data.connectionConfig,
      });
      if (result.ok) {
        setConnStatus('success');
      } else {
        setConnStatus('error');
        setConnError(result.error ?? 'Could not connect');
      }
    } catch (e) {
      setConnStatus('error');
      setConnError(e instanceof Error ? e.message : 'Connection test failed');
    }
  }

  async function handleFinish() {
    if (!data.printerType) return;
    setFinishing(true);
    setFinishError(null);
    try {
      await createPrinter({
        name: data.nickname || data.printerType.display_name,
        printer_type: data.printerType.printer_type,
        connection_config: data.connectionConfig,
      });
      onCreated();
    } catch (e) {
      setFinishError(e instanceof Error ? e.message : 'Failed to add printer');
      setFinishing(false);
    }
  }

  const steps = [
    { n: 1, label: 'Type' },
    { n: 2, label: 'Connect' },
    { n: 3, label: 'Review' },
  ] as const;

  return (
    <div className="col gap-4">
      <div className="row gap-2">
        <button className="btn ghost sm" onClick={onCancel}>
          {Icons.chevL} Printers
        </button>
        <span className="muted small">/</span>
        <span className="small">Add printer</span>
      </div>

      <div style={{ maxWidth: 760 }}>
        {/* Step indicators */}
        <div className="row gap-3" style={{ marginBottom: 24 }}>
          {steps.map(s => (
            <div key={s.n} className="row gap-2" style={{ alignItems: 'center' }}>
              <div style={{
                width: 24, height: 24, borderRadius: 12,
                display: 'grid', placeItems: 'center',
                fontSize: 11, fontWeight: 600,
                background: step >= s.n ? 'var(--accent)' : 'var(--bg-3)',
                color: step >= s.n ? '#04101f' : 'var(--text-3)',
                border: step === s.n ? '2px solid var(--accent-hi)' : '1px solid var(--border-1)',
              }}>
                {step > s.n ? '✓' : s.n}
              </div>
              <span className="small" style={{ color: step >= s.n ? 'var(--text-1)' : 'var(--text-3)' }}>
                {s.label}
              </span>
              {s.n < 3 && (
                <div style={{ width: 40, height: 1, background: 'var(--border-1)', marginLeft: 8 }} />
              )}
            </div>
          ))}
        </div>

        {/* Step 1: Printer type */}
        {step === 1 && (
          <div className="card" style={{ padding: 24 }}>
            <SectionHeader title="Select printer type" sub="Choose the vendor for this printer." />
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10, marginBottom: 20 }}>
              {types.map(t => {
                const active = data.printerType?.printer_type === t.printer_type;
                return (
                  <button key={t.printer_type}
                    onClick={() => setData({ ...data, printerType: t, connectionConfig: {} })}
                    className="card"
                    style={{
                      textAlign: 'left', padding: 14, cursor: 'pointer',
                      background: active ? 'var(--bg-3)' : 'var(--bg-1)',
                      borderColor: active ? 'var(--accent)' : 'var(--border-1)',
                    }}>
                    <div className="row between">
                      <div style={{ fontWeight: 500 }}>{t.display_name}</div>
                      {active && <div style={{ color: 'var(--accent-hi)' }}>{Icons.check}</div>}
                    </div>
                  </button>
                );
              })}
            </div>
            <div>
              <label className="label">Nickname</label>
              <input className="input" value={data.nickname}
                placeholder="e.g. Atlas, Forge, Iris"
                onChange={e => setData({ ...data, nickname: e.target.value })} />
              <div className="tiny muted" style={{ marginTop: 6 }}>
                Shown in queue and on tiles.
              </div>
            </div>
            <div className="row gap-2" style={{ marginTop: 24, justifyContent: 'flex-end' }}>
              <button className="btn ghost" onClick={onCancel}>Cancel</button>
              <button className="btn primary" disabled={!data.printerType} onClick={() => setStep(2)}>
                Next {Icons.chevR}
              </button>
            </div>
          </div>
        )}

        {/* Step 2: Connection fields */}
        {step === 2 && data.printerType && (
          <div className="card" style={{ padding: 24 }}>
            <SectionHeader title={`Connect to ${data.printerType.display_name}`} />
            <div className="col gap-3">
              {data.printerType.connection_fields.map((f: ConnectionField) => (
                <div key={f.name}>
                  <label className="label">{f.label}{f.required ? '' : ' (optional)'}</label>
                  <input
                    className={`input${f.field_type === 'number' ? ' mono' : ''}`}
                    type={f.field_type === 'password' ? 'password' : f.field_type === 'number' ? 'text' : 'text'}
                    placeholder={f.placeholder}
                    value={data.connectionConfig[f.name] ?? (f.default != null ? String(f.default) : '')}
                    onChange={e => setData({
                      ...data,
                      connectionConfig: { ...data.connectionConfig, [f.name]: e.target.value },
                    })}
                  />
                  {f.help_text && <div className="tiny muted" style={{ marginTop: 4 }}>{f.help_text}</div>}
                </div>
              ))}
            </div>

            {connStatus === 'success' && (
              <div className="row gap-2" style={{ marginTop: 12, color: 'var(--ok)', fontSize: 13 }}>
                {Icons.check} Connection successful
              </div>
            )}
            {connStatus === 'error' && (
              <div className="row gap-2" style={{ marginTop: 12, color: 'var(--err)', fontSize: 13 }}>
                {Icons.alert} {connError ?? 'Could not connect'}
              </div>
            )}

            <div className="row gap-2" style={{ marginTop: 24, justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setStep(1)}>{Icons.chevL} Back</button>
              <button className="btn"
                onClick={handleTestConnection}
                disabled={connStatus === 'testing'}>
                {connStatus === 'testing' ? 'Testing…' : 'Test connection'}
              </button>
              <button className="btn primary" onClick={() => setStep(3)}>
                Next {Icons.chevR}
              </button>
            </div>
          </div>
        )}

        {/* Step 3: Review + finish */}
        {step === 3 && data.printerType && (
          <div className="card" style={{ padding: 24 }}>
            <SectionHeader title="Review" sub="Confirm the details before adding." />
            <div className="col gap-2" style={{ marginBottom: 20 }}>
              <div className="row gap-3">
                <span className="muted small" style={{ width: 120 }}>Type</span>
                <span className="small">{data.printerType.display_name}</span>
              </div>
              <div className="row gap-3">
                <span className="muted small" style={{ width: 120 }}>Nickname</span>
                <span className="small">{data.nickname || data.printerType.display_name}</span>
              </div>
              {Object.entries(data.connectionConfig).map(([k, v]) => {
                const field = data.printerType!.connection_fields.find(f => f.name === k);
                const isPassword = field?.field_type === 'password';
                return (
                  <div key={k} className="row gap-3">
                    <span className="muted small" style={{ width: 120 }}>{field?.label ?? k}</span>
                    <span className="small mono">{isPassword ? '••••••••' : v}</span>
                  </div>
                );
              })}
            </div>

            {finishError && (
              <div style={{ color: 'var(--err)', fontSize: 13, marginBottom: 12 }}>{finishError}</div>
            )}

            <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setStep(2)}>{Icons.chevL} Back</button>
              <button className="btn primary" onClick={handleFinish} disabled={finishing}>
                {finishing ? 'Adding…' : <>{Icons.check} Finish</>}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PrintersScreen — main export
// ---------------------------------------------------------------------------

export function PrintersScreen() {
  const [printers, setPrinters] = useState<ApiPrinter[]>([]);
  const [types, setTypes] = useState<PrinterType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [ps, ts] = await Promise.all([fetchPrinters(), fetchPrinterTypes()]);
      setPrinters(ps);
      setTypes(ts);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load printers');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleDelete(id: number) {
    if (!confirm('Delete this printer?')) return;
    try {
      await deletePrinter(id);
      await load();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Delete failed');
    }
  }

  if (adding) {
    return (
      <PrinterAddForm
        types={types}
        onCancel={() => setAdding(false)}
        onCreated={() => { setAdding(false); load(); }}
      />
    );
  }

  const onlineCount = printers.filter(p => p.connected).length;
  const offlineCount = printers.filter(p => !p.connected).length;

  const displayName = (p: ApiPrinter) =>
    types.find(t => t.printer_type === p.printer_type)?.display_name ?? p.printer_type;

  const connectionSummary = (p: ApiPrinter) => {
    const cfg = p.connection_config;
    if ('host' in cfg) return String(cfg.host);
    const first = Object.values(cfg)[0];
    return first != null ? String(first) : '—';
  };

  return (
    <div className="col gap-4">
      <SectionHeader
        title="Printers"
        sub={`${onlineCount} connected · ${offlineCount} offline`}
        actions={
          <button className="btn primary sm" onClick={() => setAdding(true)}>
            {Icons.plus} Add printer
          </button>
        }
      />

      {loading && (
        <div className="card" style={{ padding: 24, textAlign: 'center' }}>
          <span className="muted small">Loading…</span>
        </div>
      )}

      {error && (
        <div className="card" style={{ padding: 16, color: 'var(--err)' }}>
          {error}
          <button className="btn ghost sm" style={{ marginLeft: 12 }} onClick={load}>Retry</button>
        </div>
      )}

      {!loading && !error && printers.length === 0 && (
        <div className="card" style={{ padding: 24, textAlign: 'center' }}>
          <span className="muted small">No printers yet — add one to get started.</span>
        </div>
      )}

      {!loading && !error && printers.length > 0 && (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Printer</th>
                <th>Type</th>
                <th>Connection</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {printers.map(p => (
                <Fragment key={p.id}>
                  <tr style={{ opacity: p.enabled ? 1 : 0.5 }}>
                    <td>
                      <div className="col">
                        <div style={{ fontWeight: 500 }}>{p.name}</div>
                        <div className="mono tiny muted">#{p.id}</div>
                      </div>
                    </td>
                    <td><div className="small">{displayName(p)}</div></td>
                    <td className="mono small">{connectionSummary(p)}</td>
                    <td>
                      <StatusPill status={p.connected ? 'idle' : 'offline'} />
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
                        <button
                          className="btn ghost sm"
                          onClick={() => setEditingId(editingId === p.id ? null : p.id)}>
                          Edit
                        </button>
                        <button
                          className="btn icon ghost sm"
                          onClick={() => handleDelete(p.id)}>
                          {Icons.trash ?? Icons.more}
                        </button>
                      </div>
                    </td>
                  </tr>
                  {editingId === p.id && (
                    <tr>
                      <td colSpan={5} style={{ padding: '0 16px 16px' }}>
                        <EditForm
                          printer={p}
                          types={types}
                          onSave={() => { setEditingId(null); load(); }}
                          onCancel={() => setEditingId(null)}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run frontend tests**

```bash
cd frontend && npx vitest run src/screens/PrintersScreen.test.tsx
```

Expected: all tests `PASSED`.

- [ ] **Step 5: Verify TypeScript**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/screens/PrintersScreen.tsx frontend/src/screens/PrintersScreen.test.tsx
git commit -m "feat(frontend): wire printers screen to API, redesign add-printer wizard"
```

---

### Task 7: Run full test suite and verify

**Files:** none

- [ ] **Step 1: Run all backend tests**

```bash
cd backend && python -m pytest tests/ -v
```

Expected: all tests `PASSED`.

- [ ] **Step 2: Run all frontend tests**

```bash
cd frontend && npx vitest run
```

Expected: all tests `PASSED`.

- [ ] **Step 3: TypeScript full check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

---

### Task 8: Open PR

**Files:** none

- [ ] **Step 1: Push branch**

```bash
git push -u origin feat/issue-9-printers-screen-wiring
```

- [ ] **Step 2: Open PR linking to issue #9**

```bash
gh pr create \
  --title "feat: wire printers screen to printer CRUD + connection test API (#9)" \
  --body "$(cat <<'EOF'
## Summary
- Adds `create_client_from_config` factory helper (reuses ABC/registry pattern without a DB row)
- Adds `POST /api/v1/printers/test-connection` endpoint exercising the ABC connect/connected/disconnect interface
- Augments `GET /api/v1/printers` to include `connected: bool` from PrinterManager
- Replaces mock data in `PrintersScreen` with live API calls
- Redesigns add-printer wizard: type tiles from `GET /types`, dynamic connection fields per type, real `POST /api/v1/printers` on finish
- Adds backend test infrastructure (`tests/conftest.py`) and tests for new endpoints

Closes #9

## Test plan
- [ ] Backend: `pytest tests/ -v` passes
- [ ] Frontend: `vitest run` passes
- [ ] `tsc --noEmit` clean
- [ ] Manual: start backend + frontend dev servers, add a Bambu printer via the wizard (test connection expected to fail without hardware, finish should succeed and appear in the table)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
