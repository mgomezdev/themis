"""Auth is mandatory on every /api/v1 route (docs/agent/conventions.md): the only routes that answer without a
key are the documented public ones. Walks the real OpenAPI surface and probes each operation, so a route
added without `Depends(require_scope(...))` fails here instead of shipping open."""
import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import SCOPES
from app.main import app
from app.models import ApiKey
from app.services.api_key_service import generate_key, hash_key

# Deliberately unauthenticated: login/recovery is how a browser obtains a key, the share page is addressed
# by an unguessable per-project token, and health is a liveness probe.
PUBLIC_OPERATIONS = {
    ("POST", "/api/v1/auth/login"),
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/recover"),
    ("POST", "/api/v1/auth/recover/confirm"),
    ("GET", "/api/v1/public/projects/{token}"),
    ("GET", "/api/v1/health"),
}
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _operations() -> set[tuple[str, str]]:
    return {(m.upper(), path) for path, item in app.openapi()["paths"].items() for m in item if m in HTTP_METHODS}


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


async def _key_with(session_factory, scopes: list[str]) -> str:
    raw, prefix = generate_key()
    async with session_factory() as s:
        s.add(ApiKey(name="probe", key_prefix=prefix, key_hash=hash_key(raw), scopes=scopes,
                     enabled=True, created_at="2026-01-01T00:00:00"))
        await s.commit()
    return raw


def _as(key: str | None) -> AsyncClient:
    headers = {"X-Api-Key": key} if key else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers)


def test_the_public_allowlist_only_names_operations_that_exist():
    assert PUBLIC_OPERATIONS <= _operations()


async def test_every_non_public_operation_rejects_no_key_with_401_and_a_scopeless_key_with_403(client, session_factory):
    scopeless = await _key_with(session_factory, [])
    offenders = []
    async with _as(None) as anon, _as(scopeless) as nothing:
        for method, path in sorted(_operations() - PUBLIC_OPERATIONS):
            url = _url(path)
            no_key = (await anon.request(method, url)).status_code
            no_scope = (await nothing.request(method, url)).status_code
            if (no_key, no_scope) != (401, 403):
                offenders.append(f"{method} {path}: no key -> {no_key} (want 401), scopeless key -> {no_scope} (want 403)")
    assert offenders == [], "operations missing a require_scope guard:\n" + "\n".join(offenders)


@pytest.mark.parametrize("scope, allowed, denied, needs", [
    pytest.param("jobs:read", ("GET", "/api/v1/jobs"), ("POST", "/api/v1/jobs/1/cancel"), "jobs:write",
                 id="jobs-read-cannot-cancel"),
    pytest.param("printers:read", ("GET", "/api/v1/printers"), ("POST", "/api/v1/printers"), "printers:write",
                 id="printers-read-cannot-create"),
    pytest.param("printers:write", ("POST", "/api/v1/printers"), ("POST", "/api/v1/printers/1/plate-cleared"),
                 "printers:control", id="printers-write-cannot-control-hardware"),
    pytest.param("tags:read", ("GET", "/api/v1/tags"), ("POST", "/api/v1/tags"), "tags:write",
                 id="tags-read-cannot-create"),
    pytest.param("queue:read", ("GET", "/api/v1/queue"), ("GET", "/api/v1/printers"), "printers:read",
                 id="queue-read-cannot-see-printers"),
])
async def test_a_key_gets_exactly_the_access_its_scope_names(client, session_factory, scope, allowed, denied, needs):
    """`allowed` must get past the guard (any status but 401/403 — an empty body may still 422); `denied`
    must be refused naming the scope it needs."""
    key = await _key_with(session_factory, [scope])
    async with _as(key) as api:
        allowed_status = (await api.request(*allowed)).status_code
        denied_response = await api.request(*denied)
    assert allowed_status not in (401, 403)
    assert denied_response.status_code == 403
    assert denied_response.json()["detail"] == f"API key lacks required scope: {needs}"


def test_frontend_scope_list_mirrors_the_backend_registry():
    """frontend/src/api/apiKeys.ts hand-mirrors auth.SCOPES (no codegen). 'customer' is deliberately not
    grantable from the UI: it is minted only by customer login."""
    source = (Path(__file__).resolve().parents[2] / "frontend/src/api/apiKeys.ts").read_text(encoding="utf-8")
    block = source[source.index("export const SCOPES"):source.index("export const ALL_SCOPES")]
    frontend_scopes = re.findall(r"scope:\s*'([^']+)'", block)
    assert len(frontend_scopes) == len(set(frontend_scopes)), "duplicate scope in apiKeys.ts"
    assert set(frontend_scopes) == SCOPES - {"customer"}
