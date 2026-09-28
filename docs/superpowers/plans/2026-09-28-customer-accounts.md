# Customer accounts, project stages, local admin — plan

Minimal/surgical. Decisions confirmed with the user 2026-09-28.

## Decisions
- **Local = full admin.** `THEMIS_LOCAL_NETWORKS` (comma-separated CIDRs, default `192.168.0.0/16`).
  Client IP in range → synthetic full-scope key, no login. No Host-header / proxy-header trust.
  A reverse proxy's IP must not be inside the local range (documented).
- **Accounts are customers only** (email + password, staff-created). Staff remote access stays API keys.
- **Login reuses the API-key pipeline.** `POST /api/v1/auth/login` mints an `api_keys` row
  (`customer_id` set, scopes `["customer"]`, 30-day expiry). Frontend stores it like any key.
- **Customer ↔ project:** `projects.customer_id` FK → `customers.id` (free-text `customer` untouched).
- **Stages:** `projects.stage` ∈ `draft | planning | queued`, forward-only promote (staff).
  Existing rows → `queued`. Staff/API-created projects default `queued` (keeps Ordinus + current flow);
  customer-created → `draft`.
  - `POST /projects/{id}/generate` → 409 in `draft`.
  - Queue engine claims only jobs with no project or project `stage == queued`.
- Bootstrap hatch + AuthGate bootstrap flow unchanged (local check only after bootstrap fails), so the
  hatch still closes on first browser load exactly as today.

## Backend
- `models.py`: `Customer`; `Project.stage`, `Project.customer_id`; `ApiKey.customer_id`.
- `migrations/v021_customer_accounts.py` (+ runner).
- `auth.py`: `customer`, `customers:read/write` scopes; `is_local(host)`; `require_scope`/`require_any_key`
  honor local; `require_customer` dependency.
- `api/websocket.py`: local → full scopes.
- `services/password.py`: pbkdf2 hash/verify (stdlib).
- `api/routes/auth.py`: `POST /auth/login`, `GET /auth/me` (no scope; reports local/role).
- `api/routes/customers.py` (staff): list/create/patch; disable or password change revokes sessions.
- `api/routes/customer_portal.py` (`customer` scope, always own-scoped): list/get projects (+ jobs, no
  internal cost/printer fields), create/patch draft, upload file to draft (→ ProjectItem).
- `projects.py`: `stage`/`customer_id` in create/patch/dict; `POST /{id}/promote`; generate guard.
- `queue_engine.py`: claim filter.
- `api_keys.py`: list excludes customer session keys.

## Frontend
- `AuthGate`: email/password login form (API-key fallback); `/auth/me` → session context.
- `App.tsx`: customer role → `CustomerPortal` instead of staff shell.
- `screens/CustomerPortal.tsx`: own projects, detail + jobs, new/edit draft, file upload, logout.
- Staff: Settings → Customers page; project detail stage badge + promote + customer select.

## Tests
Backend: local detection, login/me, customer scoping (can't see others' projects), draft lock,
promote transitions, generate guard, queue filter, migration. Frontend: build + existing suite.
