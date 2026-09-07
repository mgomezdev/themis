# Project public share links — design

Status: approved, ready for implementation plan.

## Motivation

Themis needs a way to share a single project's status with someone who has no Themis account and
should not get one — typically an external customer checking on their order. The link must be:

- **Read-only** — no way to mutate anything through it.
- **Unauthenticated** — no API key, no login.
- **Scoped to exactly one project** — no path to any other project or any other Themis data.
- **Revocable** — the shop can kill a link at any time.
- **Regenerable** — issuing a new link invalidates the old one immediately.

This is a Themis-only change. Themis already owns full project data — items, parts, links, and
progress — including for projects created by Ordinus (`source_app="ordinus"`), since Ordinus uploads
item files into Themis's own library and creates/populates the project through the normal API (see
`docs/agent/data-model.md` § Ordinus → Themis project creation). No coordination with Laminus or
Ordinus is required.

## Data model

New migration `backend/app/migrations/v018_project_share_token.py`, adding two nullable columns to
`projects`:

- `share_token: str | None` — unique index. Generated with `secrets.token_urlsafe(32)` (256 bits).
- `share_token_created_at: str | None` — same timestamp format as the rest of the table
  (`datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")`).

A project's sharing state is fully captured by whether `share_token` is set — no new table. Approaches
considered and rejected:

- **Separate `project_share_links` table** (for history / multiple simultaneous links) — nothing in the
  requirements asks for either; adds a table and cleanup logic for no benefit.
- **Signed token, no DB lookup** (JWT-style) — makes revocation *harder*: you'd still need a revocation
  list to actually revoke a token, which is the DB lookup this avoids in the first place, plus it adds a
  signing-key management concern this app has never needed.

`down()` drops both columns, per this repo's standard migration shape.

## Auth model — a new scope, not reuse of `projects:write`

Add `projects:share` to the `SCOPES` set in `backend/app/auth.py`. All three share-management endpoints
require it — **not** `projects:write`. Rationale: an integration key (e.g. Ordinus's, which needs
`projects:write` to create/edit projects) should not automatically be able to mint public,
unauthenticated links for arbitrary projects just because it can write project data. Minting a public
link is a materially more sensitive action than editing project fields and gets its own gate.

The browser-bootstrapped "Browser" API key every human gets on first load (`AuthGate.tsx`) is already
granted every scope in `SCOPES` (see `auth.py`'s bootstrap path) — this is the closest thing Themis has
to "admin," and it picks up `projects:share` automatically with no code change. A scoped-down
integration key only gets it if explicitly granted via the API Keys settings page.

This scope must be added in two places that don't share a source of truth (a documented, hand-mirrored
contract in this codebase — see `docs/agent/backend-review.md` § 2):
- `backend/app/auth.py`'s `SCOPES` set
- `frontend/src/api/apiKeys.ts`'s mirrored `SCOPES` list (surfaced in the API Keys settings page's scope
  picker)

## Backend API surface

Three new endpoints on the existing authenticated `projects.py` router:

- `GET /api/v1/projects/{id}/share` (scope `projects:share`) → `{"enabled": bool, "token": str | null}`
- `PUT /api/v1/projects/{id}/share` (scope `projects:share`) → generates a fresh token unconditionally
  (this *is* both "create" and "regenerate" — overwriting an absent token and overwriting an existing
  one are the same code path) and sets `share_token_created_at`. Returns the new state, same shape as
  GET.
- `DELETE /api/v1/projects/{id}/share` (scope `projects:share`) → clears `share_token` and
  `share_token_created_at` (revoke). Returns the new state (`enabled: false`).

The backend returns the bare `token`, never a full URL. The frontend builds the shareable link as
`${window.location.origin}/share/${token}` — Themis is reached from different hostnames depending on
deployment (localhost, a Tailscale name, a reverse proxy), so the browser's own origin is the only
reliable source for the externally-visible URL.

**One new, deliberately isolated module: `backend/app/api/routes/public.py`**, holding exactly one
route:

- `GET /api/v1/public/projects/{token}` — looks up the project by `share_token`. Missing, never-valid,
  or revoked tokens all return the same generic `404` — the response never distinguishes "no such
  token" from "token was revoked" from "project was deleted." Nothing to learn from probing.

This route carries **no** `Depends(require_scope(...))`. It is the one deliberate exception to this
codebase's "every `/api/v1/*` route requires an auth scope" invariant, beyond the existing empty-table
bootstrap hatch (`docs/agent/conventions.md` § Invariants). `docs/agent/conventions.md` gets an explicit
amendment naming this route, and `docs/agent/backend-review.md` gets a note flagging `public.py` so a
future reviewer doesn't mistake the missing auth dependency for a bug.

