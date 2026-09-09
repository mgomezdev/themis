# Project Public Share Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan
> task-by-task, executing directly in the main session — this project's CLAUDE.md "Development workflow"
> explicitly opts out of per-task subagent fan-out (see "Cross-cutting changes" / "Development workflow"
> sections). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Themis user generate a public, unauthenticated, read-only link for a single project
(e.g. to share status with a customer), which can be revoked or regenerated at any time.

**Architecture:** A nullable `share_token` column on `projects` (one active token per project; NULL =
not shared). Three new authenticated endpoints (`GET`/`PUT`/`DELETE /api/v1/projects/{id}/share`) manage
it, gated by a new `projects:share` scope. One new, deliberately isolated route module
(`app/api/routes/public.py`) exposes `GET /api/v1/public/projects/{token}` with **no** auth dependency —
the one new exception (beyond the existing bootstrap hatch) to this codebase's "every route requires a
scope" invariant. A new frontend route `/share/:token`, mounted outside `AuthGate`, renders the public
page via a plain `fetch()` call, decoupled from the authenticated API client.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + aiosqlite (backend), React + react-router-dom + Vitest
(frontend). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-06-project-public-share-links-design.md`

---

### Task 1: `share_token` columns — model + migration

**Files:**
- Modify: `backend/app/models.py:167-188` (`Project` class)
- Create: `backend/app/migrations/v018_project_share_token.py`
- Modify: `backend/app/migrations/runner.py:4-8` (register the new migration)
- Test: `backend/tests/test_migrations.py`
- Test: `backend/tests/test_models.py`

- [ ] **Step 1: Write the failing migration test**

Add to `backend/tests/test_migrations.py` (follows the existing idempotent-run pattern used throughout
this file, e.g. `test_migrate_adds_tool_index_idempotently`):

```python
@pytest.mark.asyncio
async def test_migrate_adds_share_token_to_projects_idempotently():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)  # idempotent — second run must not raise
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(projects)"))).fetchall()}
    assert "share_token" in cols
    assert "share_token_created_at" in cols
    await engine.dispose()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && pytest tests/test_migrations.py::test_migrate_adds_share_token_to_projects_idempotently -v`
Expected: FAIL — `share_token` not in `cols` (the column doesn't exist yet; `Base.metadata.create_all`
uses the current, unmodified `Project` model).

- [ ] **Step 3: Add the failing uniqueness test**

Add to `backend/tests/test_models.py`:

```python
from sqlalchemy.exc import IntegrityError
from app.models import Project


async def test_project_share_token_must_be_unique(session):
    now = "2026-09-06T00:00:00Z"
    p1 = Project(name="Project A", created_at=now, updated_at=now, share_token="dup-token")
    p2 = Project(name="Project B", created_at=now, updated_at=now, share_token="dup-token")
    session.add_all([p1, p2])
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_project_share_token_defaults_to_none(session):
    now = "2026-09-06T00:00:00Z"
    p = Project(name="Project C", created_at=now, updated_at=now)
    session.add(p)
    await session.commit()
    await session.refresh(p)
    assert p.share_token is None
    assert p.share_token_created_at is None
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd backend && pytest tests/test_models.py::test_project_share_token_must_be_unique tests/test_models.py::test_project_share_token_defaults_to_none -v`
Expected: FAIL — `TypeError: 'share_token' is an invalid keyword argument for Project` (the field
doesn't exist on the model yet).

- [ ] **Step 5: Add the columns to the `Project` model**

In `backend/app/models.py`, inside the `Project` class (after `updated_at`, before the closing of the
class at line 188):

```python
    share_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    share_token_created_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    __table_args__ = (UniqueConstraint("share_token", name="uq_projects_share_token"),)
```

Confirm `UniqueConstraint` is already imported at the top of `models.py` (it is — used by `ApiKey`); if
not, add it to the existing `from sqlalchemy import ...` line.

- [ ] **Step 6: Run the model tests to verify they pass**

Run: `cd backend && pytest tests/test_models.py -v`
Expected: PASS (all tests, including the two new ones)

- [ ] **Step 7: Write the migration**

Create `backend/app/migrations/v018_project_share_token.py`:

```python
"""Add share_token / share_token_created_at to projects for public share links."""
from __future__ import annotations
from sqlalchemy import text

version = 18
name = "project_share_token"


async def up(conn) -> None:
    info = (await conn.execute(text("PRAGMA table_info(projects)"))).fetchall()
    cols = {row[1] for row in info}
    if "share_token" not in cols:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN share_token VARCHAR(64)"))
    if "share_token_created_at" not in cols:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN share_token_created_at VARCHAR(32)"))
    await conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_share_token ON projects (share_token)"
    ))


async def down(conn) -> None:
    await conn.execute(text("DROP INDEX IF EXISTS uq_projects_share_token"))
    await conn.execute(text("ALTER TABLE projects DROP COLUMN share_token_created_at"))
    await conn.execute(text("ALTER TABLE projects DROP COLUMN share_token"))
```

- [ ] **Step 8: Register the migration**

In `backend/app/migrations/runner.py`, update the import line and `_MIGRATIONS` list (line 4-8) to add
`v018_project_share_token` at the end of both:

```python
from . import v001_initial, v002_project_order_link, v003_webhook_config, v004_gcode_estimates, v005_project_order_merge, v006_project_links, v007_printer_bed_size, v008_job_estimates_and_queue_config, v009_drop_filament_profile_uuid, v010_project_parts, v011_maintenance_tracking, v012_api_keys, v013_filament_any_keyword, v014_api_key_expiration, v015_bootstrap_sentinel, v016_clear_stored_path, v017_notification_config, v018_project_share_token

_MIGRATIONS = sorted(
    [v001_initial, v002_project_order_link, v003_webhook_config, v004_gcode_estimates, v005_project_order_merge, v006_project_links, v007_printer_bed_size, v008_job_estimates_and_queue_config, v009_drop_filament_profile_uuid, v010_project_parts, v011_maintenance_tracking, v012_api_keys, v013_filament_any_keyword, v014_api_key_expiration, v015_bootstrap_sentinel, v016_clear_stored_path, v017_notification_config, v018_project_share_token],
    key=lambda m: m.version,
)
```

- [ ] **Step 9: Run the migration test to verify it passes**

Run: `cd backend && pytest tests/test_migrations.py -v`
Expected: PASS (all tests, including the new one)

- [ ] **Step 10: Run the full backend suite to check for regressions**

Run: `cd backend && pytest -v`
Expected: PASS (same pass count as before, plus the 4 new tests from this task)

- [ ] **Step 11: Commit**

```bash
git add backend/app/models.py backend/app/migrations/v018_project_share_token.py backend/app/migrations/runner.py backend/tests/test_migrations.py backend/tests/test_models.py
git commit -m "Add share_token columns to projects table"
```

---

### Task 2: `projects:share` scope

**Files:**
- Modify: `backend/app/auth.py:13-27` (`SCOPES` set)
- Modify: `frontend/src/api/apiKeys.ts:77-80` (`SCOPES` mirror)
- Test: `backend/tests/test_auth.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_auth.py`:

```python
from app.auth import SCOPES, require_scope


