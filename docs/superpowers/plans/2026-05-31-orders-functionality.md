# Orders Functionality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make orders a real, persisted feature — backend `orders` table + CRUD API, a one-order-per-job link, order status/progress derived from linked jobs, and rewired Orders/New-Order/New-Job frontend screens.

**Architecture:** A new `orders` table stores order metadata + a JSON parts bill-of-materials (no per-part fulfillment tracking). Each job links to at most one order via a renamed `jobs.order_id` FK. Order status (`queued`/`in_progress`/`complete`/`hold`) and progress are computed on read from the statuses of linked jobs, with a manual `on_hold` override. The frontend Orders screens swap mock data for the new API; New Job links jobs to a single order.

**Tech Stack:** FastAPI, async SQLAlchemy 2.0 + aiosqlite (SQLite/WAL), pytest-asyncio + httpx; React 18 + Vite + TypeScript, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-05-31-orders-functionality-design.md`

---

## File Structure

**Backend**
- Modify `backend/app/models.py` — add `Order`; change `Job.project_id` → `Job.order_id`; remove `Project`.
- Modify `backend/app/database.py` — add `order_id` column in `_migrate`.
- Create `backend/app/api/routes/orders.py` — orders CRUD + derived status/progress.
- Delete `backend/app/api/routes/projects.py`.
- Modify `backend/app/api/routes/jobs.py` — `order_id` in `JobCreate`, validation, `_to_dict`.
- Modify `backend/app/api/routes/queue.py` — `order_id` in `_to_dict`.
- Modify `backend/app/main.py` — swap `projects_router` for `orders_router`.
- Create `backend/tests/api/test_orders_api.py`.
- Delete `backend/tests/api/test_projects_api.py`.
- Modify `backend/tests/api/test_jobs_api.py` — `order_id` round-trip.

**Frontend**
- Create `frontend/src/api/orders.ts` — types, CRUD functions, `useOrders` hook.
- Create `frontend/src/api/orders.test.ts`.
- Rewrite `frontend/src/screens/OrdersScreen.tsx` — real data, trimmed parts table, hold/edit/delete.
- Rewrite `frontend/src/screens/NewOrderScreen.tsx` — create + edit, no Suggested-plates panel.
- Modify `frontend/src/screens/NewJobScreen.tsx` — single-select real-order picker, send `order_id`.
- Modify `frontend/src/App.tsx` — real `ordersOpen` count, `/orders/:id/edit` route, wire topbar "New order".
- Modify `frontend/src/screens/OrdersScreen.test.tsx`, `frontend/src/screens/NewOrderScreen.test.tsx`.

---

## Task 1: Backend schema — `Order` model, `Job.order_id`, migration

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/database.py:33-51`
- Modify: `backend/app/api/routes/jobs.py`
- Modify: `backend/app/api/routes/queue.py:26-38`
- Test: `backend/tests/api/test_jobs_api.py`

- [ ] **Step 1: Update `test_jobs_api.py` to use `order_id`**

In `backend/tests/api/test_jobs_api.py`, in `test_create_job`, change the payload key `"project_id": None` to `"order_id": None`, and add one assertion after `assert data["id"] is not None`:

```python
    assert data["order_id"] is None
```

(The job→order link with a real order is exercised in Task 2's `test_orders_api.py::test_status_in_progress_with_job`, since it needs the orders route.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/api/test_jobs_api.py::test_create_job -v`
Expected: FAIL — `KeyError`/assertion on `data["order_id"]` because `_to_dict` still emits `project_id`.

- [ ] **Step 3: Edit `models.py` — add `Order`, switch `Job` FK, drop `Project`**

In `backend/app/models.py`, delete the entire `Project` class (lines 32-38). Replace the `Job.project_id` line with `order_id`, and add an `Order` class. The `Job` class becomes:

```python
class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    uploaded_file_id: Mapped[int] = mapped_column(ForeignKey("uploaded_files.id"))
    plate_number: Mapped[int] = mapped_column(default=1)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id"), nullable=True)
    assigned_printer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("printers.id"), nullable=True)
    queue_position: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    block_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))
```

Add this class (place it just above `Job`, so the `orders` table is declared before `jobs`):

```python
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_type: Mapped[str] = mapped_column(String(20))  # "customer" | "internal"
    customer: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    due_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    on_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    parts: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))
```

- [ ] **Step 4: Edit `database.py` `_migrate` — add `order_id` to existing DBs**

In `backend/app/database.py`, inside `_migrate`, append after the `block_reason` block (line 51):

```python
    if "order_id" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN order_id INTEGER"))
```

(The new `orders` table is created by `create_all`; only the `jobs` column needs an explicit `ALTER`. The old `project_id` column physically remains in pre-existing DBs but is unmapped and harmless.)

- [ ] **Step 5: Edit `jobs.py` — `order_id` everywhere `project_id` appeared**

In `backend/app/api/routes/jobs.py`:

In `JobCreate`, change `project_id: int | None = None` to `order_id: int | None = None`.

In `_to_dict`, change `"project_id": j.project_id,` to `"order_id": j.order_id,`.

In `create_job`, change the `Job(...)` construction line `project_id=body.project_id,` to `order_id=body.order_id,`. Immediately before building the `Job`, add order validation (after the `for cfg in body.printer_configs:` validation loop, before `now = ...`):

```python
    if body.order_id is not None:
        from ...models import Order
        order = await session.get(Order, body.order_id)
        if order is None:
            raise HTTPException(404, f"Order {body.order_id} not found")
```

- [ ] **Step 6: Edit `queue.py` `_to_dict`**

In `backend/app/api/routes/queue.py`, change `"project_id": j.project_id,` to `"order_id": j.order_id,`.

- [ ] **Step 7: Run the jobs + queue suites**

Run: `cd backend && pytest tests/api/test_jobs_api.py tests/api/test_queue_api.py -v`
Expected: all PASS — `test_create_job` now sees `order_id`, and the queue tests tolerate the renamed field.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models.py backend/app/database.py backend/app/api/routes/jobs.py backend/app/api/routes/queue.py backend/tests/api/test_jobs_api.py
git commit -m "feat(orders): add Order model and job order_id link"
```

---

## Task 2: Backend — Orders CRUD API with derived status/progress

**Files:**
- Create: `backend/app/api/routes/orders.py`
- Modify: `backend/app/main.py:14,68` (swap router)
- Test: `backend/tests/api/test_orders_api.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/api/test_orders_api.py`:

```python
# backend/tests/api/test_orders_api.py
import io
import json
import zipfile
from unittest.mock import patch


def _make_3mf() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/slice_info.config", json.dumps({
            "plate": [{"index": 1, "prediction": 60, "weight": [5.0]}]
        }))
        zf.writestr("Metadata/plate_1.png", b"\x89PNG")
    return buf.getvalue()