## Public page content

The public serializer (`_public_project_dict`, in `public.py`, built from the same underlying queries
as `projects.py`'s `_project_dict` but trimmed) returns:

```
{
  "name": str,
  "customer": str,
  "due_date": str | null,
  "on_hold": bool,
  "items": [{ "name": str, "quantity": int, "quantity_completed": int }],
  "parts": [{ "name": str, "quantity": int }],
  "links": [{ "url": str, "label": str | null }],
  "jobs_total": int,
  "jobs_complete": int,
  "estimate_seconds_remaining": int | null,
  "updated_at": str
}
```

Deliberately excluded: `notes` (internal shop notes), `source_app`/`source_user`/`source_layout_id`
(integration plumbing), `machine_uuid`/`process_uuid`/`order_id`/`result_file_id` (internal linkage),
`actual_filament_grams`/`actual_seconds`/`estimate_filament_grams_*` (internal cost/production
metrics), and per-item `filament_type`/`filament_color`/`filament_id`/`quantity_failed` (internal
production routing and failure detail). The internal `id` is also omitted — the page is addressed
purely by token, and there's no reason to expose the row's position in an auto-increment sequence.

## Frontend

**Routing.** `App.tsx` currently wraps its entire `<Routes>` tree in `<AuthGate>`. Restructure so
`BrowserRouter` holds two top-level routes:

- `/share/:token` → new `SharedProjectScreen`, standalone (no `Sidebar`/`Topbar`/`AuthGate`)
- `/*` → today's `<AuthGate><AppShell/></AuthGate>`, unchanged

**`SharedProjectScreen`** calls the public endpoint with a plain `fetch()`, **not** the app's `apiFetch`
client — it has no API key and must not touch the authenticated client's header-injection or
401-redirect-to-AuthGate machinery. A `404` renders "This link is invalid or has been revoked,"
regardless of the underlying reason. On success, it renders a standalone card: name, customer, due
date, an on-hold badge if applicable, a progress readout (`jobs_complete`/`jobs_total`, plus an ETA if
`estimate_seconds_remaining` is present), the items list with per-item quantity/completed, the parts
list, and any links.

**`ProjectDetailScreen`** gets a "Share" action opening a small panel (same interaction shape as the
existing webhook-secret / API-key reveal dialogs elsewhere in Settings):
- No link yet → "Create share link" button.
- Link exists → the full URL in a readonly input with a Copy button, plus **Regenerate** and **Revoke**,
  both behind a confirm (they invalidate whatever the customer currently has bookmarked/saved).

No client-side hiding of the Share button based on scope. Following this app's existing pattern (e.g.
Settings pages), the action is always visible; a key lacking `projects:share` gets the existing
403 → `ForbiddenToast` treatment for free — no new UI plumbing needed for the entitlement check itself.

## Security & edge cases

- Token entropy (256 bits via `secrets.token_urlsafe(32)`) makes brute-forcing infeasible. Unique DB
  index; on the astronomically unlikely collision, retry generation once.
- Revoke/regenerate take effect immediately — no grace period. The next request with the old token
  404s.
- **No rate limiting or abuse protection beyond token entropy is planned**, and this is an explicit,
  accepted non-goal, not a gap: Themis is a self-hosted, low-traffic internal tool (see this repo's
  existing Tailscale-oriented deployment model in the root `CLAUDE.md`), and adding rate-limiting
  infrastructure for one new read endpoint would be disproportionate to the actual threat model.

## Testing plan

**Backend (TDD):**
- Scope enforcement: `projects:write`-only key gets 403 on all three share-management endpoints;
  `projects:share` gets 200.
- Public endpoint: valid token returns the trimmed shape above; missing/never-valid/revoked token all
  return the same 404.
- Regenerate invalidates the old token (old token 404s immediately after; new token works).
- Revoke clears the token (subsequent public fetch 404s; subsequent GET share-state shows
  `enabled: false`).

**Frontend:**
- `SharedProjectScreen` renders project data on success; shows the not-found message on 404.
- The Share panel's create/copy/regenerate/revoke flow calls the right endpoints and updates displayed
  state correctly.

**Docs updated in the same change:**
- `docs/agent/conventions.md` — amend the "auth is mandatory" invariant to name this route as the
  second deliberate exception.
- `docs/agent/data-model.md` — document the two new `projects` columns and the three new endpoints.
- `docs/agent/backend-review.md` — flag `public.py` explicitly so its missing auth dependency isn't
  mistaken for an oversight in a future review.