def test_projects_share_scope_is_registered():
    assert "projects:share" in SCOPES
    require_scope("projects:share")  # must not raise ValueError("unknown scope")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && pytest tests/test_auth.py::test_projects_share_scope_is_registered -v`
Expected: FAIL — `AssertionError` (`"projects:share" in SCOPES` is `False`)

- [ ] **Step 3: Add the scope**

In `backend/app/auth.py`, in the `SCOPES` set (line 13-27), add `"projects:share",` on its own line
right after `"projects:read", "projects:write",`:

```python
    "projects:read", "projects:write", "projects:share",
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd backend && pytest tests/test_auth.py -v`
Expected: PASS

- [ ] **Step 5: Mirror the scope in the frontend**

In `frontend/src/api/apiKeys.ts`, in the `projects` resource group (line 77-80), add the new scope:

```typescript
  { resource: 'projects', label: 'Projects', scopes: [
    { scope: 'projects:read', label: 'Read' },
    { scope: 'projects:write', label: 'Write' },
    { scope: 'projects:share', label: 'Share links' },
  ] },
```

- [ ] **Step 6: Verify the frontend still builds**

Run: `cd frontend && npm run build`
Expected: success, no TypeScript errors

- [ ] **Step 7: Commit**

```bash
git add backend/app/auth.py frontend/src/api/apiKeys.ts backend/tests/test_auth.py
git commit -m "Add projects:share scope, gating project-share-link management separately from projects:write"
```

---

### Task 3: Extract `_project_progress` helper (refactor, no behavior change)

This is a pure refactor to let the public endpoint (Task 5) reuse the jobs-progress calculation instead
of duplicating it — no new behavior, so there's no new test to write first. Instead: confirm the
existing tests pass before and after, as the regression check.

**Files:**
- Modify: `backend/app/api/routes/projects.py:230-294` (`_project_dict`)

- [ ] **Step 1: Run the existing project tests to record the baseline**

Run: `cd backend && pytest tests/api/test_projects_api.py -v`
Expected: PASS (record the pass count)

- [ ] **Step 2: Extract `_project_progress`**

In `backend/app/api/routes/projects.py`, replace the body of `_project_dict` (lines 230-294) with:

```python
_TERMINAL = {"complete", "failed", "cancelled"}


def _project_progress(job_rows: list[Job]) -> dict:
    jobs_total = len(job_rows)
    jobs_complete = sum(1 for j in job_rows if j.status == "complete")

    estimate_filament_grams_total = (
        sum(j.estimate_filament_grams for j in job_rows if j.estimate_filament_grams is not None) or None
    )
    estimate_seconds_total = (
        sum(j.estimate_seconds for j in job_rows if j.estimate_seconds is not None) or None
    )
    estimate_filament_grams_remaining = (
        sum(
            j.estimate_filament_grams for j in job_rows
            if j.estimate_filament_grams is not None and j.status not in _TERMINAL
        ) or None
    )
    estimate_seconds_remaining = (
        sum(
            j.estimate_seconds for j in job_rows
            if j.estimate_seconds is not None and j.status not in _TERMINAL
        ) or None
    )
    actual_filament_grams = (
        sum(j.actual_filament_grams for j in job_rows if j.actual_filament_grams is not None) or None
    )
    actual_seconds = (
        sum(j.actual_seconds for j in job_rows if j.actual_seconds is not None) or None
    )

    return {
        "jobs_total": jobs_total,
        "jobs_complete": jobs_complete,
        "estimate_filament_grams_total": round(estimate_filament_grams_total, 2) if estimate_filament_grams_total else None,
        "estimate_seconds_total": estimate_seconds_total,
        "estimate_filament_grams_remaining": round(estimate_filament_grams_remaining, 2) if estimate_filament_grams_remaining else None,
        "estimate_seconds_remaining": estimate_seconds_remaining,
        "actual_filament_grams": round(actual_filament_grams, 2) if actual_filament_grams else None,
        "actual_seconds": actual_seconds,
    }


async def _project_dict(project: Project, session: AsyncSession) -> dict:
    items = await _load_items(session, project.id)
    links = await _load_links(session, project.id)
    parts = await _load_parts(session, project.id)

    job_rows = (await session.execute(
        select(Job).where(Job.project_id == project.id)
    )).scalars().all()
    progress = _project_progress(job_rows)

    return {
        "id": project.id,
        "name": project.name,
        "customer": project.customer,
        "order_type": project.order_type,
        "on_hold": project.on_hold,
        "due_date": project.due_date,
        "notes": project.notes,
        "result_file_id": project.result_file_id,
        "source_app": project.source_app,
        "source_user": project.source_user,
        "source_layout_id": project.source_layout_id,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "items": items,
        "links": links,
        "parts": parts,
        **progress,
    }
```

This is a byte-for-byte behavior-preserving extraction: every key `_project_dict` returned before is
still returned, computed the same way, just via the new `_project_progress` helper.

- [ ] **Step 3: Run the existing project tests again to confirm no regression**

Run: `cd backend && pytest tests/api/test_projects_api.py -v`
Expected: PASS — same pass count as Step 1, including
`test_project_estimate_rollup_keys` and `test_project_estimate_remaining_excludes_terminal_jobs`, which
specifically exercise these fields.

- [ ] **Step 4: Run the full backend suite**

Run: `cd backend && pytest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/projects.py
git commit -m "Extract _project_progress helper from _project_dict for reuse by the public endpoint"
```

---

### Task 4: Share-management endpoints

**Files:**
- Modify: `backend/app/api/routes/projects.py` (add `import secrets`; add `ProjectShareOut` schema and
  three routes after `delete_project`, i.e. after line 410 in the pre-Task-3 numbering — insert
  immediately after the `delete_project` function)