async def _create_order(client, **over):
    body = {
        "order_type": "customer", "customer": "Vela Robotics",
        "title": "Brackets", "due_date": "2026-06-01", "notes": "match black",
        "parts": [{"name": "Arm L", "qty": 8, "material": "PA-CF", "est_minutes": 78}],
    }
    body.update(over)
    return await client.post("/api/v1/orders", json=body)


async def _make_job(client, tmp_path, order_id, status="queued"):
    with patch("app.api.routes.files.get_data_dir", return_value=tmp_path):
        f = await client.post("/api/v1/files/upload",
                              files={"file": ("m.3mf", _make_3mf(), "application/octet-stream")})
    file_id = f.json()["id"]
    p = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu", "connection_config": {},
        "orca_printer_profiles": ["X"], "current_orca_printer_profile": "X"})
    printer_id = p.json()["id"]
    with patch("app.api.routes.jobs.queue_engine"):
        j = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1, "order_id": order_id,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm"}]})
    return j.json()["id"]


async def test_create_order(client):
    resp = await _create_order(client)
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] is not None
    assert data["customer"] == "Vela Robotics"
    assert data["status"] == "queued"
    assert data["progress"] == 0.0
    assert data["job_count"] == 0
    assert data["parts"][0]["name"] == "Arm L"
    assert data["parts"][0]["id"]  # server-assigned part id


async def test_list_orders_empty(client):
    resp = await client.get("/api/v1/orders")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_order_not_found(client):
    assert (await client.get("/api/v1/orders/9999")).status_code == 404


async def test_status_in_progress_with_job(client, tmp_path):
    oid = (await _create_order(client)).json()["id"]
    await _make_job(client, tmp_path, oid)
    data = (await client.get(f"/api/v1/orders/{oid}")).json()
    assert data["status"] == "in_progress"
    assert data["job_count"] == 1
    assert data["progress"] == 0.0
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["plate_number"] == 1


async def test_hold_override(client):
    oid = (await _create_order(client)).json()["id"]
    resp = await client.patch(f"/api/v1/orders/{oid}", json={"on_hold": True})
    assert resp.status_code == 200
    assert resp.json()["status"] == "hold"


async def test_patch_replaces_parts(client):
    oid = (await _create_order(client)).json()["id"]
    resp = await client.patch(f"/api/v1/orders/{oid}", json={
        "parts": [{"name": "Clamp", "qty": 4, "material": "PETG", "est_minutes": 12}]})
    assert resp.status_code == 200
    parts = resp.json()["parts"]
    assert len(parts) == 1 and parts[0]["name"] == "Clamp" and parts[0]["id"]


