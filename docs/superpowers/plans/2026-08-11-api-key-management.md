# API Key Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user create named, scope-limited API keys for external apps (and for the Themis browser SPA itself) to authenticate against the Themis API. Every `/api/v1/*` request — including from the React frontend — must carry a valid key; the key's granted scopes gate which routes it may call. Key lifecycle (create/list/revoke/delete) is managed from a new **Settings → API Keys** page.

**Architecture:** One new table, `api_keys` (name, hashed key + prefix, JSON scope list, enabled/revoked, timestamps). A new `app/auth.py` exposes a `require_scope(scope)` FastAPI dependency applied to every existing route, keyed against a fixed, hardcoded scope registry mirroring the existing route-module boundaries (`files:read`, `jobs:write`, `printers:control`, …). A **bootstrap escape hatch** (requests are allowed through, unscoped, only while `api_keys` is empty) solves the chicken-and-egg problem of needing a key to create the first key. The frontend gains a small client-side "auth gate": on first load with no stored key it bootstraps a full-access key named "Browser" via the open table, stores the raw key in `localStorage`, and injects it as `X-Api-Key` on every subsequent `fetch` — plus as a `?key=` query param on the three `/ws` connections and on the `<img>`-tag endpoints (camera snapshot, thumbnails) that can't carry custom headers.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy async / SQLite (backend); React 19 / TypeScript / Vite (frontend); pytest-asyncio (backend tests); Vitest + Testing Library + Playwright (frontend tests).

---

## Design notes (read before starting)