- Test: Create `backend/tests/api/test_project_share.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/api/test_project_share.py`:

```python
from httpx import AsyncClient


async def _create_project(client: AsyncClient) -> int:
    resp = await client.post("/api/v1/projects", json={"name": "Shareable Project"})
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_get_share_state_disabled_by_default(client: AsyncClient):
    project_id = await _create_project(client)

    resp = await client.get(f"/api/v1/projects/{project_id}/share")

    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "token": None}


async def test_put_share_creates_a_token(client: AsyncClient):
    project_id = await _create_project(client)

    resp = await client.put(f"/api/v1/projects/{project_id}/share")

    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert isinstance(body["token"], str) and len(body["token"]) > 20


async def test_get_share_state_reflects_created_token(client: AsyncClient):
    project_id = await _create_project(client)
    created = (await client.put(f"/api/v1/projects/{project_id}/share")).json()

    resp = await client.get(f"/api/v1/projects/{project_id}/share")

    assert resp.json() == created


async def test_put_share_again_regenerates_a_different_token(client: AsyncClient):
    project_id = await _create_project(client)
    first = (await client.put(f"/api/v1/projects/{project_id}/share")).json()

    second = (await client.put(f"/api/v1/projects/{project_id}/share")).json()

    assert second["token"] != first["token"]


async def test_delete_share_revokes_the_token(client: AsyncClient):
    project_id = await _create_project(client)
    await client.put(f"/api/v1/projects/{project_id}/share")

    resp = await client.delete(f"/api/v1/projects/{project_id}/share")

    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "token": None}


async def test_share_endpoints_404_for_missing_project(client: AsyncClient):
    assert (await client.get("/api/v1/projects/999999/share")).status_code == 404
    assert (await client.put("/api/v1/projects/999999/share")).status_code == 404
    assert (await client.delete("/api/v1/projects/999999/share")).status_code == 404


async def test_share_endpoints_require_projects_share_scope_not_just_write(client: AsyncClient):
    """A key scoped to projects:write (but not projects:share) must not be able to
    manage share links - minting one via the real /api/v1/api-keys endpoint (the
    fixture's own key has every scope, including apikeys:write) rather than reaching
    into test internals."""
    project_id = await _create_project(client)

    create_resp = await client.post("/api/v1/api-keys", json={
        "name": "write-only", "scopes": ["projects:read", "projects:write"],
    })
    assert create_resp.status_code == 200
    write_only_key = create_resp.json()["key"]

    resp = await client.get(
        f"/api/v1/projects/{project_id}/share", headers={"X-Api-Key": write_only_key},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/api/test_project_share.py -v`
