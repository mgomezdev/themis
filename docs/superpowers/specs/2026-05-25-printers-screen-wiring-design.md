# Printers Screen Wiring — Design Spec

**Date:** 2026-05-25
**Issue:** [#9 — feat: wire printers screen to printer CRUD + connection test API](https://github.com/mgomezdev/themis/issues/9)

---

## Overview

Wire the Printers management screen to the backend REST API, replacing all mock data with live CRUD. Redesign the add-printer wizard to be data-driven from the backend's printer type registry. Add a `test-connection` backend endpoint that exercises the AbstractPrinterClient ABC.

---

## Backend Changes

### 1. Augment `_to_dict` with live connection status

`backend/app/api/routes/printers.py` — add `connected: bool` to the dict returned by `_to_dict`. Pull from `printer_manager._clients`: if a live client exists for the printer's ID and `client.connected` is True, `connected = True`; otherwise `False`.

```python
def _to_dict(p: Printer) -> dict:
    client = printer_manager._clients.get(p.id)
    return {
        ...existing fields...,
        "connected": client.connected if client else False,
    }
```

### 2. `create_client_from_config` helper

`backend/app/services/printer_client_factory.py` — add a thin helper that accepts `printer_type: str` and `connection_config: dict` directly (without a DB row), so `test-connection` doesn't duplicate the instantiation logic in `create_client`.

```python
def create_client_from_config(printer_type: str, connection_config: dict) -> AbstractPrinterClient:
    cls = _load_class(printer_type)
    accepted = {f.name for f in cls.connection_fields()}
    kwargs = {k: v for k, v in connection_config.items() if k in accepted}
    return cls(**kwargs)
```

### 3. `POST /api/v1/printers/test-connection`

New endpoint in `backend/app/api/routes/printers.py`. Accepts `{ printer_type, connection_config }`. Uses `create_client_from_config` to instantiate a throwaway client, calls `connect()`, waits up to 5 seconds, checks `client.connected`, then calls `disconnect()`. Returns `{ ok: bool, error?: str }`. Does not write to the database.

Must be registered **before** the `/{printer_id}` routes to avoid the path being swallowed by the integer param.

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
        return {"ok": ok} if ok else {"ok": False, "error": "Could not connect"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        client.disconnect()
```

---

## Frontend Changes

### 1. `frontend/src/api/printers.ts` — new file

Typed fetch wrappers. All throw on non-2xx.

```ts
export interface ConnectionField {
  name: string; label: string; field_type: 'text' | 'password' | 'number';
  required: boolean; default: string | number | null; placeholder: string; help_text: string;
}
export interface PrinterType {
  printer_type: string; display_name: string; connection_fields: ConnectionField[];
}
export interface ApiPrinter {
  id: number; name: string; printer_type: string; connection_config: Record<string, unknown>;
  awaiting_plate_clear: boolean; orca_printer_profiles: string[];
  current_orca_printer_profile: string | null; enabled: boolean; connected: boolean;
}

export const fetchPrinterTypes = (): Promise<PrinterType[]>
export const fetchPrinters = (): Promise<ApiPrinter[]>
export const createPrinter = (body: {...}): Promise<ApiPrinter>
export const updatePrinter = (id: number, body: {...}): Promise<ApiPrinter>
export const deletePrinter = (id: number): Promise<void>
export const testConnection = (body: {...}): Promise<{ ok: boolean; error?: string }>
```

Base URL is `/api/v1/printers`.

### 2. `PrintersScreen.tsx` — table wiring

- Replace `import { PRINTERS } from '../data/mock'` with `useState<ApiPrinter[]>([])` + `useEffect` that calls `fetchPrinters()` on mount.
- Loading state: show a spinner row while fetching.
- Error state: show an inline error banner with a "Retry" button.
- Empty state: "No printers yet — add one to get started."
- Column mapping from `ApiPrinter`:
  - **Printer** → `name` + `id`
  - **Type** → `printer_type` (display name via `PrinterType.display_name` from fetched types)
  - **Connection** → key fields from `connection_config` (e.g. IP)
  - **Status** → `connected` → "Online"/"Offline" pill; disabled row if `enabled === false`
- Header subtitle derived: `"${onlineCount} connected · ${offlineCount} offline"`
- Edit button: opens an inline edit form pre-populated with `name` + `connection_config` fields; submits `updatePrinter(id, ...)`, refreshes list on success.
- Delete: confirm dialog → `deletePrinter(id)` → refresh list.

### 3. `PrinterAddForm` — wizard redesign

**Step 1 — Type + nickname:**
- On mount, call `fetchPrinterTypes()`. Show tiles for each returned type (display_name).
- Nickname text field unchanged.

**Step 2 — Connection + test:**
- Render `connection_fields[]` from the selected `PrinterType` dynamically. Each field maps `field_type` → `<input type="text|password|number">`.
- "Test connection" button calls `testConnection({ printer_type, connection_config })`. Shows inline success/error.
- "Next" is always enabled (test is optional but encouraged).

**Step 3 — Review + finish:**
- Summary card showing type, nickname, and connection details.
- "Finish" calls `createPrinter(...)`. On success, closes wizard and refreshes the printer list.
- On error, shows inline error message and stays on step 3.

The existing capabilities step is removed — no corresponding backend field.

---

## Tests

### Backend (`tests/test_printers.py`)

- `test_test_connection_unknown_type` — POST with unknown `printer_type` → 422
- `test_list_printers_includes_connected` — GET list includes `connected: false` for a printer with no live client

### Frontend (`PrintersScreen.test.tsx`)

- Mock `fetch` via `vi.stubGlobal('fetch', ...)` returning a canned `ApiPrinter[]`
- Replace "renders Atlas/Forge" with "renders fetched printer name"
- Wizard step 1 shows type display names from mocked `fetchPrinterTypes` response
- Test connection button calls `/api/v1/printers/test-connection`
- Finish button calls `POST /api/v1/printers` and wizard closes

---

## Out of Scope

- OrcaSlicer profile assignment in the wizard (can be added via Edit after creation)
- Capabilities / material tags (no backend field; deferred)
- Real-time status updates in the printers table (covered by issue #13 WebSocket work)