- **This reverses a documented invariant.** `CONTRIBUTING.md` and `docs/agent/backend.md`/`conventions.md` currently state *"No auth. All API routes are unauthenticated. Do not add auth-dependent logic."* That line becomes false the moment Task 5 lands. Task 15 updates every doc that says this — until then, this plan itself is the source of truth, and doc drift is real, not hypothetical.
- **Decision (confirmed with user): mandatory everywhere.** Every `/api/v1/*` request needs a valid key, including the browser SPA and the existing unauthenticated Ordinus integration (`data-model.md`'s `projects.source_app="ordinus"` flow). This is a bigger blast radius than gating only new "external" routes — it touches all ~12 route modules, all 8 duplicated `request<T>` helpers in `frontend/src/api/*.ts`, all 3 independent `/ws` connections (`queue.ts`, `orders.ts`, `fleet.ts`), and the two raw-URL `<img>` consumers (printer snapshot in `ui.tsx`, plate thumbnails via `plateThumbnailUrl` in `queue.ts`).
- **Decision (confirmed with user): resource:action scopes.** One scope per (route-module, verb-class), not per-endpoint. Full registry in Task 3.
- **No login system is being built.** There are no user accounts. "Mandatory everywhere" is satisfied by having the browser hold its own API key (created for itself, functioning like a device credential), not by adding passwords/sessions. This keeps the feature to exactly what was asked for — API key management — instead of growing into an auth system.
- **Bootstrap escape hatch, not a setup token.** While `SELECT COUNT(*) FROM api_keys` is `0`, `require_scope()` lets every request through unauthenticated (matching today's behavior) so the very first key can be created through the normal `POST /api/v1/api-keys` endpoint. The instant one key exists, the hatch closes **permanently** — there is no "re-open if you forget your key" path other than the DB itself. The frontend's first-run gate races to create that first key before a human ever manually hits the API, so in practice the window is sub-second.
- **The bootstrap key is always full-access, server-side.** The scopes list the client sends on the bootstrap-time create call is ignored; the server grants every scope in the registry when `api_keys` is empty. This stops a bug or a slow frontend from ever creating a crippled first key that can't manage keys.
- **Can't revoke/delete your way out.** Reject (`400`) revoking or deleting the last enabled key that holds `apikeys:write`, mirroring the common "can't delete the last admin" guard. Otherwise a careless cleanup locks out the API Keys page itself.
- **Hashing, not encryption.** Keys are high-entropy random tokens (not human passwords), so SHA-256 is the right primitive (same choice GitHub/Stripe-style tokens make) — no bcrypt/argon2 cost factor needed. Store `key_hash` (sha256 hex) + `key_prefix` (first 12 chars of the raw key, unhashed) for fast lookup; verify the full hash after prefix lookup. The raw key is shown exactly once, at creation, and never persisted or logged.
- **Query-param auth exists only for the handful of routes browsers can't attach headers to**: `/ws`, `GET /printers/{id}/camera`, `GET /printers/{id}/snapshot`, `GET /files/{id}/thumbnails/{name}`. Everything else uses the `X-Api-Key` header. Query-param keys are more exposure-prone (URL logs, browser history) — this plan accepts that for the streaming/image endpoints only, since there's no alternative for native `<img>`/`<video>`/`WebSocket`, and flags it explicitly rather than silently.
- **Existing backend tests must keep passing without per-test edits.** All ~15 test files use the `client` fixture (`backend/tests/conftest.py`) unauthenticated today. Task 13 makes the fixture auto-provision a full-scope key and set it as the `AsyncClient`'s default header, so every existing call site keeps working; tests that specifically want to exercise 401/403 pass an explicit override header per-call.
- **Ordinus breaks on deploy, on purpose.** Since this is mandatory-everywhere, Ordinus's existing unauthenticated calls (`POST /files/upload`, `POST /projects`, `POST /projects/:id/items`) will start returning 401 the moment this ships. This plan does not update Ordinus — flagged in Task 16 as a required *operational* step (mint Ordinus a key with `files:write, projects:write` and hand it over) that must happen at/before rollout, not as code in this repo.
- **`mock/server.py`** (the published `ninjabuffalo/themis-mock` API mock external teams build against) is explicitly **out of scope** for this plan — flagged as an open question in Task 16 rather than decided unilaterally, since it's a separately published artifact.
- **Doc drift already found:** none beyond the "No auth" line above — `docs/agent/*` otherwise matches the current route/model/screen structure as verified while scoping this plan (2026-08-11).

---

## Scope registry (Task 3 detail, referenced throughout)

Fixed, hardcoded (not user-extensible) — one `read`/`write` pair per route module, plus `control` for printer hardware actions and a single `apikeys` pair for key management itself.

| Scope | Grants |
|---|---|
| `files:read` | `GET` on `files.py` (list, tree, plates, model-filaments, embedded-settings, thumbnails) |
| `files:write` | upload, folders create/delete, patch, delete, tag assign/unassign, rescan |
| `jobs:read` | list/get/details/slice-failures, `check-overrides` |
| `jobs:write` | create, `configs` patch, unblock, cancel, `verify-slice`, outcome |
| `printers:read` | `types`, `profiles`, `orca-machine-catalog`, `camera`, `snapshot`, `test-connection` |
| `printers:write` | create, patch, delete, `rescan-profiles` |
| `printers:control` | `plate-cleared`, pause/resume/stop/light/jog-z/fan/bed-temp/reconnect |
| `queue:read` | `GET /queue` |
| `queue:write` | `PATCH /queue/reorder` |
| `fleet:read` | `GET /fleet` |
| `orders:read` | list/get |
| `orders:write` | create/patch/delete |
| `projects:read` | list/get + items/links/parts GETs |
| `projects:write` | create/patch/delete + items/links/parts mutations + `generate` |
| `laminus:read` | `catalog`, `catalog/status` |
| `laminus:write` | `catalog/refresh`, `catalog/rescan` |
| `settings:read` | `GET` queue/spoolman/webhook config, `fleet-backup` |
| `settings:write` | `PUT` queue/spoolman/webhook config, `spoolman/test`, `fleet-import` |
| `spoolman:read` | `filaments`, `spools` |
| `spoolman:write` | `PATCH filaments/{id}` |
| `tags:read` | `GET /tags` |
| `tags:write` | create/patch/delete, assign/unassign |
| `maintenance:read` | items/templates/status GETs |
| `maintenance:write` | items CRUD, triggers PUT, complete |
| `apikeys:read` | `GET /api-keys` (list — never returns raw key or hash) |
| `apikeys:write` | create/revoke/delete keys |

`/ws` requires *any* valid, non-revoked key (no specific scope — it's a read-only broadcast fan-out of job/printer/queue state already covered piecemeal by the `:read` scopes above; requiring one specific scope would arbitrarily privilege one hook over the others). `/api/v1/health` and static frontend serving (`/`, `/assets/*`, SPA fallback) stay **unauthenticated** — health checks and the SPA shell itself must load before any key exists. FastAPI's `/docs`/`/openapi.json` stay unauthenticated too (schema only, no data); the OpenAPI security scheme (Task 4) makes Swagger's "Authorize" button work for trying real calls from there.

---

### Task 1: Data model + migration v012

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/app/migrations/v012_api_keys.py`
- Modify: `backend/app/migrations/runner.py`
- Test: `backend/tests/test_migrations.py`

- [x] **Step 1: Write the failing migration test**

Append to `backend/tests/test_migrations.py`:

```python
@pytest.mark.asyncio
async def test_v012_adds_api_keys_table():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)  # idempotent second run
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(api_keys)"))).fetchall()}
        tables = {r[0] for r in (await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )).fetchall()}
    assert "api_keys" in tables
    assert {"name", "key_prefix", "key_hash", "scopes", "enabled", "created_at", "last_used_at"} <= cols
    await engine.dispose()
```

Run it, confirm it FAILs (table doesn't exist yet).

- [x] **Step 2: Add the model to `backend/app/models.py`**

```python
class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("key_prefix", name="uq_api_keys_prefix"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex digest, 64 chars
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(32))
    last_used_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    revoked_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
```

Import `UniqueConstraint` if not already imported at the top of `models.py`. Follow the existing `JSON` column convention (matches `webhook_config.events`).

- [x] **Step 3: Write the migration**

Create `backend/app/migrations/v012_api_keys.py`:

```python
"""Add api_keys table for API key authentication."""
from __future__ import annotations
from sqlalchemy import text

version = 12
name = "api_keys"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) NOT NULL,
            key_prefix VARCHAR(16) NOT NULL,
            key_hash VARCHAR(64) NOT NULL,
            scopes TEXT NOT NULL DEFAULT '[]',
            enabled BOOLEAN NOT NULL DEFAULT 1,
            created_at VARCHAR(32) NOT NULL,
            last_used_at VARCHAR(32),
            revoked_at VARCHAR(32)
        )
    """))
    await conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_api_keys_prefix ON api_keys (key_prefix)"
    ))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS api_keys"))
```

- [x] **Step 4: Register in `runner.py`**

Add `v012_api_keys` to the import line and `_MIGRATIONS` list (append at the end, matching the existing pattern).

- [x] **Step 5: Run the migration test, confirm it PASSes**

`cd backend && pytest tests/test_migrations.py::test_v012_adds_api_keys_table -v`

---

### Task 2: Key generation + hashing service

**Files:**
- Create: `backend/app/services/api_key_service.py`
- Test: `backend/tests/services/test_api_key_service.py`

- [x] **Step 1: Write failing tests**

```python
from app.services.api_key_service import generate_key, hash_key, PREFIX_LEN

def test_generate_key_shape():
    raw, prefix = generate_key()
    assert raw.startswith("thm_")
    assert prefix == raw[:PREFIX_LEN]
    assert len(raw) > PREFIX_LEN

def test_generate_key_is_random():
    raw1, _ = generate_key()
    raw2, _ = generate_key()
    assert raw1 != raw2

def test_hash_key_deterministic_and_not_reversible_looking():
    raw, _ = generate_key()
    assert hash_key(raw) == hash_key(raw)
    assert hash_key(raw) != raw
    assert len(hash_key(raw)) == 64  # sha256 hex
```

- [x] **Step 2: Implement**

```python
"""Key generation/hashing for API-key auth. sha256 is sufficient here — these are
high-entropy random tokens, not human-chosen passwords, so no bcrypt/argon2 cost
factor is needed."""
from __future__ import annotations
import hashlib
import secrets

PREFIX_LEN = 12  # "thm_" + 8 chars, enough to disambiguate without help of the hash


def generate_key() -> tuple[str, str]:
    """Returns (raw_key, prefix). raw_key is shown to the user exactly once."""
    raw = "thm_" + secrets.token_urlsafe(24)
    return raw, raw[:PREFIX_LEN]


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

- [x] **Step 3: Run tests, confirm PASS**

---

### Task 3: Auth dependency (`require_scope`) + scope registry

**Files:**
- Create: `backend/app/auth.py`
- Test: `backend/tests/test_auth.py`

`app/auth.py` owns: the scope registry (as a flat `set[str]`, for validation when creating keys), the `X-Api-Key`/`?key=` extraction, prefix→hash lookup, the bootstrap-empty-table hatch, `last_used_at` touch (throttled), and the `require_scope(scope)` dependency factory.

- [x] **Step 1: Write failing tests** covering: no key + empty table → passes (bootstrap); no key + non-empty table → 401; bad key → 401; valid key missing the required scope → 403; valid key with scope → 200; disabled/revoked key → 401; key accepted via `?key=` query param as well as header.

- [x] **Step 2: Implement `app/auth.py`**

```python
from __future__ import annotations
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_session
from .models import ApiKey
from .services.api_key_service import hash_key

SCOPES: set[str] = {
    "files:read", "files:write",
    "jobs:read", "jobs:write",
    "printers:read", "printers:write", "printers:control",
    "queue:read", "queue:write",
    "fleet:read",
    "orders:read", "orders:write",
    "projects:read", "projects:write",
    "laminus:read", "laminus:write",
    "settings:read", "settings:write",
    "spoolman:read", "spoolman:write",
    "tags:read", "tags:write",
    "maintenance:read", "maintenance:write",
    "apikeys:read", "apikeys:write",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


async def _table_is_empty(session: AsyncSession) -> bool:
    count = (await session.execute(select(func.count()).select_from(ApiKey))).scalar_one()
    return count == 0


async def _resolve_key(request: Request, session: AsyncSession) -> ApiKey | None:
    raw = request.headers.get("X-Api-Key") or request.query_params.get("key")
    if not raw:
        return None
    prefix = raw[:12]
    row = (await session.execute(
        select(ApiKey).where(ApiKey.key_prefix == prefix, ApiKey.enabled == True)  # noqa: E712
    )).scalar_one_or_none()
    if row is None or row.key_hash != hash_key(raw):
        return None
    # Throttled last_used_at touch — don't write on every single request.
    if row.last_used_at is None or row.last_used_at < _now()[:16]:  # coarser than a full write each time
        row.last_used_at = _now()
        await session.commit()
    return row


def require_scope(scope: str):
    assert scope in SCOPES, f"unknown scope {scope!r}"

    async def _dep(request: Request, session: AsyncSession = Depends(get_session)) -> ApiKey | None:
        if await _table_is_empty(session):
            return None  # bootstrap: open access until the first key is created
        key = await _resolve_key(request, session)
        if key is None:
            raise HTTPException(401, "Missing or invalid API key")
        if scope not in (key.scopes or []):
            raise HTTPException(403, f"API key lacks required scope: {scope}")
        return key

    return _dep


async def require_any_key(request: Request, session: AsyncSession = Depends(get_session)) -> ApiKey | None:
    """For /ws — any valid key, no specific scope."""
    if await _table_is_empty(session):
        return None
    key = await _resolve_key(request, session)
    if key is None:
        raise HTTPException(401, "Missing or invalid API key")
    return key
```

- [x] **Step 3: Run tests, confirm PASS**

*Note on the `last_used_at` throttle:* the sketch above compares minute-granularity strings as a cheap throttle so a hot-polling client doesn't write every request; tune/replace with whatever's simplest to get right (e.g. an in-process `dict[int, float]` last-write-time cache keyed by key id) — the requirement is just "don't `commit()` on every single authenticated request," not a specific mechanism.

---

### Task 4: API key CRUD routes (`api_keys.py`)

**Files:**
- Create: `backend/app/api/routes/api_keys.py`
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/test_api_keys.py`

- [x] **Step 1: Write failing tests** covering: `POST` with no existing keys ignores requested scopes and grants all of `SCOPES`; `POST` with an existing full-access key and an explicit scope list grants exactly that list (rejecting unknown scope strings with 422); response includes the raw key **only** on create, never on list; `GET` list never includes `key_hash` or the raw key; `DELETE`/revoke on the last enabled key holding `apikeys:write` → 400; revoke sets `enabled=False`+`revoked_at` (soft) vs delete removing the row (hard) — decide which the UI exposes (see Step 3).

- [x] **Step 2: Implement**, following the existing route-module pattern (Pydantic `*Create`/`*Patch`, `_to_dict`, `_get_or_404`, positional `HTTPException` detail):

```python
from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import SCOPES, require_scope, _table_is_empty
from ...database import get_session
from ...models import ApiKey
from ...services.api_key_service import generate_key, hash_key

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _to_dict(row: ApiKey) -> dict:
    return {
        "id": row.id, "name": row.name, "key_prefix": row.key_prefix,
        "scopes": row.scopes or [], "enabled": row.enabled,
        "created_at": row.created_at, "last_used_at": row.last_used_at,
        "revoked_at": row.revoked_at,
    }


async def _get_or_404(session: AsyncSession, key_id: int) -> ApiKey:
    row = await session.get(ApiKey, key_id)
    if row is None:
        raise HTTPException(404, "API key not found")
    return row


async def _enabled_apikeys_write_count(session: AsyncSession, exclude_id: int | None = None) -> int:
    rows = (await session.execute(select(ApiKey).where(ApiKey.enabled == True))).scalars().all()  # noqa: E712
    return sum(1 for r in rows if r.id != exclude_id and "apikeys:write" in (r.scopes or []))


class ApiKeyCreate(BaseModel):
    name: str
    scopes: list[str] = []


@router.get("", dependencies=[Depends(require_scope("apikeys:read"))])
async def list_keys(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))).scalars().all()
    return [_to_dict(r) for r in rows]


@router.post("", dependencies=[Depends(require_scope("apikeys:write"))])
async def create_key(body: ApiKeyCreate, session: AsyncSession = Depends(get_session)):
    bootstrap = await _table_is_empty(session)
    scopes = sorted(SCOPES) if bootstrap else body.scopes
    unknown = set(scopes) - SCOPES
    if unknown:
        raise HTTPException(422, f"Unknown scope(s): {', '.join(sorted(unknown))}")

    raw, prefix = generate_key()
    row = ApiKey(
        name=body.name, key_prefix=prefix, key_hash=hash_key(raw),
        scopes=scopes, enabled=True, created_at=_now(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {**_to_dict(row), "key": raw}  # raw key: this response only, ever


@router.post("/{key_id}/revoke", dependencies=[Depends(require_scope("apikeys:write"))])
async def revoke_key(key_id: int, session: AsyncSession = Depends(get_session)):
    row = await _get_or_404(session, key_id)
    if "apikeys:write" in (row.scopes or []) and await _enabled_apikeys_write_count(session, exclude_id=key_id) == 0:
        raise HTTPException(400, "Cannot revoke the last key with API-key management access")
    row.enabled = False
    row.revoked_at = _now()
    await session.commit()
    return _to_dict(row)


@router.delete("/{key_id}", dependencies=[Depends(require_scope("apikeys:write"))])
async def delete_key(key_id: int, session: AsyncSession = Depends(get_session)):
    row = await _get_or_404(session, key_id)
    if row.enabled and "apikeys:write" in (row.scopes or []) and await _enabled_apikeys_write_count(session, exclude_id=key_id) == 0:
        raise HTTPException(400, "Cannot delete the last key with API-key management access")
    await session.delete(row)
    await session.commit()
    return {"ok": True}
```

- [x] **Step 3: Decide revoke vs delete in the UI** — expose both: "Revoke" (soft, keeps history/audit row visible greyed-out) as the primary action, "Delete" (hard remove) as a secondary confirm-guarded action on already-revoked keys. This mirrors no existing Themis pattern exactly, but is the safer default for a credential-management surface (accidental delete of an in-use key is unrecoverable and unauditable).

- [x] **Step 4: Register router in `main.py`** — add the import and `app.include_router(api_keys_router)` alongside the others.

- [x] **Step 5: Run tests, confirm PASS**

---

### Task 5: Apply `require_scope` to every existing route

**Files (one dependency per route or per-router, per the table below):**
`files.py`, `jobs.py`, `printers.py`, `queue.py`, `fleet.py`, `orders.py`, `projects.py`, `laminus.py`, `settings.py`, `spoolman.py`, `tags.py`, `maintenance.py`.

Mechanical, per-route application using the Scope registry table above — for each route function, add `dependencies=[Depends(require_scope("<scope>"))]` to the `@router.<method>(...)` decorator (or set it once at `APIRouter(..., dependencies=[...])` level for a router/sub-path that's uniformly one scope, e.g. `queue.py`'s single write route can stay per-route since the module mixes read+write). Import `require_scope` from `...auth` in each file.

- [x] **Step 1:** `files.py` — GETs get `files:read`; upload/folders/patch/delete/tags/rescan get `files:write`.
- [x] **Step 2:** `jobs.py` — list/get/details/slice-failures/check-overrides get `jobs:read`; create/configs/unblock/cancel/verify-slice/outcome get `jobs:write`.
- [x] **Step 3:** `printers.py` — types/profiles/orca-machine-catalog/camera/snapshot/test-connection get `printers:read`; create/patch/delete/rescan-profiles get `printers:write`; plate-cleared/pause/resume/stop/light/jog-z/fan/bed-temp/reconnect get `printers:control`.
- [x] **Step 4:** `queue.py` — `GET` gets `queue:read`; `PATCH /reorder` gets `queue:write`.
- [x] **Step 5:** `fleet.py` — `GET` gets `fleet:read`.
- [x] **Step 6:** `orders.py` — GETs get `orders:read`; create/patch/delete get `orders:write`.
- [x] **Step 7:** `projects.py` — GETs (incl. items/links/parts) get `projects:read`; all mutations (incl. `generate`) get `projects:write`.
- [x] **Step 8:** `laminus.py` — `catalog`/`catalog/status` get `laminus:read`; `catalog/refresh`/`catalog/rescan` get `laminus:write`.
- [x] **Step 9:** `settings.py` — GETs (queue/spoolman/webhook/fleet-backup) get `settings:read`; PUTs + `spoolman/test` + `fleet-import` get `settings:write`.
- [x] **Step 10:** `spoolman.py` — GETs get `spoolman:read`; `PATCH` gets `spoolman:write`.
- [x] **Step 11:** `tags.py` — `GET` gets `tags:read`; create/patch/delete/assign/unassign get `tags:write`.
- [x] **Step 12:** `maintenance.py` — GETs get `maintenance:read`; CRUD/triggers/complete get `maintenance:write`.
- [x] **Step 13:** For each module, run its existing test file and fix failures by adding the seeded test key's header (Task 13 makes this automatic via the fixture — if any test constructs its own `AsyncClient` instead of using the `client` fixture, fix it up individually).

---

### Task 6: `/ws` auth

**Files:** Modify `backend/app/api/websocket.py`, `backend/app/main.py` (route signature).

- [x] **Step 1:** Change `websocket_endpoint` to accept a `key: str | None = None` query param, resolve it via `auth.require_any_key`-equivalent logic run manually before `await websocket.accept()` (can't use a normal `Depends` chain the same way for websockets — call the resolution helper directly), and `await websocket.close(code=4401)` + `return` if invalid (and the table isn't empty).
- [x] **Step 2:** Test: connect without `key` when a key exists → connection closed with 4401; connect with a valid key → connection accepted and receives broadcasts as before.

---

### Task 7: OpenAPI security scheme (so `/docs` "Authorize" works)

**Files:** Modify `backend/app/main.py`.

- [x] Add an `APIKeyHeader(name="X-Api-Key")` security scheme to the FastAPI app so Swagger UI's Authorize button lets a developer paste a key and exercise real authenticated calls from `/docs`. This is documentation ergonomics, not enforcement — enforcement is entirely `require_scope`.

---

### Task 8: Frontend — centralize the fetch header injection

**Files:**
- Create: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/{files,queue,orders,printers,spoolman,projects,maintenance,tags}.ts` (8 files — each has its own local `request<T>`)

- [x] **Step 1:** Create `frontend/src/api/client.ts`:

```typescript
import { getApiKey } from '../auth/apiKeyStore';

export async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const key = getApiKey();
  const headers = new Headers(init?.headers);
  if (key) headers.set('X-Api-Key', key);
  return fetch(url, { ...init, headers });
}

export function withKeyParam(url: string): string {
  const key = getApiKey();
  if (!key) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}key=${encodeURIComponent(key)}`;
}
```

- [x] **Step 2:** In each of the 8 `api/*.ts` files, change the local `request<T>` to call `apiFetch` instead of `fetch` directly — one-line change per file, signature/exports unchanged:

```typescript
// before: const resp = await fetch(url, init);
// after:
import { apiFetch } from './client';
// ...
const resp = await apiFetch(url, init);
```

- [x] **Step 3:** `npm run build` (`tsc -b`) to confirm no unused-import/type breaks across the 8 files.

---

### Task 9: Frontend — key storage + first-run auth gate

**Files:**
- Create: `frontend/src/auth/apiKeyStore.ts`
- Create: `frontend/src/auth/AuthGate.tsx`
- Modify: `frontend/src/App.tsx` (or `main.tsx`) — wrap the app in `AuthGate`

- [x] **Step 1:** `apiKeyStore.ts` — thin localStorage wrapper: `getApiKey()`, `setApiKey(key: string)`, `clearApiKey()`, namespaced key `themis.apiKey`.

- [x] **Step 2:** `AuthGate.tsx` — on mount: if no stored key, `POST /api/v1/api-keys` (via plain `fetch`, not `apiFetch`, since there's nothing to inject yet) with `{name: "Browser"}`; store the returned `key`; render children. If the create call 401/403s (table already non-empty — someone else bootstrapped first, e.g. two browser tabs racing, or a stale install), fall back to a manual "Enter your API key" form instead of auto-bootstrapping. Also handle the steady-state case: any `apiFetch` response that comes back `401` (e.g. this browser's key was revoked from elsewhere) should clear the stored key and re-render the gate's manual-entry form — wire this as a small event/callback from `client.ts` (e.g. a module-level `onUnauthorized` callback the gate registers) rather than duplicating the check at every call site.

- [x] **Step 3:** Wrap `<AppShell>` (or the top of `App.tsx`'s tree) in `<AuthGate>`.

- [x] **Step 4:** Manual Vitest coverage: gate renders children immediately when a key is already stored; gate calls bootstrap POST and stores the result when none is stored; gate shows manual-entry form when bootstrap 401s.

---

### Task 10: Frontend — WebSocket + image-URL consumers

**Files:**
- Modify: `frontend/src/api/queue.ts`, `frontend/src/api/orders.ts`, `frontend/src/api/fleet.ts` (3 `new WebSocket(...)` call sites)
- Modify: `frontend/src/components/ui.tsx` (printer snapshot `<img src>`, camera `VideoTile`)
- Modify: `frontend/src/api/queue.ts` (`plateThumbnailUrl`)

- [x] **Step 1:** In each of the 3 WS call sites, append the key: `` `${proto}//${window.location.host}/ws?key=${encodeURIComponent(getApiKey() ?? '')}` ``.
- [x] **Step 2:** In `ui.tsx`, wrap the snapshot `src` and the camera MJPEG `src` with `withKeyParam(...)` from `api/client.ts`.
- [x] **Step 3:** In `plateThumbnailUrl` (`queue.ts`), return `withKeyParam(...)` instead of the bare URL.
- [x] **Step 4:** Manually verify in a running dev server: Fleet camera tiles and snapshots load; plate thumbnails render in Queue/Job Detail; `/ws` connects (check Network tab, no 4401 close).

---

### Task 11: Frontend — API Keys API client + Settings page

**Files:**
- Create: `frontend/src/api/apiKeys.ts`
- Modify: `frontend/src/screens/SettingsScreen.tsx`
- Modify: `frontend/src/components/Sidebar.tsx` (`settingsSubItems` — kept in sync per the existing note in `frontend.md`)

- [x] **Step 1:** `api/apiKeys.ts` — types `ApiKeyOut { id, name, key_prefix, scopes: string[], enabled, created_at, last_used_at, revoked_at }`, `ApiKeyCreated extends ApiKeyOut { key: string }`; `getApiKeys()`, `createApiKey(name, scopes)`, `revokeApiKey(id)`, `deleteApiKey(id)`; also export the `SCOPES` list grouped by resource (mirrors the backend registry — hand-write it here too, there's no shared-types codegen in this repo) for the checkbox UI.

- [x] **Step 2:** In `SettingsScreen.tsx`: add `'api-keys'` to the `PageId` union; add a nav item (new "Security" section, or under "System") `{ id: 'api-keys', label: 'API Keys', icon: SettingsIcons.<pick or add one>, sub: 'Manage app access & scopes' }`; add `activePage === 'api-keys' && <ApiKeysPage />`.

- [x] **Step 3:** Implement `ApiKeysPage()` in `SettingsScreen.tsx` (co-located with the other `*Page` components, matching the file's existing pattern):
  - Table (`.tbl`, matching `styling.md` vocabulary) of keys: name, prefix (`thm_ab12cd34…`), scope pills, created/last-used, enabled/revoked `<StatusPill>`, row actions (Revoke / Delete).
  - "Create key" button opens a modal: name input (`.input`) + scope checkboxes grouped by resource (two/three columns per resource for read/write/control, using `SCOPES` from `apiKeys.ts`).
  - On successful create, show a one-time reveal dialog: the raw key in a monospace, selectable field + a copy-to-clipboard button + explicit "this will not be shown again" warning. Closing the dialog clears the raw key from component state (never re-fetchable).
  - Empty state via `<Empty>` when no keys exist yet (shouldn't normally happen post-bootstrap, but the "Browser" key could theoretically be deleted).

- [x] **Step 4:** `Sidebar.tsx` — add the matching `{ to: '/settings/api-keys', label: 'API Keys' }` entry to `settingsSubItems`.

- [x] **Step 5:** `npm run build`, then manually exercise the page in a running dev server: create a scoped key, copy it, `curl` a protected endpoint with it (should 200/403 correctly per its scopes), revoke it (subsequent `curl` → 401), confirm the last-`apikeys:write`-key guard blocks self-lockout.

---

### Task 12: Backend test fixture — keep existing tests green

**Files:** Modify `backend/tests/conftest.py`.

- [x] **Step 1:** After creating the schema and before yielding the client, seed one full-scope `ApiKey` row directly via the session, and construct the `AsyncClient` with `headers={"X-Api-Key": raw_key}` as a default:

```python
from app.auth import SCOPES
from app.models import ApiKey
from app.services.api_key_service import generate_key, hash_key

# inside the client fixture, after create_all and before the AsyncClient block:
raw, prefix = generate_key()
async with factory() as _seed:
    _seed.add(ApiKey(
        name="test-fixture", key_prefix=prefix, key_hash=hash_key(raw),
        scopes=sorted(SCOPES), enabled=True, created_at="2026-01-01T00:00:00",
    ))
    await _seed.commit()

async with AsyncClient(
    transport=ASGITransport(app=app), base_url="http://test",
    headers={"X-Api-Key": raw},
) as c:
    yield c
```

- [x] **Step 2:** Run the full existing suite (`pytest -v`) — everything should pass unmodified, since the fixture now authenticates every call by default. Individual tests that want to assert 401/403 behavior override per-call: `await client.get(url, headers={"X-Api-Key": ""})`.
- [x] **Step 3:** Add `backend/tests/test_api_keys.py` (drafted in Task 4) and `backend/tests/test_auth.py` (drafted in Task 3) if not already written inline with those tasks.

---

### Task 13: Frontend test infra — e2e + unit

**Files:**
- Modify: `frontend/e2e/mock-api.ts`
- Check: any Vitest spec that stubs `fetch`/`WebSocket` directly (`QueueScreen.test.tsx`, `ui.test.tsx`, others per `frontend.md`'s test list)

- [x] **Step 1:** In `mock-api.ts`'s `mockApi(page, over?)`, seed `localStorage` with a fake key (e.g. `thm_e2e_fake_key`) via `page.addInitScript` (or equivalent Playwright pre-navigation hook) **before** the app loads, and route-mock `POST **/api/v1/api-keys` in case the gate races anyway. This keeps every existing Playwright spec working without inserting a bootstrap-click step into each one.
- [x] **Step 2:** Run `npm run test:e2e` — fix any spec that now sees the `AuthGate`'s loading/gate flash before the mocked key resolves (likely needs `page.waitForSelector` on real content instead of assuming immediate render, if not already the pattern).
- [x] **Step 3:** Run `npx vitest run` — any spec that stubs `fetch` via `vi.stubGlobal` and asserts exact call args may need the `X-Api-Key` header accounted for (or `apiFetch` mocked instead of global `fetch`, per-file, whichever is less invasive).

---

### Task 14: Rollout / operational steps (not code — track explicitly)

- [ ] Deploy the migration + backend change to a fresh or existing install; confirm the *first* browser load auto-bootstraps a "Browser" key (check the new Settings → API Keys page shows exactly one full-scope key).
- [ ] Mint a scoped key for **Ordinus** (`files:write, projects:write` at minimum — confirm exact scope needs against Ordinus's actual call list before handing it over) and update Ordinus's integration config with it. Until this happens, Ordinus's calls to Themis 401.
- [ ] Decide (open question, not answered by this plan) whether `mock/server.py`/`ninjabuffalo/themis-mock` should also start requiring a key for realism, or stay permissive since it's a dev-time mock. Flag to the user before touching that separately-published artifact.
- [ ] Any other existing script/cron/integration that calls Themis unauthenticated today needs a key minted before this ships, or it silently starts failing.

---

### Task 15: Docs sync

**Files:** `CONTRIBUTING.md`, `docs/agent/backend.md`, `docs/agent/conventions.md`, `docs/agent/data-model.md`, `docs/agent/frontend.md`.

- [x] Replace the "No auth" gotcha in `CONTRIBUTING.md` and `conventions.md` with a description of the new scheme (mandatory `X-Api-Key`/`?key=`, bootstrap-when-empty, scope registry location).
- [x] Add `api_keys` to `data-model.md`'s table list and column reference.
- [x] Add `api_keys.py` to `backend.md`'s routes table and `app/auth.py` to the services/key-flows section.
- [x] Add the API Keys screen + `api/apiKeys.ts` + `auth/` dir to `frontend.md`'s screens/components tables.
- [x] Or, simpler: run the `themis-docs-sync` skill after implementation instead of hand-editing each doc.

---

## Summary of new/changed files

**Backend (new):** `app/auth.py`, `app/services/api_key_service.py`, `app/api/routes/api_keys.py`, `app/migrations/v012_api_keys.py`, `tests/test_auth.py`, `tests/test_api_keys.py`, `tests/services/test_api_key_service.py`.
**Backend (modified):** `app/models.py`, `app/migrations/runner.py`, `app/main.py`, `app/api/websocket.py`, every file in `app/api/routes/` except the new one, `tests/conftest.py`, `tests/test_migrations.py`.
**Frontend (new):** `src/api/client.ts`, `src/api/apiKeys.ts`, `src/auth/apiKeyStore.ts`, `src/auth/AuthGate.tsx`.
**Frontend (modified):** `src/api/{files,queue,orders,printers,spoolman,projects,maintenance,tags}.ts`, `src/components/ui.tsx`, `src/screens/SettingsScreen.tsx`, `src/components/Sidebar.tsx`, `src/App.tsx` (or `main.tsx`), `e2e/mock-api.ts`.