async def test_delete_nulls_job_link(client, tmp_path):
    oid = (await _create_order(client)).json()["id"]
    job_id = await _make_job(client, tmp_path, oid)
    assert (await client.delete(f"/api/v1/orders/{oid}")).status_code == 204
    assert (await client.get(f"/api/v1/orders/{oid}")).status_code == 404
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["order_id"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && pytest tests/api/test_orders_api.py -v`
Expected: FAIL (404s — `/api/v1/orders` not registered).

- [ ] **Step 3: Create `orders.py`**

Create `backend/app/api/routes/orders.py`:

```python
from __future__ import annotations
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Job, Order

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


class OrderPartIn(BaseModel):
    id: str | None = None
    name: str
    qty: int = 1
    material: str = ""
    est_minutes: int = 0


class OrderCreate(BaseModel):
    order_type: str
    customer: str
    title: str
    due_date: str | None = None
    notes: str | None = None
    parts: list[OrderPartIn] = []


class OrderPatch(BaseModel):
    order_type: str | None = None
    customer: str | None = None
    title: str | None = None
    due_date: str | None = None
    notes: str | None = None
    on_hold: bool | None = None
    parts: list[OrderPartIn] | None = None


def _normalize_parts(parts: list[OrderPartIn]) -> list[dict]:
    out = []
    for p in parts:
        out.append({
            "id": p.id or uuid.uuid4().hex,
            "name": p.name,
            "qty": p.qty,
            "material": p.material,
            "est_minutes": p.est_minutes,
        })
    return out


async def _derive(session: AsyncSession, order: Order) -> tuple[str, float, int]:
    result = await session.execute(select(Job.status).where(Job.order_id == order.id))
    statuses = [r[0] for r in result.all()]
    active = [s for s in statuses if s != "cancelled"]
    completed = [s for s in active if s == "complete"]
    if order.on_hold:
        status = "hold"
    elif not active:
        status = "queued"
    elif all(s == "complete" for s in active):
        status = "complete"
    else:
        status = "in_progress"
    progress = (len(completed) / len(active)) if active else 0.0
    return status, round(progress, 4), len(active)


async def _to_dict(session: AsyncSession, o: Order, with_jobs: bool = False) -> dict:
    status, progress, job_count = await _derive(session, o)
    data = {
        "id": o.id,
        "order_type": o.order_type,
        "customer": o.customer,
        "title": o.title,
        "due_date": o.due_date,
        "notes": o.notes,
        "on_hold": o.on_hold,
        "parts": o.parts or [],
        "status": status,
        "progress": progress,
        "job_count": job_count,
        "created_at": o.created_at,
        "updated_at": o.updated_at,
    }
    if with_jobs:
        result = await session.execute(
            select(Job).where(Job.order_id == o.id, Job.status != "cancelled")
            .order_by(Job.queue_position)
        )
        data["jobs"] = [
            {
                "id": j.id,
                "status": j.status,
                "plate_number": j.plate_number,
                "uploaded_file_id": j.uploaded_file_id,
                "queue_position": j.queue_position,
            }
            for j in result.scalars().all()
        ]
    return data


async def _get_or_404(order_id: int, session: AsyncSession) -> Order:
    o = await session.get(Order, order_id)
    if o is None:
        raise HTTPException(404, f"Order {order_id} not found")
    return o


@router.get("")
async def list_orders(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(select(Order).order_by(Order.created_at.desc()))
    return [await _to_dict(session, o) for o in result.scalars().all()]


@router.post("", status_code=201)
async def create_order(body: OrderCreate, session: AsyncSession = Depends(get_session)) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    order = Order(
        order_type=body.order_type,
        customer=body.customer,
        title=body.title,
        due_date=body.due_date,
        notes=body.notes,
        on_hold=False,
        parts=_normalize_parts(body.parts),
        created_at=now,
        updated_at=now,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return await _to_dict(session, order, with_jobs=True)


@router.get("/{order_id}")
async def get_order(order_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    return await _to_dict(session, await _get_or_404(order_id, session), with_jobs=True)


@router.patch("/{order_id}")
async def patch_order(order_id: int, body: OrderPatch,
                      session: AsyncSession = Depends(get_session)) -> dict:
    order = await _get_or_404(order_id, session)
    fields = body.model_dump(exclude_unset=True)
    if "parts" in fields and fields["parts"] is not None:
        order.parts = _normalize_parts([OrderPartIn(**p) for p in fields.pop("parts")])
    else:
        fields.pop("parts", None)
    for k, v in fields.items():
        setattr(order, k, v)
    order.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    await session.refresh(order)
    return await _to_dict(session, order, with_jobs=True)


@router.delete("/{order_id}", status_code=204)
async def delete_order(order_id: int, session: AsyncSession = Depends(get_session)) -> None:
    order = await _get_or_404(order_id, session)
    await session.execute(update(Job).where(Job.order_id == order_id).values(order_id=None))
    await session.delete(order)
    await session.commit()
```

- [ ] **Step 4: Register the router; remove the projects router**

In `backend/app/main.py`: change the import line `from .api.routes.projects import router as projects_router` to `from .api.routes.orders import router as orders_router`, and change `app.include_router(projects_router)` to `app.include_router(orders_router)`.

- [ ] **Step 5: Run the orders + jobs suites**

Run: `cd backend && pytest tests/api/test_orders_api.py tests/api/test_jobs_api.py -v`
Expected: all PASS (including `test_create_job_links_order` from Task 1).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/orders.py backend/app/main.py backend/tests/api/test_orders_api.py
git commit -m "feat(orders): orders CRUD API with job-derived status"
```

---

## Task 3: Backend — retire the `projects` route

**Files:**
- Delete: `backend/app/api/routes/projects.py`
- Delete: `backend/tests/api/test_projects_api.py`

- [ ] **Step 1: Delete the files**

```bash
git rm backend/app/api/routes/projects.py backend/tests/api/test_projects_api.py
```

(The `Project` model and `projects_router` wiring were already removed in Tasks 1–2. The `projects` table is no longer declared, so it simply stops being created in fresh DBs.)

- [ ] **Step 2: Run the full backend suite**

Run: `cd backend && pytest -q`
Expected: all PASS, no references to `projects` remain.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore(orders): remove superseded projects route"
```

---

## Task 4: Frontend — `api/orders.ts` client + `useOrders` hook

**Files:**
- Create: `frontend/src/api/orders.ts`
- Test: `frontend/src/api/orders.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/api/orders.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getOrders, getOrder, createOrder, updateOrder, deleteOrder } from './orders';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

function mockOk(body: unknown) {
  mockFetch.mockResolvedValueOnce({
    ok: true, status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

beforeEach(() => vi.clearAllMocks());

describe('orders api', () => {
  it('getOrders fetches the list', async () => {
    mockOk([]);
    const r = await getOrders();
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/orders');
    expect(Array.isArray(r)).toBe(true);
  });

  it('getOrder fetches one', async () => {
    mockOk({ id: 7, jobs: [] });
    const r = await getOrder(7);
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/orders/7');
    expect(r.id).toBe(7);
  });

  it('createOrder POSTs', async () => {
    mockOk({ id: 1 });
    await createOrder({ order_type: 'customer', customer: 'A', title: 'T', due_date: null, notes: null, parts: [] });
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/orders', expect.objectContaining({ method: 'POST' }));
  });

  it('updateOrder PATCHes', async () => {
    mockOk({ id: 1, on_hold: true });
    await updateOrder(1, { on_hold: true });
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/orders/1', expect.objectContaining({ method: 'PATCH' }));
  });

  it('deleteOrder DELETEs', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 204, text: () => Promise.resolve(''), json: () => Promise.resolve(null) });
    await deleteOrder(1);
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/orders/1', expect.objectContaining({ method: 'DELETE' }));
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/api/orders.test.ts`
Expected: FAIL (`./orders` not found).

- [ ] **Step 3: Implement `api/orders.ts`**

Create `frontend/src/api/orders.ts`:

```ts
import { useState, useEffect, useCallback } from 'react';

export type OrderType = 'customer' | 'internal';

export interface ApiOrderPart {
  id: string;
  name: string;
  qty: number;
  material: string;
  est_minutes: number;
}

export interface OrderJobSummary {
  id: number;
  status: string;
  plate_number: number;
  uploaded_file_id: number;
  queue_position: number | null;
}

export interface ApiOrder {
  id: number;
  order_type: OrderType;
  customer: string;
  title: string;
  due_date: string | null;
  notes: string | null;
  on_hold: boolean;
  parts: ApiOrderPart[];
  status: string;
  progress: number;       // 0..1
  job_count: number;
  created_at: string;
  updated_at: string;
}

export interface ApiOrderDetail extends ApiOrder {
  jobs: OrderJobSummary[];
}

export interface OrderPartInput {
  id?: string;
  name: string;
  qty: number;
  material: string;
  est_minutes: number;
}

export interface OrderCreateInput {
  order_type: OrderType;
  customer: string;
  title: string;
  due_date: string | null;
  notes: string | null;
  parts: OrderPartInput[];
}

export type OrderPatchInput = Partial<OrderCreateInput & { on_hold: boolean }>;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await (init ? fetch(url, init) : fetch(url));
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
  return resp.json();
}

function jsonInit(method: string, body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

export async function getOrders(): Promise<ApiOrder[]> {
  return request('/api/v1/orders');
}

export async function getOrder(id: number): Promise<ApiOrderDetail> {
  return request(`/api/v1/orders/${id}`);
}

export async function createOrder(body: OrderCreateInput): Promise<ApiOrderDetail> {
  return request('/api/v1/orders', jsonInit('POST', body));
}

export async function updateOrder(id: number, patch: OrderPatchInput): Promise<ApiOrderDetail> {
  return request(`/api/v1/orders/${id}`, jsonInit('PATCH', patch));
}

export async function deleteOrder(id: number): Promise<void> {
  const resp = await fetch(`/api/v1/orders/${id}`, { method: 'DELETE' });
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status} ${text}`);
  }
}

/** Orders list that refetches when jobs change (so derived progress stays live). */
export function useOrders(): { orders: ApiOrder[]; refetch: () => void } {
  const [orders, setOrders] = useState<ApiOrder[]>([]);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick(t => t + 1), []);

  useEffect(() => {
    let alive = true;
    getOrders().then(d => { if (alive) setOrders(d); }).catch(console.error);
    return () => { alive = false; };
  }, [tick]);

  useEffect(() => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${window.location.host}/ws`);
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data) as { type: string };
        if (msg.type === 'job_update' || msg.type === 'queue_update') {
          getOrders().then(d => setOrders(d)).catch(() => {});
        }
      } catch { /* ignore malformed frames */ }
    };
    return () => { ws.close(); };
  }, []);

  return { orders, refetch };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/api/orders.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/orders.ts frontend/src/api/orders.test.ts
git commit -m "feat(orders): frontend orders api client and useOrders hook"
```

---

## Task 5: Frontend — rewrite `OrdersScreen` on real data

**Files:**
- Rewrite: `frontend/src/screens/OrdersScreen.tsx`
- Test: `frontend/src/screens/OrdersScreen.test.tsx`

The new screen: list from `useOrders()`; accordion rows show derived progress (`order.progress * 100`) and `status`; expanding fetches `getOrder(id)` for the **jobs filling this order**; parts table shows **Part / Material / Qty / Est. each** only (no printed/status/remaining columns); per-order **Hold/Unhold**, **Edit** (→ `/orders/:id/edit`), **Delete**.

- [ ] **Step 1: Update the test to drive real data (failing)**

Replace `frontend/src/screens/OrdersScreen.test.tsx` with:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { OrdersScreen } from './OrdersScreen';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);
class FakeWS { onmessage: ((e: MessageEvent) => void) | null = null; close() {} }
vi.stubGlobal('WebSocket', FakeWS as unknown as typeof WebSocket);

const ORDER = {
  id: 1, order_type: 'customer', customer: 'Vela Robotics', title: 'Brackets',
  due_date: '2026-06-01', notes: '', on_hold: false,
  parts: [{ id: 'p1', name: 'Arm L', qty: 8, material: 'PA-CF', est_minutes: 78 }],
  status: 'in_progress', progress: 0.5, job_count: 2, created_at: '', updated_at: '',
};

function mockOk(body: unknown) {
  mockFetch.mockResolvedValue({
    ok: true, status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;
beforeEach(() => vi.clearAllMocks());

describe('OrdersScreen', () => {
  it('renders orders from the api', async () => {
    mockOk([ORDER]);
    render(<OrdersScreen />, { wrapper });
    await waitFor(() => expect(screen.getByText('Vela Robotics')).toBeTruthy());
  });

  it('shows empty state with no orders', async () => {
    mockOk([]);
    render(<OrdersScreen />, { wrapper });
    await waitFor(() => expect(screen.getByText(/no orders/i)).toBeTruthy());
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/screens/OrdersScreen.test.tsx`
Expected: FAIL (current screen reads mock data, makes no fetch; `/no orders/` empty state absent).

- [ ] **Step 3: Rewrite `OrdersScreen.tsx`**

Replace the entire file `frontend/src/screens/OrdersScreen.tsx` with:

```tsx
import { useState, useMemo, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { matColor, fmtTime } from '../data/helpers';
import { StatusPill, Progress, MaterialChip, Empty } from '../components/ui';
import { Icons } from '../components/icons';
import { useFilePlates } from '../api/queue';
import {
  useOrders, getOrder, updateOrder, deleteOrder,
  type ApiOrder, type ApiOrderDetail, type OrderJobSummary,
} from '../api/orders';

type Filter = 'open' | 'all' | 'customer' | 'internal';

function PartsTable({ order }: { order: ApiOrder }) {
  if (order.parts.length === 0) {
    return <div className="tiny muted" style={{ padding: '12px 18px' }}>No parts listed.</div>;
  }
  return (
    <table className="tbl">
      <thead>
        <tr>
          <th style={{ paddingLeft: 18 }}>Part</th>
          <th style={{ width: 140 }}>Material</th>
          <th style={{ width: 90 }}>Qty</th>
          <th style={{ width: 120, textAlign: 'right', paddingRight: 18 }}>Est. each</th>
        </tr>
      </thead>
      <tbody>
        {order.parts.map(p => (
          <tr key={p.id}>
            <td style={{ paddingLeft: 18, fontWeight: 500 }}>{p.name || <span className="muted">unnamed</span>}</td>
            <td><MaterialChip material={p.material} color={matColor(p.material)} /></td>
            <td className="num small">{p.qty}</td>
            <td className="num small" style={{ textAlign: 'right', paddingRight: 18 }}>{fmtTime(p.est_minutes)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function JobsFilling({ jobs }: { jobs: OrderJobSummary[] }) {
  const fileIds = useMemo(() => [...new Set(jobs.map(j => j.uploaded_file_id))], [jobs]);
  const getPlate = useFilePlates(fileIds);
  if (jobs.length === 0) {
    return <div className="tiny muted" style={{ padding: '12px 18px' }}>No jobs linked yet.</div>;
  }
  return (
    <div className="col gap-2" style={{ padding: '12px 18px' }}>
      {jobs.map(j => {
        const plate = getPlate(j.uploaded_file_id, j.plate_number);
        return (
          <div key={j.id} className="row between" style={{ padding: '10px 12px', background: 'var(--bg-2)', borderRadius: 8, border: '1px solid var(--border-1)' }}>
            <div className="row gap-3" style={{ alignItems: 'center', minWidth: 0 }}>
              <span className="mono tiny muted">#{j.id}</span>
              <div style={{ fontWeight: 500, fontSize: 13 }}>Plate {j.plate_number}</div>
              <StatusPill status={j.status as never} />
            </div>
            <span className="num tiny muted">{plate?.estimated_time ? fmtTime(plate.estimated_time) : '—'}</span>
          </div>
        );
      })}
    </div>
  );
}

function OrderAccordion({ order, expanded, onToggle, onChanged }: {
  order: ApiOrder;
  expanded: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ApiOrderDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const pct = Math.round(order.progress * 100);

  useEffect(() => {
    if (!expanded) return;
    let alive = true;
    getOrder(order.id).then(d => { if (alive) setDetail(d); }).catch(console.error);
    return () => { alive = false; };
  }, [expanded, order.id, order.updated_at]);

  async function toggleHold(e: React.MouseEvent) {
    e.stopPropagation();
    setBusy(true);
    try { await updateOrder(order.id, { on_hold: !order.on_hold }); onChanged(); }
    finally { setBusy(false); }
  }

  async function remove(e: React.MouseEvent) {
    e.stopPropagation();
    if (!window.confirm(`Delete order for ${order.customer}? Linked jobs stay in the queue.`)) return;
    setBusy(true);
    try { await deleteOrder(order.id); onChanged(); }
    finally { setBusy(false); }
  }

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden', borderColor: expanded ? 'var(--border-3)' : 'var(--border-1)' }}>
      <button onClick={onToggle} aria-label={`order-${order.id}`}
              style={{ width: '100%', background: 'transparent', border: 'none', color: 'inherit', textAlign: 'left', padding: '14px 18px', cursor: 'pointer', display: 'block' }}>
        <div aria-hidden="true" className="row gap-4" style={{ alignItems: 'center' }}>
          <div style={{ width: 20, color: 'var(--text-3)', transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 160ms ease', display: 'inline-flex' }}>{Icons.chevR}</div>
          <div className="col" style={{ width: 130, flexShrink: 0 }}>
            <span className="mono tiny muted">#{order.id}</span>
            <span className="tiny" style={{
              padding: '1px 6px', borderRadius: 4, marginTop: 3, alignSelf: 'flex-start',
              background: order.order_type === 'internal' ? 'rgba(99,102,241,0.12)' : 'rgba(56,189,248,0.12)',
              color: order.order_type === 'internal' ? '#a5b4fc' : 'var(--info)', fontWeight: 500,
            }}>{order.order_type === 'internal' ? 'INTERNAL' : 'CUSTOMER'}</span>
          </div>
          <div className="col" style={{ flex: '1 1 0', minWidth: 0 }}>
            <div style={{ fontWeight: 500, fontSize: 14, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{order.title}</div>
            <div className="tiny muted" style={{ marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{order.customer}</div>
          </div>
          <div className="col" style={{ width: 90, flexShrink: 0 }}>
            <span className="tag-key">DUE</span>
            <span className="num small" style={{ marginTop: 2 }}>{order.due_date ? order.due_date.slice(5) : '—'}</span>
          </div>
          <div className="col" style={{ width: 140, flexShrink: 0 }}>
            <div className="row between">
              <span className="tag-key">JOBS</span>
              <span className="num tiny" style={{ color: pct === 100 ? 'var(--ok)' : 'var(--text-2)' }}>{pct}%</span>
            </div>
            <div style={{ marginTop: 6 }}><Progress value={pct} /></div>
          </div>
          <div style={{ width: 110, flexShrink: 0, display: 'flex', justifyContent: 'flex-end' }}>
            <StatusPill status={order.status as never} />
          </div>
        </div>
      </button>

      {expanded && (
        <div style={{ borderTop: '1px solid var(--border-1)', background: 'var(--bg-1)' }}>
          <div className="row between" style={{ padding: '12px 18px', alignItems: 'center' }}>
            <div className="tiny muted">{order.notes || `${order.job_count} job${order.job_count === 1 ? '' : 's'} linked`}</div>
            <div className="row gap-2">
              <button className="btn sm" disabled={busy} onClick={toggleHold}>{order.on_hold ? 'Release hold' : 'Hold'}</button>
              <button className="btn sm" onClick={(e) => { e.stopPropagation(); navigate(`/orders/${order.id}/edit`); }}>{Icons.copy} Edit</button>
              <button className="btn ghost sm" disabled={busy} style={{ color: 'var(--err)' }} onClick={remove}>{Icons.trash} Delete</button>
            </div>
          </div>
          <div style={{ padding: '0 0 8px' }}>
            <div style={{ padding: '0 18px 6px' }}><span className="tag-key">Parts · {order.parts.length}</span></div>
            <PartsTable order={order} />
          </div>
          <div style={{ borderTop: '1px solid var(--border-1)' }}>
            <div style={{ padding: '12px 18px 4px' }}><span className="tag-key">Jobs filling this order</span></div>
            <JobsFilling jobs={detail?.jobs ?? []} />
          </div>
        </div>
      )}
    </div>
  );
}

export function OrdersScreen() {
  const { orders, refetch } = useOrders();
  const [expanded, setExpanded] = useState<number | null>(null);
  const [filter, setFilter] = useState<Filter>('open');

  const filtered = useMemo(() => orders.filter(o => {
    if (filter === 'open') return o.status !== 'complete';
    if (filter === 'customer') return o.order_type === 'customer';
    if (filter === 'internal') return o.order_type === 'internal';
    return true;
  }), [orders, filter]);

  return (
    <div className="col gap-4" style={{ maxWidth: 1200 }}>
      <div className="row gap-2">
        {([['open', 'Open'], ['all', 'All'], ['customer', 'Customer'], ['internal', 'Internal']] as const).map(([k, l]) => (
          <button key={k} onClick={() => setFilter(k)}
                  className={`btn sm ${filter === k ? 'primary' : ''}`}
                  style={filter === k ? undefined : { background: 'transparent', borderColor: 'var(--border-1)' }}>{l}</button>
        ))}
        <div style={{ flex: 1 }} />
        <span className="tiny muted">{filtered.length} orders</span>
      </div>

      {filtered.length === 0 ? (
        <Empty title="No orders" sub="Create one from the New order button." icon={Icons.orders} />
      ) : (
        <div className="col" style={{ gap: 10 }}>
          {filtered.map(o => (
            <OrderAccordion
              key={o.id}
              order={o}
              expanded={expanded === o.id}
              onToggle={() => setExpanded(expanded === o.id ? null : o.id)}
              onChanged={refetch}
            />
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run the screen test**

Run: `cd frontend && npx vitest run src/screens/OrdersScreen.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Typecheck + commit**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors from `OrdersScreen.tsx`/`orders.ts`. (`Icons.orders`, `Icons.chevR`, `Icons.copy`, `Icons.trash` all exist in `components/icons.tsx`.)

```bash
git add frontend/src/screens/OrdersScreen.tsx frontend/src/screens/OrdersScreen.test.tsx
git commit -m "feat(orders): wire OrdersScreen to real api"
```

---

## Task 6: Frontend — rewrite `NewOrderScreen` (create + edit, no suggested plates)

**Files:**
- Rewrite: `frontend/src/screens/NewOrderScreen.tsx`
- Test: `frontend/src/screens/NewOrderScreen.test.tsx`

The new screen creates an order via `createOrder`, or edits an existing one when mounted at `/orders/:id/edit` (preload via `getOrder`, save via `updateOrder`). The **Suggested-plates panel is removed**; the right rail shows a compact totals summary + Create/Save and Cancel.

- [ ] **Step 1: Update the test (failing)**

Replace `frontend/src/screens/NewOrderScreen.test.tsx` with:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { NewOrderScreen } from './NewOrderScreen';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);
const wrapper = ({ children }: { children: React.ReactNode }) => <MemoryRouter>{children}</MemoryRouter>;
beforeEach(() => vi.clearAllMocks());