Expected: FAIL — all tests error with `404 Not Found` (the routes don't exist yet), except the scope
test which will fail differently (also 404, since the route doesn't exist).

- [ ] **Step 3: Add `import secrets` to projects.py**

In `backend/app/api/routes/projects.py`, add `import secrets` to the top-of-file stdlib imports (near
`import re`, line 6):

```python
import re
import secrets
```

- [ ] **Step 4: Add the `ProjectShareOut` schema**

In `backend/app/api/routes/projects.py`, in the "Pydantic schemas" section (after `ProjectPatch`,
around line 62):

```python
class ProjectShareOut(BaseModel):
    enabled: bool
    token: Optional[str] = None
```

- [ ] **Step 5: Add the three routes**

In `backend/app/api/routes/projects.py`, immediately after the `delete_project` function (which ends
with `return {"deleted": project_id}`), add:

```python
@router.get(
    "/{project_id}/share",
    response_model=ProjectShareOut,
    summary="Get project share-link state",
    responses={404: {"description": "Project not found"}},
    dependencies=[Depends(require_scope("projects:share"))],
)
async def get_project_share(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> ProjectShareOut:
    proj = await _get_project_or_404(project_id, session)
    return ProjectShareOut(enabled=proj.share_token is not None, token=proj.share_token)


@router.put(
    "/{project_id}/share",
    response_model=ProjectShareOut,
    summary="Create or regenerate the project's share link",
    responses={404: {"description": "Project not found"}},
    dependencies=[Depends(require_scope("projects:share"))],
)
async def put_project_share(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> ProjectShareOut:
    """Always generates a fresh token, whether or not one already existed - "create"
    and "regenerate" are the same operation."""
    proj = await _get_project_or_404(project_id, session)
    proj.share_token = secrets.token_urlsafe(32)
    proj.share_token_created_at = _now_iso()
    await session.commit()
    await session.refresh(proj)
    return ProjectShareOut(enabled=True, token=proj.share_token)


@router.delete(
    "/{project_id}/share",
    response_model=ProjectShareOut,
    summary="Revoke the project's share link",
    responses={404: {"description": "Project not found"}},
    dependencies=[Depends(require_scope("projects:share"))],
)
async def delete_project_share(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> ProjectShareOut:
    proj = await _get_project_or_404(project_id, session)
    proj.share_token = None
    proj.share_token_created_at = None
    await session.commit()
    await session.refresh(proj)
    return ProjectShareOut(enabled=False, token=None)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && pytest tests/api/test_project_share.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && pytest -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/routes/projects.py backend/tests/api/test_project_share.py
git commit -m "Add GET/PUT/DELETE /api/v1/projects/{id}/share endpoints"
```

---

### Task 5: Public read-only endpoint

**Files:**
- Create: `backend/app/api/routes/public.py`
- Modify: `backend/app/main.py:22-34,149-161` (import + register the router)
- Test: Create `backend/tests/api/test_public_projects.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/api/test_public_projects.py`:

```python
from unittest.mock import patch

from httpx import AsyncClient


def _make_stl_bytes() -> bytes:
    return b"solid part\nendsolid"


async def _create_shared_project(client: AsyncClient) -> tuple[int, str]:
    resp = await client.post("/api/v1/projects", json={
        "name": "Public Project", "customer": "Acme Co", "due_date": "2026-12-01",
        "notes": "internal note that must never appear on the public page",
    })
    project_id = resp.json()["id"]
    share = (await client.put(f"/api/v1/projects/{project_id}/share")).json()
    return project_id, share["token"]


async def test_public_project_returns_trimmed_shape(client: AsyncClient):
    project_id, token = await _create_shared_project(client)

    resp = await client.get(f"/api/v1/public/projects/{token}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Public Project"
    assert body["customer"] == "Acme Co"
    assert body["due_date"] == "2026-12-01"
    assert body["on_hold"] is False
    assert body["items"] == []
    assert body["parts"] == []
    assert body["links"] == []
    assert body["jobs_total"] == 0
    assert body["jobs_complete"] == 0
    assert body["estimate_seconds_remaining"] is None
    assert "updated_at" in body


async def test_public_project_excludes_internal_fields(client: AsyncClient):
    project_id, token = await _create_shared_project(client)

    resp = await client.get(f"/api/v1/public/projects/{token}")

    body = resp.json()
    for internal_field in (
        "notes", "source_app", "source_user", "source_layout_id",
        "machine_uuid", "process_uuid", "order_id", "result_file_id", "id",
    ):
        assert internal_field not in body


async def test_public_project_404_for_unknown_token(client: AsyncClient):
    resp = await client.get("/api/v1/public/projects/not-a-real-token")
    assert resp.status_code == 404


async def test_public_project_404_after_revoke(client: AsyncClient):
    project_id, token = await _create_shared_project(client)
    await client.delete(f"/api/v1/projects/{project_id}/share")

    resp = await client.get(f"/api/v1/public/projects/{token}")

    assert resp.status_code == 404


async def test_public_project_old_token_404s_after_regenerate(client: AsyncClient):
    project_id, token = await _create_shared_project(client)

    await client.put(f"/api/v1/projects/{project_id}/share")  # regenerate

    resp = await client.get(f"/api/v1/public/projects/{token}")

    assert resp.status_code == 404


async def test_public_project_requires_no_api_key(client: AsyncClient):
    """The public endpoint must work with zero auth headers at all - a plain,
    unauthenticated client, not just the fixture's pre-keyed one."""
    from httpx import AsyncClient as PlainClient, ASGITransport
    from app.main import app

    project_id, token = await _create_shared_project(client)

    async with PlainClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.get(f"/api/v1/public/projects/{token}")

    assert resp.status_code == 200
    assert resp.json()["name"] == "Public Project"


async def test_public_project_includes_item_and_part_and_link_summaries(client: AsyncClient, tmp_path):
    project_id, token = await _create_shared_project(client)

    lib = tmp_path / "library"
    lib.mkdir(exist_ok=True)
    (tmp_path / "filecache").mkdir(exist_ok=True)
    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
    ):
        upload = await client.post("/api/v1/files/upload", files={
            "file": ("part.stl", _make_stl_bytes(), "application/octet-stream"),
        })
        assert upload.status_code == 201
        file_id = upload.json()["id"]

        await client.post(f"/api/v1/projects/{project_id}/items", json={"file_id": file_id, "quantity": 3})
        await client.post(f"/api/v1/projects/{project_id}/parts", json={"name": "M3 bolt", "quantity": 4})
        await client.post(f"/api/v1/projects/{project_id}/links", json={"url": "https://example.com", "label": "Spec sheet"})

        resp = await client.get(f"/api/v1/public/projects/{token}")

    body = resp.json()
    assert body["items"] == [{"name": "part.stl", "quantity": 3, "quantity_completed": 0}]
    assert body["parts"] == [{"name": "M3 bolt", "quantity": 4}]
    assert body["links"] == [{"url": "https://example.com", "label": "Spec sheet"}]
```

This follows the same upload-test pattern as `backend/tests/api/test_projects_api.py`'s
`_setup_project_with_stl` helper — an STL needs no internal zip structure (unlike a real `.3mf`), so
arbitrary bytes with a `.stl` extension upload successfully.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/api/test_public_projects.py -v`
Expected: FAIL — `404 Not Found` for every request (the route doesn't exist yet)

- [ ] **Step 3: Write the public route module**

Create `backend/app/api/routes/public.py`:

```python
"""Public, unauthenticated read-only routes.

This is the ONE deliberate exception (beyond the empty-api_keys-table bootstrap
hatch) to this codebase's "every /api/v1/* route requires Depends(require_scope(...))"
invariant - see docs/agent/conventions.md § Invariants. Keep this module small and
its own file so the one auth-exempt surface in the whole API stays trivially easy to
audit in isolation. Never import require_scope here, and never add a route to this
router that isn't addressed by an unguessable per-resource token.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_session
from ...models import Job, Project
from .projects import _load_items, _load_links, _load_parts, _project_progress

router = APIRouter(prefix="/api/v1/public", tags=["public"])


@router.get("/projects/{token}", summary="Get a project via its public share token")
async def get_public_project(token: str, session: AsyncSession = Depends(get_session)) -> dict:
    proj = (await session.execute(
        select(Project).where(Project.share_token == token)
    )).scalar_one_or_none()
    if proj is None:
        raise HTTPException(404, "Not found")

    items = await _load_items(session, proj.id)
    parts = await _load_parts(session, proj.id)
    links = await _load_links(session, proj.id)
    job_rows = (await session.execute(
        select(Job).where(Job.project_id == proj.id)
    )).scalars().all()
    progress = _project_progress(job_rows)

    return {
        "name": proj.name,
        "customer": proj.customer,
        "due_date": proj.due_date,
        "on_hold": proj.on_hold,
        "items": [
            {"name": i["file_name"], "quantity": i["quantity"], "quantity_completed": i["quantity_completed"]}
            for i in items
        ],
        "parts": [{"name": p["name"], "quantity": p["quantity"]} for p in parts],
        "links": [{"url": l["url"], "label": l["label"]} for l in links],
        "jobs_total": progress["jobs_total"],
        "jobs_complete": progress["jobs_complete"],
        "estimate_seconds_remaining": progress["estimate_seconds_remaining"],
        "updated_at": proj.updated_at,
    }
```

- [ ] **Step 4: Register the router in main.py**

In `backend/app/main.py`, add the import next to the other route imports (line 22-34, insert after the
`projects` import):

```python
from .api.routes.projects import router as projects_router
from .api.routes.public import router as public_router
```

And register it next to the other `include_router` calls (line 149-161, insert after `projects_router`):

```python
app.include_router(projects_router)
app.include_router(public_router)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && pytest tests/api/test_public_projects.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && pytest -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/routes/public.py backend/app/main.py backend/tests/api/test_public_projects.py
git commit -m "Add GET /api/v1/public/projects/{token} - unauthenticated read-only project view"
```

---

### Task 6: Backend docs

**Files:**
- Modify: `docs/agent/conventions.md:30-35`
- Modify: `docs/agent/data-model.md:162-174`
- Modify: `docs/agent/backend-review.md` (append new section)

- [ ] **Step 1: Amend the auth invariant in conventions.md**

In `docs/agent/conventions.md`, in the "Auth is mandatory, not opt-in" bullet (lines 30-35), add a
sentence after the existing bootstrap-hatch sentence:

```markdown
- **Auth is mandatory, not opt-in**: every `/api/v1/*` route (new or existing) must carry
  `Depends(require_scope("<scope>"))`, and the scope must exist in the hardcoded `SCOPES` registry in
  `app/auth.py` — there's no auto-derivation, forgetting either half means an unprotected route or a
  crash on an unknown scope. The bootstrap hatch (open access while `api_keys` is empty) is the only
  built-in exception; don't hand-roll another one. The one other deliberate exception is
  `app/api/routes/public.py`'s `GET /api/v1/public/projects/{token}` — addressed by an unguessable
  per-project token instead of a scope, by design; it's the sole route in that file and the file exists
  specifically to keep that exception isolated and auditable. Frontend: every `api/*.ts` call goes
  through `apiFetch`/`withKeyParam` (`api/client.ts`), never raw `fetch`, or it silently 401s once a key
  exists — except the public share page (`SharedProjectScreen`), which deliberately uses a plain
  `fetch()` since it has no API key and must not touch the authenticated client's 401/403 handlers.
```

- [ ] **Step 2: Document the new columns and endpoints in data-model.md**

In `docs/agent/data-model.md`, in the `### projects` section (lines 162-174), update the field list line
and add a new bullet after the existing ones:

```markdown
### projects
`id, name, customer:str="", order_type:str="internal"` (`"customer"`|`"internal"` — same vocabulary as
`orders.order_type`, but this is the project's own field, not a copy of the linked order's), `on_hold:
bool, due_date?, machine_uuid?, process_uuid?, notes?, result_file_id FK?, order_id FK?, source_app?,
source_user?, source_layout_id?, share_token? (unique), share_token_created_at?, created_at, updated_at`.
- Full CRUD at `/api/v1/projects`. Created by Themis UI (Project Builder) or by Ordinus
  (`source_app="ordinus"`, `source_layout_id=<ordinus BOM id>`).
- `customer`/`order_type`/`on_hold`/`due_date` are the project's own customer-facing fields (set/edited
  directly via the Project Builder), independent of whether it's linked to an `orders` row.
- `order_id`: set by `generate_project` — the internal `orders` row that groups all generated jobs for
  fulfillment tracking. `NULL` until the project is first generated. Not the same thing as the
  project's own `order_type` field above.
- `share_token`/`share_token_created_at`: public share-link state, `NULL` = not shared. Managed via
  `GET`/`PUT`/`DELETE /api/v1/projects/{id}/share` (scope `projects:share`, distinct from
  `projects:write` — see `docs/agent/conventions.md` § Invariants). `PUT` always generates a fresh
  token (create and regenerate are the same operation); `DELETE` clears it (revoke). Read via the
  unauthenticated `GET /api/v1/public/projects/{token}` in `app/api/routes/public.py`, which returns a
  trimmed, customer-facing subset — see that file for the exact field list.
```

(Leave the rest of the `### projects` section, e.g. the `machine_uuid`/`process_uuid` bullet, unchanged.)

- [ ] **Step 3: Add a backend-review.md section flagging public.py**

Append to `docs/agent/backend-review.md` (after the existing `## 9. Tests` section):

```markdown
## 10. Public (unauthenticated) routes

`app/api/routes/public.py` is the one deliberate exception (beyond the empty-`api_keys`-table bootstrap
hatch) to § 4's "every route requires `Depends(require_scope(...))`" rule — its single route,
`GET /api/v1/public/projects/{token}`, is addressed by an unguessable per-project token instead. This is
intentional, not an oversight: don't add `require_scope` to it, and don't add a second route to that
file without re-reading its module docstring first. If you're reviewing a change near this file, the
question isn't "does this have auth" (it deliberately doesn't) but "does the response leak anything
beyond what `docs/agent/data-model.md`'s § projects documents as the intended public field list."
```

- [ ] **Step 4: Commit**

```bash
git add docs/agent/conventions.md docs/agent/data-model.md docs/agent/backend-review.md
git commit -m "Document the projects:share scope and the public.py auth exception"
```

---

### Task 7: Frontend API client functions

**Files:**
- Modify: `frontend/src/api/projects.ts` (add share-management functions)
- Create: `frontend/src/api/public.ts`

No dedicated unit tests for this task — these are thin fetch wrappers with no independent logic,
consistent with this codebase's existing convention (`api/settings.ts`, `api/spoolman.ts` have no
standalone tests either; they're exercised through the screens that use them, which is what Tasks 8 and
10 do).

- [ ] **Step 1: Add share-management functions to `api/projects.ts`**

In `frontend/src/api/projects.ts`, after `getProjectJobs` (line 161-162), add:

```typescript
export interface ProjectShare {
  enabled: boolean;
  token: string | null;
}

export const getProjectShare = (projectId: number) =>
  request<ProjectShare>(`/api/v1/projects/${projectId}/share`);
export const createOrRegenerateProjectShare = (projectId: number) =>
  request<ProjectShare>(`/api/v1/projects/${projectId}/share`, { method: 'PUT' });
export const revokeProjectShare = (projectId: number) =>
  request<ProjectShare>(`/api/v1/projects/${projectId}/share`, { method: 'DELETE' });
```

- [ ] **Step 2: Create the public API client**

Create `frontend/src/api/public.ts`:

```typescript
// Deliberately does NOT use apiFetch (api/client.ts) - this is called from
// SharedProjectScreen, which has no API key and must not touch the authenticated
// client's 401/403 handling. See docs/agent/conventions.md § Invariants.

export interface PublicProjectItem {
  name: string;
  quantity: number;
  quantity_completed: number;
}

export interface PublicProjectPart {
  name: string;
  quantity: number;
}

export interface PublicProjectLink {
  url: string;
  label: string | null;
}

export interface PublicProject {
  name: string;
  customer: string;
  due_date: string | null;
  on_hold: boolean;
  items: PublicProjectItem[];
  parts: PublicProjectPart[];
  links: PublicProjectLink[];
  jobs_total: number;
  jobs_complete: number;
  estimate_seconds_remaining: number | null;
  updated_at: string;
}

export async function getPublicProject(token: string): Promise<PublicProject> {
  const resp = await fetch(`/api/v1/public/projects/${encodeURIComponent(token)}`);
  if (!resp.ok) {
    throw new Error(resp.status === 404 ? 'not-found' : `${resp.status}`);
  }
  return resp.json();
}
```

- [ ] **Step 3: Verify the frontend builds**

Run: `cd frontend && npm run build`
Expected: success, no TypeScript errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/projects.ts frontend/src/api/public.ts
git commit -m "Add frontend API clients for project share management and the public project view"
```

---

### Task 8: `SharedProjectScreen`

**Files:**
- Create: `frontend/src/screens/SharedProjectScreen.tsx`
- Test: Create `frontend/src/screens/SharedProjectScreen.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/screens/SharedProjectScreen.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { SharedProjectScreen } from './SharedProjectScreen';

function renderAtToken(token: string) {
  return render(
    <MemoryRouter initialEntries={[`/share/${token}`]}>
      <Routes>
        <Route path="/share/:token" element={<SharedProjectScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('SharedProjectScreen', () => {
  it('renders project name, customer, and progress on success', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      name: 'Public Project', customer: 'Acme Co', due_date: '2026-12-01', on_hold: false,
      items: [{ name: 'part.3mf', quantity: 3, quantity_completed: 1 }],
      parts: [{ name: 'M3 bolt', quantity: 4 }],
      links: [{ url: 'https://example.com', label: 'Spec sheet' }],
      jobs_total: 3, jobs_complete: 1, estimate_seconds_remaining: 7200,
      updated_at: '2026-09-06T00:00:00Z',
    }), { status: 200 })));

    renderAtToken('valid-token');

    await waitFor(() => expect(screen.getByText('Public Project')).toBeTruthy());
    expect(screen.getByText('Acme Co')).toBeTruthy();
    expect(screen.getByText(/part\.3mf/)).toBeTruthy();
    expect(screen.getByText(/M3 bolt/)).toBeTruthy();
    expect(screen.getByText(/Spec sheet/)).toBeTruthy();
  });

  it('shows a not-found message on 404', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 404 })));

    renderAtToken('revoked-token');

    await waitFor(() => expect(screen.getByText(/invalid or has been revoked/i)).toBeTruthy());
  });

  it('does not send an X-Api-Key header', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      name: 'P', customer: '', due_date: null, on_hold: false,
      items: [], parts: [], links: [], jobs_total: 0, jobs_complete: 0,
      estimate_seconds_remaining: null, updated_at: '2026-09-06T00:00:00Z',
    }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    localStorage.setItem('themis.apiKey', 'thm_should_not_be_sent');

    renderAtToken('valid-token');

    await waitFor(() => expect(screen.getByText('P')).toBeTruthy());
    const [, init] = fetchMock.mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.has('X-Api-Key')).toBe(false);
    localStorage.removeItem('themis.apiKey');
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/screens/SharedProjectScreen.test.tsx`
Expected: FAIL — cannot find module `./SharedProjectScreen` (the component doesn't exist yet)

- [ ] **Step 3: Write the component**

Create `frontend/src/screens/SharedProjectScreen.tsx`:

```typescript
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { getPublicProject, type PublicProject } from '../api/public';

function fmtDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function fmtDuration(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function SharedProjectScreen() {
  const { token } = useParams<{ token: string }>();
  const [project, setProject] = useState<PublicProject | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!token) return;
    let alive = true;
    getPublicProject(token)
      .then(p => { if (alive) setProject(p); })
      .catch(() => { if (alive) setNotFound(true); });
    return () => { alive = false; };
  }, [token]);

  const shellStyle: React.CSSProperties = {
    minHeight: '100vh', display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
    padding: '48px 16px', background: 'var(--bg-0)',
  };
  const cardStyle: React.CSSProperties = {
    width: '100%', maxWidth: 640, background: 'var(--bg-1)', border: '1px solid var(--border-1)',
    borderRadius: 12, padding: 28,
  };

  if (notFound) {
    return (
      <div style={shellStyle}>
        <div style={cardStyle}>
          <p style={{ color: 'var(--text-3)', margin: 0 }}>
            This link is invalid or has been revoked.
          </p>
        </div>
      </div>
    );
  }

  if (!project) {
    return (
      <div style={shellStyle}>
        <div style={cardStyle}>
          <p style={{ color: 'var(--text-3)', margin: 0 }}>Loading…</p>
        </div>
      </div>
    );
  }

  const dueStr = fmtDate(project.due_date);

  return (
    <div style={shellStyle}>
      <div style={cardStyle}>
        <h1 style={{ margin: '0 0 4px', fontSize: 20, fontWeight: 700, color: 'var(--text-1)' }}>
          {project.name}
        </h1>
        {project.customer && (
          <div style={{ fontSize: 14, color: 'var(--text-3)', marginBottom: 4 }}>{project.customer}</div>
        )}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 12, color: 'var(--text-4)', marginBottom: 16 }}>
          {dueStr && <span>Due {dueStr}</span>}
          {project.on_hold && <span style={{ color: 'var(--warn)' }}>On hold</span>}
        </div>

        <div style={{ fontSize: 14, color: 'var(--text-2)', marginBottom: 16 }}>
          {project.jobs_complete} of {project.jobs_total} printed
          {project.estimate_seconds_remaining != null && (
            <span style={{ color: 'var(--text-4)' }}> · ~{fmtDuration(project.estimate_seconds_remaining)} remaining</span>
          )}
        </div>

        {project.items.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-3)', margin: '0 0 8px' }}>Items</h2>
            {project.items.map((item, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '4px 0' }}>
                <span>{item.name}</span>
                <span style={{ color: 'var(--text-4)' }}>{item.quantity_completed} / {item.quantity}</span>
              </div>
            ))}
          </div>
        )}

        {project.parts.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-3)', margin: '0 0 8px' }}>Parts</h2>
            {project.parts.map((part, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '4px 0' }}>
                <span>{part.name}</span>
                <span style={{ color: 'var(--text-4)' }}>x{part.quantity}</span>
              </div>
            ))}
          </div>
        )}

        {project.links.length > 0 && (
          <div>
            <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-3)', margin: '0 0 8px' }}>Links</h2>
            {project.links.map((link, i) => (
              <div key={i} style={{ fontSize: 13, padding: '4px 0' }}>
                <a href={link.url} target="_blank" rel="noreferrer">{link.label || link.url}</a>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/screens/SharedProjectScreen.test.tsx`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Verify the frontend builds**

Run: `cd frontend && npm run build`
Expected: success, no TypeScript errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/screens/SharedProjectScreen.tsx frontend/src/screens/SharedProjectScreen.test.tsx
git commit -m "Add SharedProjectScreen for the public project view"
```

---

### Task 9: Route `/share/:token` outside `AuthGate`

**Files:**
- Modify: `frontend/src/App.tsx:1-10,162-213`
- Test: Create `frontend/src/App.share-route.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/App.share-route.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import App from './App';

beforeEach(() => {
  vi.restoreAllMocks();
  // Deliberately do NOT seed an API key - the whole point of this route is that
  // it works without one.
});

afterEach(() => {
  window.history.pushState({}, '', '/');
});

describe('App - public share route', () => {
  it('renders the shared project page at /share/:token without going through AuthGate', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (typeof url === 'string' && url.includes('/api/v1/public/projects/')) {
        return new Response(JSON.stringify({
          name: 'Public Project', customer: '', due_date: null, on_hold: false,
          items: [], parts: [], links: [], jobs_total: 0, jobs_complete: 0,
          estimate_seconds_remaining: null, updated_at: '2026-09-06T00:00:00Z',
        }), { status: 200 });
      }
      return new Response('{}', { status: 200 });
    }));

    window.history.pushState({}, '', '/share/some-token');
    render(<App />);

    await waitFor(() => expect(screen.getByText('Public Project')).toBeTruthy());
    // AuthGate's "Connecting…" / API-key prompt must never appear on this route.
    expect(screen.queryByText(/Connecting/i)).toBeNull();
    expect(screen.queryByText(/Enter your API key/i)).toBeNull();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/App.share-route.test.tsx`
Expected: FAIL — the route doesn't exist yet, so `App` falls through to `AuthGate`'s bootstrap flow and
never renders "Public Project" (the bootstrap POST to `/api/v1/api-keys` will hit the generic `{}`
fallback, which doesn't produce a usable key, leaving it stuck on the manual-entry/connecting state).

- [ ] **Step 3: Restructure `App.tsx`**

In `frontend/src/App.tsx`, add the import for the new screen near the other screen imports at the top of
the file:

```typescript
import { SharedProjectScreen } from './screens/SharedProjectScreen';
```

Then replace the `export default function App()` block (currently lines 205-213):

```typescript
export default function App() {
  return (
    <BrowserRouter>
      <AuthGate>
        <AppShell />
      </AuthGate>
    </BrowserRouter>
  );
}
```

with:

```typescript
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/share/:token" element={<SharedProjectScreen />} />
        <Route
          path="/*"
          element={
            <AuthGate>
              <AppShell />
            </AuthGate>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
```

`Routes` and `Route` are already imported at the top of the file (used inside `AppShell`), so no new
import is needed for them.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/App.share-route.test.tsx`
Expected: PASS

- [ ] **Step 5: Run the existing App test to confirm no regression**

Run: `cd frontend && npx vitest run src/App.test.tsx`
Expected: PASS (unchanged - the default `/` route still resolves through `AuthGate` → `AppShell` →
redirect to `/queue`)

- [ ] **Step 6: Verify the frontend builds**

Run: `cd frontend && npm run build`
Expected: success, no TypeScript errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.share-route.test.tsx
git commit -m "Route /share/:token outside AuthGate"
```

---

### Task 10: "Share" panel on `ProjectDetailScreen`

**Files:**
- Modify: `frontend/src/screens/ProjectDetailScreen.tsx:1-10,149-160`
- Test: Create `frontend/src/screens/ProjectDetailScreen.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/screens/ProjectDetailScreen.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { ProjectDetailScreen } from './ProjectDetailScreen';

const BASE_PROJECT = {
  id: 42, name: 'Test Project', customer: '', order_type: 'internal', on_hold: false,
  due_date: null, notes: null, result_file_id: null, source_app: null, source_user: null,
  source_layout_id: null, created_at: '2026-09-06T00:00:00Z', updated_at: '2026-09-06T00:00:00Z',
  items: [], links: [], parts: [], jobs_total: 0, jobs_complete: 0,
  estimate_filament_grams_total: null, estimate_seconds_total: null,
  estimate_filament_grams_remaining: null, estimate_seconds_remaining: null,
  actual_filament_grams: null, actual_seconds: null,
};

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={['/projects/42']}>
      <Routes>
        <Route path="/projects/:id" element={<ProjectDetailScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

function mockFetch(shareState: { enabled: boolean; token: string | null }) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/share')) {
      return new Response(JSON.stringify(shareState), { status: 200 });
    }
    if (url.includes('/jobs')) {
      return new Response('[]', { status: 200 });
    }
    if (url.match(/\/api\/v1\/projects\/42$/)) {
      return new Response(JSON.stringify(BASE_PROJECT), { status: 200 });
    }
    return new Response('{}', { status: 200 });
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('ProjectDetailScreen share panel', () => {
  it('shows a Create share link button when no link exists', async () => {
    vi.stubGlobal('fetch', mockFetch({ enabled: false, token: null }));

    renderScreen();
    await waitFor(() => screen.getByText('Test Project'));
    await userEvent.click(screen.getByRole('button', { name: /share/i }));

    expect(await screen.findByRole('button', { name: /create share link/i })).toBeTruthy();
  });

  it('shows the share URL and Copy/Regenerate/Revoke when a link exists', async () => {
    vi.stubGlobal('fetch', mockFetch({ enabled: true, token: 'abc123' }));

    renderScreen();
    await waitFor(() => screen.getByText('Test Project'));
    await userEvent.click(screen.getByRole('button', { name: /share/i }));

    await waitFor(() => expect(screen.getByDisplayValue(/abc123/)).toBeTruthy());
    expect(screen.getByRole('button', { name: /copy/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /regenerate/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /revoke/i })).toBeTruthy();
  });

  it('clicking Create share link calls PUT and displays the new token', async () => {
    const fetchMock = mockFetch({ enabled: false, token: null });
    vi.stubGlobal('fetch', fetchMock);

    renderScreen();
    await waitFor(() => screen.getByText('Test Project'));
    await userEvent.click(screen.getByRole('button', { name: /share/i }));
    await userEvent.click(await screen.findByRole('button', { name: /create share link/i }));

    const putCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
    expect(putCall).toBeTruthy();
    expect(putCall![0]).toBe('/api/v1/projects/42/share');
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/screens/ProjectDetailScreen.test.tsx`
Expected: FAIL — no "Share" button exists yet

- [ ] **Step 3: Add the share panel to `ProjectDetailScreen`**

In `frontend/src/screens/ProjectDetailScreen.tsx`, update the import block (lines 1-10) to add:

```typescript
import {
  getProject, getProjectJobs, generateProject, updateProjectPart,
  getProjectShare, createOrRegenerateProjectShare, revokeProjectShare,
  type Project, type ProjectJob, type ProjectShare,
} from '../api/projects';
```

Add state and handlers inside the `ProjectDetailScreen` function, after the existing `processPreset`
state declaration (around line 47):

```typescript
  const [share, setShare] = useState<ProjectShare | null>(null);
  const [showShare, setShowShare] = useState(false);
  const [shareBusy, setShareBusy] = useState(false);
  const [confirmAction, setConfirmAction] = useState<'regenerate' | 'revoke' | null>(null);
```

Add a loader for the share state, alongside the existing `reload` callback (after its definition, around
line 53):

```typescript
  const loadShare = useCallback(() => {
    if (!projectId) return;
    getProjectShare(projectId).then(setShare).catch(console.error);
  }, [projectId]);

  useEffect(() => { loadShare(); }, [loadShare]);

  async function handleCreateOrRegenerateShare() {
    if (!projectId) return;
    setShareBusy(true);
    try {
      setShare(await createOrRegenerateProjectShare(projectId));
    } finally {
      setShareBusy(false);
      setConfirmAction(null);
    }
  }

  async function handleRevokeShare() {
    if (!projectId) return;
    setShareBusy(true);
    try {
      setShare(await revokeProjectShare(projectId));
    } finally {
      setShareBusy(false);
      setConfirmAction(null);
    }
  }
```

Add the "Share" button next to the existing "Edit" button (in the header actions `div`, line 149-160):

```typescript
          <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
            <button className="btn sm" onClick={() => setShowShare(v => !v)}>
              Share
            </button>
            <button className="btn sm" onClick={() => navigate(`/projects/${project.id}/edit`)}>
              Edit
            </button>
            <button
              className="btn primary sm"
              onClick={() => { setShowPrinterPicker(v => !v); setGenerateError(''); setGenerateResult(null); }}
              disabled={generating || project.items.length === 0}
            >
              {generating ? 'Generating…' : 'Generate…'}
            </button>
          </div>
```

Add the share panel itself immediately after the header card's closing `</div>` (i.e. right after the
header card block that contains the buttons above - insert a new sibling `card` div):

```typescript
      {showShare && (
        <div className="card" style={{ padding: 20 }}>
          {!share ? (
            <p style={{ color: 'var(--text-3)', margin: 0 }}>Loading…</p>
          ) : !share.enabled ? (
            <div>
              <p style={{ color: 'var(--text-3)', marginTop: 0 }}>
                This project isn't shared. Anyone with the link can view its status - no login required.
              </p>
              <button className="btn primary sm" onClick={handleCreateOrRegenerateShare} disabled={shareBusy}>
                {shareBusy ? 'Creating…' : 'Create share link'}
              </button>
            </div>
          ) : (
            <div>
              <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                <input
                  className="input"
                  readOnly
                  value={`${window.location.origin}/share/${share.token}`}
                  style={{ flex: 1 }}
                  onFocus={e => e.target.select()}
                />
                <button
                  className="btn sm"
                  onClick={() => navigator.clipboard.writeText(`${window.location.origin}/share/${share.token}`)}
                >
                  Copy
                </button>
              </div>
              {confirmAction === null ? (
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn sm" onClick={() => setConfirmAction('regenerate')} disabled={shareBusy}>
                    Regenerate
                  </button>
                  <button className="btn sm" onClick={() => setConfirmAction('revoke')} disabled={shareBusy}>
                    Revoke
                  </button>
                </div>
              ) : (
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span style={{ fontSize: 13, color: 'var(--warn)' }}>
                    {confirmAction === 'regenerate'
                      ? 'This invalidates the current link. Continue?'
                      : 'This disables the current link. Continue?'}
                  </span>
                  <button
                    className="btn primary sm"
                    disabled={shareBusy}
                    onClick={confirmAction === 'regenerate' ? handleCreateOrRegenerateShare : handleRevokeShare}
                  >
                    Confirm
                  </button>
                  <button className="btn sm" onClick={() => setConfirmAction(null)} disabled={shareBusy}>
                    Cancel
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/screens/ProjectDetailScreen.test.tsx`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Verify the frontend builds**

Run: `cd frontend && npm run build`
Expected: success, no TypeScript errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/screens/ProjectDetailScreen.tsx frontend/src/screens/ProjectDetailScreen.test.tsx
git commit -m "Add Share panel to ProjectDetailScreen"
```

---

### Task 11: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite**

Run: `cd backend && pytest -v`
Expected: PASS, no failures

- [ ] **Step 2: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, no failures

- [ ] **Step 3: Run the frontend production build**

Run: `cd frontend && npm run build`
Expected: success, no TypeScript errors

- [ ] **Step 4: Manually smoke-test the golden path**

With the dev servers running (`themis-start` skill, or `uvicorn app.main:app --reload --port 8001` from
`backend/` and `npm run dev` from `frontend/`):
1. Open a project's detail page, click "Share" → "Create share link".
2. Copy the shown URL, open it in a private/incognito browser window (no stored API key).
3. Confirm the project's name, customer, progress, items, parts, and links render, and that no notes or
   internal fields appear.
4. Back in the authenticated tab, click "Regenerate", confirm. Reload the incognito tab on the *old*
   URL — confirm it now shows "This link is invalid or has been revoked."
5. Open the *new* URL in the incognito tab — confirm it works.
6. Back in the authenticated tab, click "Revoke", confirm. Reload the incognito tab — confirm it now
   404s with the same message.

This step has no automated check; note the result in your final report to the user.

- [ ] **Step 5: No commit for this task** — it's verification only, nothing to add to git.

---

## Post-implementation

Per this project's CLAUDE.md "Development workflow": after all tasks pass, dispatch exactly one fresh,
non-fork reviewer subagent (base = `develop`, head = current branch tip), pointing it at
`docs/agent/backend-review.md` and `docs/agent/frontend-review.md`. Pay particular attention to:
- Byte-for-byte field-name agreement between `public.py`'s response and `api/public.ts`'s `PublicProject`
  interface (backend-review.md § 2's named risk).
- That `public.py` truly has no `require_scope` dependency anywhere, and that no other route was
  accidentally added to that file.
- That `SharedProjectScreen` never imports `apiFetch`.

Address any Critical/Important findings, then write `.claude/review-state.json` before opening a PR
into `develop`, per the repo's PR-review gate.
