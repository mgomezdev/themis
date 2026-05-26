# Filament Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track what filament is physically loaded in each printer slot using a list-based data model that supports future multi-spool hardware.

**Architecture:** A new `loaded_filaments` JSON column on the `Printer` SQLAlchemy model stores a list of slot objects `{ slot, filament_id, name, type, color }`. The existing `PATCH /api/v1/printers/{id}` route handles writes. On the frontend, `ApiPrinter` gains `loaded_filaments`, the printer table row shows read-only color swatches, and the `EditForm` gains a slot editor.

**Tech Stack:** SQLAlchemy JSON column, Pydantic, FastAPI, React + TypeScript, Vitest + Testing Library.

---

### Task 1: Create feature branch

**Files:** none

- [ ] **Step 1: Create and check out the feature branch**

```bash
git checkout -b feat/filament-tracking
```

- [ ] **Step 2: Verify you are on the new branch**

```bash
git branch --show-current
```
Expected output: `feat/filament-tracking`

---

### Task 2: Backend — model + API (TDD)

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/api/routes/printers.py`
- Test: `backend/tests/test_printers.py`

Context: The test `client` fixture uses an in-memory SQLite database created via `Base.metadata.create_all`. Adding the column to the SQLAlchemy model is all that is needed — no migration script required.

- [ ] **Step 1: Write the four failing tests**

Open `backend/tests/test_printers.py` and append these tests at the end of the file:

```python
async def test_loaded_filaments_defaults_to_empty_list(client):
    resp = await client.post(
        "/api/v1/printers",
        json={
            "name": "Test",
            "printer_type": "bambu",
            "connection_config": {"ip_address": "1.1.1.1", "access_code": "00000000", "serial_number": "SN1"},
        },
    )
    assert resp.status_code == 201
    assert resp.json()["loaded_filaments"] == []