describe('NewOrderScreen', () => {
  it('renders order type selector', () => {
    render(<NewOrderScreen />, { wrapper });
    expect(screen.getByText('Customer order')).toBeTruthy();
    expect(screen.getByText('Internal project')).toBeTruthy();
  });

  it('starts with an empty part row and can add rows', async () => {
    const user = userEvent.setup();
    render(<NewOrderScreen />, { wrapper });
    const rowsBefore = screen.getAllByRole('row').length;
    await user.click(screen.getAllByRole('button', { name: /add part|add row/i })[0]);
    expect(screen.getAllByRole('row').length).toBeGreaterThan(rowsBefore);
  });

  it('has no suggested plates panel', () => {
    render(<NewOrderScreen />, { wrapper });
    expect(screen.queryByText(/suggested plates/i)).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/screens/NewOrderScreen.test.tsx`
Expected: FAIL — current screen renders "Suggested plates" and seeds mock parts (`Arm bracket — L`), so the "no suggested plates" test fails.

- [ ] **Step 3: Rewrite `NewOrderScreen.tsx`**

Replace the entire file `frontend/src/screens/NewOrderScreen.tsx` with:

```tsx
import { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { matColor, fmtTime } from '../data/helpers';
import { Icons } from '../components/icons';
import { SectionHeader } from '../components/ui';
import { createOrder, updateOrder, getOrder, type OrderType, type OrderPartInput } from '../api/orders';

interface PartRow {
  id?: string;
  name: string;
  material: string;
  qty: number;
  est_minutes: number;
}

const MATERIAL_OPTIONS = ['PLA', 'PETG', 'PLA-CF', 'PA-CF', 'ABS', 'ASA', 'PC', 'TPU'];

function emptyRow(): PartRow {
  return { name: '', material: 'PLA', qty: 1, est_minutes: 30 };
}

export function NewOrderScreen() {
  const navigate = useNavigate();
  const { id } = useParams();
  const editingId = id ? Number(id) : null;

  const [orderType, setOrderType] = useState<OrderType>('customer');
  const [customer, setCustomer] = useState('');
  const [due, setDue] = useState('');
  const [title, setTitle] = useState('');
  const [notes, setNotes] = useState('');
  const [parts, setParts] = useState<PartRow[]>([emptyRow()]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (editingId == null) return;
    getOrder(editingId).then(o => {
      setOrderType(o.order_type);
      setCustomer(o.customer);
      setDue(o.due_date ?? '');
      setTitle(o.title);
      setNotes(o.notes ?? '');
      setParts(o.parts.length ? o.parts.map(p => ({ id: p.id, name: p.name, material: p.material, qty: p.qty, est_minutes: p.est_minutes })) : [emptyRow()]);
    }).catch(e => setError(String(e)));
  }, [editingId]);

  function addPart() { setParts(prev => [...prev, emptyRow()]); }
  function updPart(i: number, patch: Partial<PartRow>) {
    setParts(prev => prev.map((p, idx) => idx === i ? { ...p, ...patch } : p));
  }
  function delPart(i: number) { setParts(prev => prev.filter((_, idx) => idx !== i)); }

  const totalQty = parts.reduce((a, b) => a + (Number(b.qty) || 0), 0);
  const totalTime = parts.reduce((a, b) => a + (Number(b.qty) || 0) * (Number(b.est_minutes) || 0), 0);

  async function handleSubmit() {
    setSaving(true);
    setError(null);
    const payloadParts: OrderPartInput[] = parts
      .filter(p => p.name.trim())
      .map(p => ({ id: p.id, name: p.name, material: p.material, qty: Number(p.qty) || 1, est_minutes: Number(p.est_minutes) || 0 }));
    const body = {
      order_type: orderType,
      customer,
      title,
      due_date: due || null,
      notes: notes || null,
      parts: payloadParts,
    };
    try {
      if (editingId == null) await createOrder(body);
      else await updateOrder(editingId, body);
      navigate('/orders');
    } catch (e) {
      setError(`Failed to save order: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="col gap-4">
      <div className="row gap-2">
        <button className="btn ghost sm" onClick={() => navigate('/orders')}>{Icons.chevL} Orders</button>
        <span className="muted small">/</span>
        <span className="small">{editingId == null ? 'New order' : 'Edit order'}</span>
      </div>

      {error && (
        <div style={{ padding: '10px 14px', borderRadius: 8, background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.3)', color: 'var(--err)', fontSize: 13 }}>{error}</div>
      )}

      <div className="screen-grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) 300px', gap: 18 }}>
        <div className="col gap-4">
          <div className="card" style={{ padding: 20 }}>
            <SectionHeader title="Order info" />
            <div className="row gap-3" style={{ marginBottom: 14 }}>
              {([
                { id: 'customer' as OrderType, label: 'Customer order', sub: 'Goes to a paying customer' },
                { id: 'internal' as OrderType, label: 'Internal project', sub: 'R&D, marketing, spares' },
              ]).map(opt => (
                <button key={opt.id} onClick={() => setOrderType(opt.id)} className="card"
                        style={{ flex: 1, textAlign: 'left', padding: 14, cursor: 'pointer',
                                 background: orderType === opt.id ? 'var(--bg-3)' : 'var(--bg-1)',
                                 borderColor: orderType === opt.id ? 'var(--accent)' : 'var(--border-1)' }}>
                  <div style={{ fontWeight: 500 }}>{opt.label}</div>
                  <div className="tiny muted" style={{ marginTop: 2 }}>{opt.sub}</div>
                </button>
              ))}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12 }}>
              <div>
                <label className="label">{orderType === 'internal' ? 'Project name' : 'Customer'}</label>
                <input className="input" value={customer} onChange={e => setCustomer(e.target.value)}
                       placeholder={orderType === 'internal' ? 'e.g. R&D — reflow oven' : 'e.g. Vela Robotics'} />
              </div>
              <div>
                <label className="label">Due</label>
                <input type="date" className="input" value={due} onChange={e => setDue(e.target.value)} />
              </div>
            </div>

            <div style={{ marginTop: 12 }}>
              <label className="label">Title</label>
              <input className="input" value={title} onChange={e => setTitle(e.target.value)}
                     placeholder="e.g. Mk3 chassis brackets — batch 5" />
            </div>

            <div style={{ marginTop: 12 }}>
              <label className="label">Notes (optional)</label>
              <textarea className="textarea" value={notes} onChange={e => setNotes(e.target.value)}
                        placeholder="Material preferences, finishing, anything special…" />
            </div>
          </div>

          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border-1)' }}>
              <SectionHeader title="Parts to print" sub={`${parts.length} parts · ${totalQty} units total`}
                             actions={<button className="btn sm" onClick={addPart} aria-label="Add row">{Icons.plus} Add row</button>} />
            </div>
            <table className="tbl">
              <thead>
                <tr>
                  <th style={{ width: 40 }}></th>
                  <th>Part name</th>
                  <th style={{ width: 130 }}>Material</th>
                  <th style={{ width: 90 }}>Qty</th>
                  <th style={{ width: 110 }}>Est. each</th>
                  <th style={{ width: 40 }}></th>
                </tr>
              </thead>
              <tbody>
                {parts.map((p, i) => (
                  <tr key={i} style={{ cursor: 'default' }}>
                    <td><div style={{ width: 32, height: 32, borderRadius: 4, background: matColor(p.material), border: '1px solid var(--border-1)', opacity: 0.7 }} /></td>
                    <td><input className="input" placeholder="Part name" value={p.name} onChange={e => updPart(i, { name: e.target.value })} /></td>
                    <td>
                      <select className="select" value={p.material} onChange={e => updPart(i, { material: e.target.value })}>
                        {MATERIAL_OPTIONS.map(m => <option key={m}>{m}</option>)}
                      </select>
                    </td>
                    <td><input className="input num" type="number" min="1" value={p.qty} onChange={e => updPart(i, { qty: Number(e.target.value) })} /></td>
                    <td><input className="input num" type="number" min="0" step="5" value={p.est_minutes} onChange={e => updPart(i, { est_minutes: Number(e.target.value) })} /></td>
                    <td><button className="btn ghost icon sm" aria-label="Remove part" onClick={() => delPart(i)}>{Icons.x}</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ padding: 14, borderTop: '1px solid var(--border-1)' }}>
              <button className="btn sm" onClick={addPart} aria-label="Add part">{Icons.plus} Add part</button>
            </div>
          </div>
        </div>

        <div className="col gap-4">
          <div className="card" style={{ padding: 18 }}>
            <div className="tag-key">Summary</div>
            <div className="row between" style={{ marginTop: 12 }}>
              <span className="tag-key">Parts</span><span className="num small">{parts.filter(p => p.name.trim()).length}</span>
            </div>
            <div className="row between" style={{ marginTop: 6 }}>
              <span className="tag-key">Units</span><span className="num small">{totalQty}</span>
            </div>
            <div className="divider" />
            <div className="row between">
              <span className="tag-key">Total time</span><span className="num small">{totalTime > 0 ? fmtTime(totalTime) : '—'}</span>
            </div>
          </div>

          <div className="card" style={{ padding: 18 }}>
            <button className="btn primary" style={{ width: '100%' }} disabled={saving || !customer.trim() || !title.trim()} onClick={handleSubmit}>
              {Icons.check} {editingId == null ? 'Create order' : 'Save changes'}
            </button>
            <button className="btn ghost sm" style={{ width: '100%', marginTop: 8 }} disabled={saving} onClick={() => navigate('/orders')}>Cancel</button>
            <div className="tiny muted" style={{ marginTop: 10, textAlign: 'center', lineHeight: 1.5 }}>
              Parts are a checklist. Link jobs to this order from New job.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run the screen test**

Run: `cd frontend && npx vitest run src/screens/NewOrderScreen.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/screens/NewOrderScreen.tsx frontend/src/screens/NewOrderScreen.test.tsx
git commit -m "feat(orders): NewOrderScreen create+edit on real api, drop suggested plates"
```

---

## Task 7: Frontend — link jobs to a single order; wire routes + sidebar count

**Files:**
- Modify: `frontend/src/screens/NewJobScreen.tsx`
- Modify: `frontend/src/api/queue.ts:78-89` (`createJob` payload) and `ApiJob` interface
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/screens/NewJobScreen.test.tsx` (verify it still passes)

- [ ] **Step 1: Change `ApiJob` + `createJob` to use `order_id`**

In `frontend/src/api/queue.ts`: in the `ApiJob` interface, change `project_id: number | null;` to `order_id: number | null;`. In `createJob`, change the body type field `project_id?: number | null;` to `order_id?: number | null;`.

Then update `frontend/src/api/queue.test.ts`: in the two job fixtures (in `createJob` and `cancelJob` tests) change `project_id: null` to `order_id: null`.

- [ ] **Step 2: Replace the mock `OrdersPicker` in `NewJobScreen.tsx`**

In `frontend/src/screens/NewJobScreen.tsx`:

(a) Remove the mock import `import { ORDERS } from '../data/mock';` and the `import type { Order } from '../data/types';` line.

(b) Add near the other api imports: `import { useOrders } from '../api/orders';` (the project has `noUnusedLocals` on — import only what each file references).

(c) The per-plate config currently holds `orderIds: string[]`. Change `PlateConfig.orderIds: string[]` to `orderId: number | null;` and update its default in `defaultConfigForPlate` from `orderIds: []` to `orderId: null`. Update `setOrdersForPlate` to set a single id:

```tsx
  function setOrderForPlate(plateId: string, orderId: number | null) {
    setPlateConfig(plateId, { orderId });
  }
```

(d) Replace the entire `OrdersPicker` component with a single-select fetched from the API:

```tsx
function OrdersPicker({ selectedOrderId, onChange }: {
  selectedOrderId: number | null;
  onChange: (id: number | null) => void;
}) {
  const navigate = useNavigate();
  const { orders } = useOrders();
  const open = orders.filter(o => o.status !== 'complete');

  return (
    <div className="col gap-2">
      <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
        {open.map(o => {
          const selected = selectedOrderId === o.id;
          return (
            <button key={o.id} onClick={() => onChange(selected ? null : o.id)}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 8, padding: '6px 12px',
                background: selected ? 'var(--bg-3)' : 'var(--bg-1)',
                border: `1px solid ${selected ? 'var(--accent)' : 'var(--border-1)'}`,
                boxShadow: selected ? '0 0 0 1px var(--accent)' : 'none',
                borderRadius: 999, cursor: 'pointer', color: 'var(--text-1)', fontFamily: 'inherit', fontSize: 12,
              }}>
              <span className="mono tiny" style={{ color: 'var(--text-3)' }}>#{o.id}</span>
              <span style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.customer}</span>
            </button>
          );
        })}
        <button onClick={() => navigate('/orders/new')}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 12px',
                   background: 'transparent', border: '1px dashed var(--border-2)', borderRadius: 999,
                   color: 'var(--text-3)', cursor: 'pointer', fontFamily: 'inherit', fontSize: 12 }}>
          {Icons.plus} New order
        </button>
        {selectedOrderId != null && (
          <button onClick={() => onChange(null)}
            style={{ padding: '6px 10px', background: 'transparent', border: 'none', color: 'var(--text-3)', cursor: 'pointer', fontFamily: 'inherit', fontSize: 12 }}>
            None — standalone job
          </button>
        )}
      </div>
    </div>
  );
}
```

(e) Where `PlateConfigPanel` renders the orders step, change the props it passes. Replace the `onSetOrders`/`config.orderIds` wiring with the single-id version. In `PlateConfigPanel`'s props change `onSetOrders: (ids: string[]) => void;` to `onSetOrder: (id: number | null) => void;`, and update the "Fulfills orders" block body to:

```tsx
            <OrdersPicker selectedOrderId={config.orderId} onChange={onSetOrder} />
```

Also update the `StepNum` done flag for that step from `config.orderIds.length > 0` to `config.orderId != null`, and the heading copy from "Fulfills orders" / "the customer or internal order(s)" to singular ("Fulfills order").

(f) In the main `NewJobScreen` render where `<PlateConfigPanel ... onSetOrders={ids => setOrdersForPlate(activePlateId, ids)} />` is wired, change to `onSetOrder={oid => setOrderForPlate(activePlateId, oid)}`.

(g) In `SummaryCard`, replace the multi-order aggregation. Change the `allOrders` Set logic to collect single ids:

```tsx
  const allOrders = new Set<number>();
  selectedPlateIds.forEach(id => {
    const oid = plateConfigs[id]?.orderId;
    if (oid != null) allOrders.add(oid);
  });
```

and render the chips as `#${id}` (the Set now holds numbers):

```tsx
            {Array.from(allOrders).map(id => (
              <span key={id} className="mono tiny" style={{ padding: '2px 8px', background: 'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 999, color: 'var(--text-2)' }}>#{id}</span>
            ))}
```

(h) In `doCreate`, pass the order link to `createJob`. Add `order_id: cfg.orderId,` to the `createJob({ ... })` call object.

- [ ] **Step 3: Wire `App.tsx` — real orders count, edit route, topbar button**

In `frontend/src/App.tsx`:

(a) Add import: `import { useOrders } from './api/orders';` and remove `import { ORDERS } from './data/mock';`.

(b) Replace `const ordersOpen = useMemo(() => ORDERS.filter(o => o.status !== 'complete').length, []);` with:

```tsx
  const { orders } = useOrders();
  const ordersOpen = useMemo(() => orders.filter(o => o.status !== 'complete').length, [orders]);
```

(c) In `screenConfig`, give the `/orders` action an onClick and add an edit-title entry:

```tsx
    '/orders':     { title: 'Orders',            crumbs: ['Workshop'],
                     actions: <button className="btn primary sm" onClick={() => navigate('/orders/new')}>{Icons.plus} New order</button> },
    '/orders/new': { title: 'New order',         crumbs: ['Workshop', 'Orders'] },
```

(d) Add the edit route inside `<Routes>` just after the `/orders/new` route:

```tsx
            <Route path="/orders/:id/edit" element={<NewOrderScreen />} />
```

- [ ] **Step 4: Run the affected frontend tests**

Run: `cd frontend && npx vitest run src/screens/NewJobScreen.test.tsx src/api/queue.test.ts`
Expected: PASS. (`NewJobScreen.test.tsx` renders the screen; `useOrders` opens a WebSocket — if the test environment lacks `WebSocket`, add the same `FakeWS` stub used in `OrdersScreen.test.tsx` to `NewJobScreen.test.tsx`.)

- [ ] **Step 5: Full frontend typecheck + test run**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: no type errors; all suites pass. Fix any remaining `data/mock` `ORDERS`/`JOBS` references surfaced by `tsc` (those imports now live only in files that still use other mock exports).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/screens/NewJobScreen.tsx frontend/src/api/queue.ts frontend/src/api/queue.test.ts frontend/src/App.tsx
git commit -m "feat(orders): link jobs to a single order; wire routes and sidebar count"
```

---

## Task 8: Full-stack verification

**Files:** none (verification only)

- [ ] **Step 1: Backend suite**

Run: `cd backend && pytest -q`
Expected: all PASS.

- [ ] **Step 2: Frontend suite + build**

Run: `cd frontend && npx vitest run && npm run build`
Expected: tests pass; production build succeeds.

- [ ] **Step 3: Manual smoke (optional but recommended)**

Start backend (`cd backend && uvicorn app.main:app --reload --port 8001`) and frontend (`cd frontend && npm run dev`). Verify: create an order (Orders → New order), see it listed; open New job, link a plate to that order, add to queue; confirm the order shows the job under "Jobs filling this order" and the progress bar reflects job state; toggle Hold; edit the order's parts; delete the order and confirm the job stays in the queue.

- [ ] **Step 4: Commit (if any fixes were needed)**

```bash
git add -A
git commit -m "test(orders): full-stack verification fixes"
```

---

## Notes for the implementer

- **No DB wipe needed.** `database._migrate` adds the `jobs.order_id` column to existing SQLite files; the `orders` table is created by `create_all`. The legacy `project_id` column may linger physically in old DBs but is unmapped and ignored.
- **`StatusPill`** already styles `queued`/`in_progress`/`hold`/`complete` (and job statuses like `printing`/`slicing`), so no component change is required; the `as never` casts in the new screens satisfy its `StatusKey` prop type for dynamic status strings.
- **`fmtTime`** takes minutes; `est_minutes` and plate `estimated_time` are already minute-valued in this codebase.
- **`data/mock.ts`** stays in place — other screens still use `PRINTERS`, `FILAMENTS`, etc. Only the `ORDERS`/`JOBS` consumers (OrdersScreen, NewOrderScreen, NewJobScreen, App) are migrated off it.
