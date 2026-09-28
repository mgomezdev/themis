# Admin account, local-login toggle, offline recovery

Stacked on `feature/customer-accounts-tests` (needs its /ws key-precedence fix).

## Decisions (user)
- Single `admin` account; created on first boot (migration) with **no password**.
- Settings → Admin account: set/change password; checkbox "Local network devices are admin without
  signing in" (default **on**). Turning it off requires a password (no self-lockout).
- Recovery, both offline:
  - CLI: `docker compose exec themis python -m app.admin reset-password` → prints new password to that terminal.
    `... allow-local-login` re-enables the local toggle.
  - Sign-in "Forgot admin password?" → one-time code written to the server log (WARNING), 15 min,
    single use, 5 wrong attempts burns it; an unexpired code is never replaced (spam can't DoS it).
    Doubles as initial setup for a remote-only fresh install.
- Bootstrap hatch removed: empty `api_keys` no longer means open access; `POST /api-keys` never
  auto-grants. `THEMIS_BOOTSTRAP_KEY` (operator-configured) stays.

## Review follow-ups
- Commands use `docker compose exec/logs themis` (no fixed container_name).
- Admin password ≥ 8; per-IP throttle on failed login/recovery; admin-account routes admin-only.
- Admin page warns about full-access API keys that bypass sign-in (e.g. old "Browser" keys).
- Customer emails must contain "@" (so "admin" can't be one). Last-key guard ignores sessions.
- Known, accepted: an attacker can burn the live log code with 5 bad guesses (CLI still works);
  open /ws connections aren't cut on revocation (they reconnect and re-auth).

## Backend
- `admin_account` singleton (id=1): username, password_hash?, allow_local_login, recovery_code_hash?,
  recovery_code_expires_at?, recovery_attempts. `api_keys.admin_session` bool (hidden from key list,
  revoked on password change). Migration v022.
- `auth.local_admin_allowed(host, session)` = is_local and allow_local_login; used by require_scope,
  require_any_key, /auth/me, /ws (key-first precedence unchanged).
- `/auth/login`: identifier `admin` → admin session (all scopes except `customer`, 30 d).
  `/auth/me` role `admin` for admin sessions. `/auth/recover`, `/auth/recover/confirm`.
- `/api/v1/admin-account` GET (apikeys:read), PUT /password + PATCH (apikeys:write).
- `app/admin.py` CLI.

## Frontend
- AuthGate: no bootstrap POST; stored key → app; else `/auth/me` role → app; else sign-in.
  "Email or username"; recovery panel.
- Settings → Security → Admin account page.

## Tests
- Backend: first-boot row, admin login/role, toggle requires password, toggle off ⇒ LAN needs login,
  password change revokes admin sessions (not current), recovery code flow (log capture, expiry,
  single use, attempts, no replace), CLI reset, empty-table no longer open, /ws.
- Frontend unit (AuthGate) + Playwright (admin login, recovery, settings toggle).