async def test_create_printer_with_loaded_filaments(client):
    slots = [{"slot": 0, "filament_id": None, "name": "Bambu PLA Matte", "type": "PLA", "color": "#ff0000"}]
    resp = await client.post(
        "/api/v1/printers",
        json={
            "name": "Test",
            "printer_type": "bambu",
            "connection_config": {"ip_address": "1.1.1.2", "access_code": "00000000", "serial_number": "SN2"},
            "loaded_filaments": slots,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["loaded_filaments"] == slots


async def test_patch_loaded_filaments(client):
    resp = await client.post(
        "/api/v1/printers",
        json={
            "name": "Test",
            "printer_type": "bambu",
            "connection_config": {"ip_address": "1.1.1.3", "access_code": "00000000", "serial_number": "SN3"},
        },
    )
    printer_id = resp.json()["id"]
    slots = [{"slot": 0, "filament_id": None, "name": "Bambu PETG HF", "type": "PETG", "color": "#00aaff"}]
    resp = await client.patch(f"/api/v1/printers/{printer_id}", json={"loaded_filaments": slots})
    assert resp.status_code == 200
    assert resp.json()["loaded_filaments"] == slots


async def test_loaded_filaments_null_filament_id_roundtrips(client):
    slots = [{"slot": 0, "filament_id": None, "name": "Generic PLA", "type": "PLA", "color": "#cccccc"}]
    resp = await client.post(
        "/api/v1/printers",
        json={
            "name": "Test",
            "printer_type": "bambu",
            "connection_config": {"ip_address": "1.1.1.4", "access_code": "00000000", "serial_number": "SN4"},
            "loaded_filaments": slots,
        },
    )
    assert resp.status_code == 201
    result = resp.json()["loaded_filaments"][0]
    assert result["filament_id"] is None
```

- [ ] **Step 2: Run the new tests and verify they all fail**

```bash
cd backend && pytest tests/test_printers.py::test_loaded_filaments_defaults_to_empty_list tests/test_printers.py::test_create_printer_with_loaded_filaments tests/test_printers.py::test_patch_loaded_filaments tests/test_printers.py::test_loaded_filaments_null_filament_id_roundtrips -v
```

Expected: all 4 FAIL (KeyError or assertion error — `loaded_filaments` key is missing from the response).

- [ ] **Step 3: Add the column to the Printer model**

In `backend/app/models.py`, add one line to the `Printer` class after `enabled`:

```python
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    loaded_filaments: Mapped[list] = mapped_column(JSON, default=list)
```

- [ ] **Step 4: Extend PrinterCreate, PrinterUpdate, _to_dict, and the PATCH handler**

In `backend/app/api/routes/printers.py`:

**Add to `PrinterCreate`** (after `current_orca_printer_profile`):
```python
class PrinterCreate(BaseModel):
    name: str
    printer_type: str
    connection_config: dict
    orca_printer_profiles: list[str] = []
    current_orca_printer_profile: str | None = None
    loaded_filaments: list[dict] = []
```

**Add to `PrinterUpdate`** (after `enabled`):
```python
class PrinterUpdate(BaseModel):
    name: str | None = None
    connection_config: dict | None = None
    orca_printer_profiles: list[str] | None = None
    current_orca_printer_profile: str | None = None
    enabled: bool | None = None
    loaded_filaments: list[dict] | None = None
```

**Add to `_to_dict`** (after `"connected"`):
```python
def _to_dict(p: Printer) -> dict:
    live_client = printer_manager._clients.get(p.id)
    return {
        "id": p.id,
        "name": p.name,
        "printer_type": p.printer_type,
        "connection_config": p.connection_config,
        "awaiting_plate_clear": p.awaiting_plate_clear,
        "orca_printer_profiles": p.orca_printer_profiles,
        "current_orca_printer_profile": p.current_orca_printer_profile,
        "enabled": p.enabled,
        "loaded_filaments": p.loaded_filaments or [],
        "connected": live_client.connected if live_client else False,
    }
```

**Update `create_printer`** — pass `loaded_filaments` when constructing `Printer`:
```python
    printer = Printer(
        name=body.name,
        printer_type=body.printer_type,
        connection_config=body.connection_config,
        orca_printer_profiles=body.orca_printer_profiles,
        current_orca_printer_profile=body.current_orca_printer_profile,
        loaded_filaments=body.loaded_filaments,
    )
```

**Update `update_printer`** — add the guard after the `enabled` block:
```python
    if body.loaded_filaments is not None:
        printer.loaded_filaments = body.loaded_filaments
```

- [ ] **Step 5: Run the four new tests and verify they all pass**

```bash
cd backend && pytest tests/test_printers.py::test_loaded_filaments_defaults_to_empty_list tests/test_printers.py::test_create_printer_with_loaded_filaments tests/test_printers.py::test_patch_loaded_filaments tests/test_printers.py::test_loaded_filaments_null_filament_id_roundtrips -v
```

Expected: 4 PASSED.

- [ ] **Step 6: Run the full backend test suite and verify nothing regressed**

```bash
cd backend && pytest -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/app/api/routes/printers.py backend/tests/test_printers.py
git commit -m "feat(backend): add loaded_filaments to Printer model and API"
```

---

### Task 3: Frontend — types + API module

**Files:**
- Modify: `frontend/src/api/printers.ts`

- [ ] **Step 1: Add the `LoadedFilament` interface and extend `ApiPrinter` and `UpdatePrinterBody`**

Open `frontend/src/api/printers.ts`. Replace the file contents with the following (adds `LoadedFilament`, extends `ApiPrinter` and `UpdatePrinterBody`):

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

export interface LoadedFilament {
  slot: number;
  filament_id: string | null;
  name: string;
  type: string;
  color: string;
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
  loaded_filaments: LoadedFilament[];
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
  loaded_filaments?: LoadedFilament[];
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

- [ ] **Step 2: Verify the TypeScript types compile**

```bash
cd frontend && npm run build
```

Expected: build succeeds with no type errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/printers.ts
git commit -m "feat(frontend): add LoadedFilament type and extend ApiPrinter"
```

---

### Task 4: Frontend — UI (TDD)

**Files:**
- Modify: `frontend/src/screens/PrintersScreen.tsx`
- Modify: `frontend/src/screens/PrintersScreen.test.tsx`

The UI has two surfaces:
1. **Printer table row** — a "Filament" column with read-only color swatches (`SlotSwatch` component). Shows `"— no filament"` when `loaded_filaments` is empty.
2. **`EditForm`** — a "Loaded filaments" section with per-slot rows (color picker, type select, name input, remove button) and an "Add slot" button. Slot list is submitted with the existing PATCH on save.

- [ ] **Step 1: Update the mock data in the test file to include `loaded_filaments`**

In `frontend/src/screens/PrintersScreen.test.tsx`, update `mockPrinters` to include `loaded_filaments: []`:

```typescript
const mockPrinters = [
  {
    id: 1,
    name: 'Forge',
    printer_type: 'bambu',
    connection_config: { ip_address: '192.168.1.100', access_code: '12345678', serial_number: 'SN001' },
    awaiting_plate_clear: false,
    orca_printer_profiles: [],
    current_orca_printer_profile: null,
    enabled: true,
    connected: true,
    loaded_filaments: [],
  },
];
```

- [ ] **Step 2: Write the five failing tests**

Add the following tests inside the existing `describe('PrintersScreen', ...)` block, after the existing tests:

```typescript
  it('shows no-filament placeholder when loaded_filaments is empty', async () => {
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => expect(screen.getByText(/— no filament/i)).toBeTruthy());
  });

  it('shows filament swatch when loaded_filaments is populated', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.includes('/types')) return Promise.resolve({ ok: true, json: () => Promise.resolve(mockTypes) });
      return Promise.resolve({ ok: true, json: () => Promise.resolve([{
        ...mockPrinters[0],
        loaded_filaments: [{ slot: 0, filament_id: null, name: 'Bambu PLA', type: 'PLA', color: '#ff0000' }],
      }]) });
    }));
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => expect(document.querySelector('[title="Bambu PLA (PLA)"]')).toBeTruthy());
  });

  it('shows loaded filaments section in edit form', async () => {
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => screen.getByText('Forge'));
    await user.click(screen.getByRole('button', { name: /edit/i }));
    await waitFor(() => expect(screen.getByText(/loaded filaments/i)).toBeTruthy());
  });

  it('can add a slot in the edit form', async () => {
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => screen.getByText('Forge'));
    await user.click(screen.getByRole('button', { name: /edit/i }));
    await waitFor(() => screen.getByText(/loaded filaments/i));
    await user.click(screen.getByRole('button', { name: /add slot/i }));
    await waitFor(() => expect(screen.getByPlaceholderText(/filament name/i)).toBeTruthy());
  });

  it('PATCH includes loaded_filaments on save', async () => {
    const calls: [string, RequestInit | undefined][] = [];
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      calls.push([url, init]);
      if (url.includes('/types')) return Promise.resolve({ ok: true, json: () => Promise.resolve(mockTypes) });
      if (init?.method === 'PATCH') return Promise.resolve({ ok: true, json: () => Promise.resolve({ ...mockPrinters[0] }) });
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockPrinters) });
    }));
    const user = userEvent.setup();
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => screen.getByText('Forge'));
    await user.click(screen.getByRole('button', { name: /edit/i }));
    await waitFor(() => screen.getByText(/loaded filaments/i));
    await user.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => {
      const patchCall = calls.find(([, init]) => init?.method === 'PATCH');
      expect(patchCall).toBeTruthy();
      const body = JSON.parse(patchCall![1]!.body as string);
      expect(body).toHaveProperty('loaded_filaments');
      expect(Array.isArray(body.loaded_filaments)).toBe(true);
    });
  });

  it('renders slot with filament_id null without error', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.includes('/types')) return Promise.resolve({ ok: true, json: () => Promise.resolve(mockTypes) });
      return Promise.resolve({ ok: true, json: () => Promise.resolve([{
        ...mockPrinters[0],
        loaded_filaments: [{ slot: 0, filament_id: null, name: 'Generic PLA', type: 'PLA', color: '#888888' }],
      }]) });
    }));
    render(<PrintersScreen />, { wrapper });
    await waitFor(() => expect(document.querySelector('[title="Generic PLA (PLA)"]')).toBeTruthy());
  });
```

- [ ] **Step 3: Run the new tests and verify they fail**

```bash
cd frontend && npx vitest run src/screens/PrintersScreen.test.tsx
```

Expected: the 6 new tests FAIL (components don't have swatches or the loaded filaments section yet).

- [ ] **Step 4: Implement the UI in `PrintersScreen.tsx`**

Open `frontend/src/screens/PrintersScreen.tsx`. Make the following changes:

**4a. Add import for `LoadedFilament` at the top** — update the existing import from `../api/printers`:

```typescript
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
  type LoadedFilament,
} from '../api/printers';
```

**4b. Add `SlotSwatch` component** — add this after the imports, before the `ConnStatus` type alias:

```typescript
const MAT_TYPES = ['PLA', 'PETG', 'ABS', 'ASA', 'PA-CF', 'PC', 'TPU', 'Other'] as const;

function SlotSwatch({ color, name, type }: { color: string; name: string; type: string }) {
  return (
    <div
      title={`${name} (${type})`}
      style={{
        width: 16,
        height: 16,
        borderRadius: '50%',
        background: color,
        border: '1px solid var(--border-2)',
        flexShrink: 0,
        display: 'inline-block',
      }}
    />
  );
}
```

**4c. Update `EditForm`** — add slot state + loaded filaments section. The full updated component:

```typescript
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
  const [slots, setSlots] = useState<LoadedFilament[]>(printer.loaded_filaments ?? []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function addSlot() {
    setSlots(s => [...s, { slot: s.length, filament_id: null, name: '', type: 'PLA', color: '#888888' }]);
  }

  function removeSlot(i: number) {
    setSlots(s => s.filter((_, idx) => idx !== i).map((x, idx) => ({ ...x, slot: idx })));
  }

  function updateSlot(i: number, patch: Partial<LoadedFilament>) {
    setSlots(s => s.map((x, idx) => idx === i ? { ...x, ...patch } : x));
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await updatePrinter(printer.id, { name, connection_config: config, loaded_filaments: slots });
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
        <div>
          <div className="label" style={{ marginBottom: 8 }}>Loaded filaments</div>
          <div className="col gap-2">
            {slots.map((s, i) => (
              <div key={i} className="row gap-2" style={{ alignItems: 'center' }}>
                <span className="tiny muted" style={{ width: 44, flexShrink: 0 }}>Slot {i + 1}</span>
                <input
                  type="color"
                  value={s.color}
                  onChange={e => updateSlot(i, { color: e.target.value })}
                  style={{
                    width: 32, height: 32, padding: 2,
                    border: '1px solid var(--border-1)', borderRadius: 6,
                    cursor: 'pointer', background: 'var(--bg-2)',
                  }}
                />
                <select
                  className="input"
                  style={{ width: 90, flexShrink: 0 }}
                  value={s.type}
                  onChange={e => updateSlot(i, { type: e.target.value })}>
                  {MAT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
                <input
                  className="input"
                  style={{ flex: 1 }}
                  placeholder="Filament name"
                  value={s.name}
                  onChange={e => updateSlot(i, { name: e.target.value })}
                />
                <button
                  className="btn ghost icon sm"
                  onClick={() => removeSlot(i)}
                  title="Remove slot">
                  {Icons.x}
                </button>
              </div>
            ))}
            <button
              className="btn ghost sm"
              onClick={addSlot}
              style={{ alignSelf: 'flex-start' }}>
              {Icons.plus} Add slot
            </button>
          </div>
        </div>
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
```

**4d. Add "Filament" column to the table header** — locate the `<thead>` block and add the column between "Connection" and "Status":

```typescript
            <thead>
              <tr>
                <th>Printer</th>
                <th>Type</th>
                <th>Connection</th>
                <th>Filament</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
```

**4e. Add filament cell to each table row** — in the `<tr>` for each printer (the one with `opacity: p.enabled ? 1 : 0.5`), add a `<td>` after the Connection `<td>` and before the Status `<td>`:

```typescript
                    <td>
                      {p.loaded_filaments.length === 0 ? (
                        <span className="tiny muted">— no filament</span>
                      ) : (
                        <div className="row gap-1" style={{ alignItems: 'center' }}>
                          {p.loaded_filaments.map(s => (
                            <SlotSwatch key={s.slot} color={s.color} name={s.name} type={s.type} />
                          ))}
                        </div>
                      )}
                    </td>
```

Also update the `<td colSpan={5}` on the edit row to `colSpan={6}` (one more column now):

```typescript
                      <td colSpan={6} style={{ padding: '0 16px 16px' }}>
```

- [ ] **Step 5: Run the tests and verify they pass**

```bash
cd frontend && npx vitest run src/screens/PrintersScreen.test.tsx
```

Expected: all tests pass, including the 6 new ones.

- [ ] **Step 6: Run the full frontend test suite and verify nothing regressed**

```bash
cd frontend && npx vitest run
```

Expected: all tests pass.

- [ ] **Step 7: Run a TypeScript type check**

```bash
cd frontend && npm run build
```

Expected: build succeeds with no errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/screens/PrintersScreen.tsx frontend/src/screens/PrintersScreen.test.tsx
git commit -m "feat(frontend): filament slot editor and read-only swatches on printer table"
```

---

### Task 5: Open PR

**Files:** none

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/filament-tracking
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --base main --title "feat: track loaded filaments per printer slot" --body "$(cat <<'EOF'
## Summary

- Adds `loaded_filaments` JSON column to `Printer` model — a list of slot objects `{ slot, filament_id, name, type, color }`
- `filament_id` is nullable (hybrid design: links to the future filament library, falls back to inline fields)
- Backend: `PrinterCreate` and `PrinterUpdate` accept `loaded_filaments`; `_to_dict` includes it in all responses
- Frontend: printer table row shows read-only color swatches per slot; `EditForm` gains a slot editor (color picker, type select, name input, add/remove slots)
- Closes: filament tracking feature (multi-slot list supports future AMS-style hardware)

## Test plan

- [ ] Backend: 4 new tests in `test_printers.py` (defaults, create with slots, PATCH, null filament_id round-trip)
- [ ] Frontend: 6 new tests in `PrintersScreen.test.tsx` (placeholder, swatch, edit section visible, add slot, PATCH payload, null filament_id renders)
- [ ] Manual: add a printer, open Edit, add a filament slot, save — verify swatch appears on the table row
- [ ] Manual: set `filament_id` to null (default) — verify no error

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Note the PR URL for the user to review**

Print the PR URL from the output of `gh pr create`. Do **not** merge — leave open for manual testing.
