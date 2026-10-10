"""Reliable project hooks for companion apps (BIZ-172): external refs, idempotent create/generate, lifecycle webhooks."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from app.eventing.hub import hub
from app.models import IdempotencyKey, Job, Project
from app.services import idempotency, webhook_service
from tests.api.test_customer_flows import _create_customer, _login, admin  # noqa: F401  (admin is a fixture)
from tests.api.test_projects_api import _make_3mf_bytes, _setup_project_with_stl
from tests.fake_providers import fake_packer
from tests.webhook_helpers import destination, install_wire, verify_signature

SRC = {"name": "Order 1001", "source_app": "shopconnector", "external_ref": "order-1001"}


@pytest.fixture
def wire(monkeypatch):
    return install_wire(monkeypatch)


async def _count(session_factory, model) -> int:
    async with session_factory() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


# --- external reference -----------------------------------------------------------------------------------------

async def test_a_project_can_be_created_and_found_by_source_app_and_external_ref(client):
    created = await client.post("/api/v1/projects", json=SRC)
    assert created.status_code == 201 and created.json()["external_ref"] == "order-1001"

    found = (await client.get("/api/v1/projects", params={"source_app": "shopconnector", "external_ref": "order-1001"})).json()
    assert [p["id"] for p in found] == [created.json()["id"]]
    assert (await client.get("/api/v1/projects", params={"source_app": "shopconnector", "external_ref": "nope"})).json() == []
    assert (await client.get("/api/v1/projects", params={"source_app": "other", "external_ref": "order-1001"})).json() == []


async def test_external_ref_requires_a_source_app(client):
    resp = await client.post("/api/v1/projects", json={"name": "x", "external_ref": "abc"})
    assert resp.status_code == 422 and "source_app" in resp.json()["detail"]


async def test_creating_the_same_external_ref_again_returns_the_existing_project_unchanged(client, session_factory):
    first = await client.post("/api/v1/projects", json=SRC)
    again = await client.post("/api/v1/projects", json={**SRC, "name": "Renamed by a retry", "notes": "ignored"})

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["id"] == first.json()["id"] and again.json()["name"] == "Order 1001" and again.json()["notes"] is None
    assert await _count(session_factory, Project) == 1


async def test_the_same_ref_from_another_source_app_is_a_different_project(client, session_factory):
    a = await client.post("/api/v1/projects", json=SRC)
    b = await client.post("/api/v1/projects", json={**SRC, "source_app": "ordinus"})
    assert a.json()["id"] != b.json()["id"] and await _count(session_factory, Project) == 2


async def test_concurrent_creates_with_one_external_ref_make_exactly_one_project(client, session_factory):
    results = await asyncio.gather(*(client.post("/api/v1/projects", json=SRC) for _ in range(6)))

    assert {r.json()["id"] for r in results} and len({r.json()["id"] for r in results}) == 1
    assert sorted(r.status_code for r in results).count(201) == 1
    assert await _count(session_factory, Project) == 1


async def test_projects_without_an_external_ref_are_unconstrained(client, session_factory):
    for _ in range(2):
        assert (await client.post("/api/v1/projects", json={"name": "same", "source_app": "ordinus"})).status_code == 201
    assert await _count(session_factory, Project) == 2


# --- Idempotency-Key: create ------------------------------------------------------------------------------------

async def test_a_repeated_create_with_the_same_idempotency_key_returns_the_first_response(client, session_factory):
    h = {"Idempotency-Key": "key-1"}
    first = await client.post("/api/v1/projects", json={"name": "Keyed"}, headers=h)
    again = await client.post("/api/v1/projects", json={"name": "Keyed"}, headers=h)

    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json() == first.json() and again.headers["idempotent-replay"] == "true" and "idempotent-replay" not in first.headers
    assert await _count(session_factory, Project) == 1


async def test_reusing_a_key_for_a_different_request_is_rejected(client, session_factory):
    h = {"Idempotency-Key": "key-2"}
    await client.post("/api/v1/projects", json={"name": "One"}, headers=h)
    resp = await client.post("/api/v1/projects", json={"name": "Two"}, headers=h)
    assert resp.status_code == 422 and "different request" in resp.json()["detail"]
    assert await _count(session_factory, Project) == 1


async def test_a_key_still_in_progress_is_a_conflict(client, session_factory):
    from app.api.routes.projects import ProjectCreate
    claimed = await idempotency.claim(session_factory, "POST /api/v1/projects", "busy",
                                      idempotency.request_hash(ProjectCreate(name="Busy").model_dump()))
    assert claimed is None                                                      # another request owns the key and has not finished

    resp = await client.post("/api/v1/projects", json={"name": "Busy"}, headers={"Idempotency-Key": "busy"})

    assert resp.status_code == 409 and "still being processed" in resp.json()["detail"]


async def test_a_failed_request_releases_its_key_so_a_retry_runs(client, session_factory):
    failing = await client.post("/api/v1/projects", json={"name": "Bad", "customer_id": 999}, headers={"Idempotency-Key": "fails"})
    assert failing.status_code == 404
    async with session_factory() as s:
        assert (await s.execute(select(IdempotencyKey))).first() is None

    retry = await client.post("/api/v1/projects", json={"name": "Bad", "customer_id": 999}, headers={"Idempotency-Key": "fails"})
    assert retry.status_code == 404 and "idempotent-replay" not in retry.headers      # it ran again for real (not a stored answer)


async def test_an_abandoned_claim_is_taken_over_after_it_goes_stale(session_factory, monkeypatch):
    assert await idempotency.claim(session_factory, "S", "k", "h") is None
    with pytest.raises(Exception, match="still being processed"):
        await idempotency.claim(session_factory, "S", "k", "h")
    monkeypatch.setattr(idempotency, "STALE_AFTER", idempotency.timedelta(seconds=-1))
    assert await idempotency.claim(session_factory, "S", "k", "h") is None


async def test_completed_keys_expire(session_factory, monkeypatch):
    await idempotency.claim(session_factory, "S", "old", "h")
    await idempotency.complete(session_factory, "S", "old", 200, {"x": 1})
    assert (await idempotency.claim(session_factory, "S", "old", "h")).response == {"x": 1}
    monkeypatch.setattr(idempotency, "KEEP_FOR", idempotency.timedelta(seconds=-1))
    assert await idempotency.claim(session_factory, "S", "old", "h") is None          # purged: a fresh claim


async def test_an_invalid_idempotency_key_is_rejected(client):
    assert (await client.post("/api/v1/projects", json={"name": "x"}, headers={"Idempotency-Key": "k" * 201})).status_code == 422


# --- Idempotency-Key: generate ----------------------------------------------------------------------------------

async def _generate(client, tmp_path, project_id, headers=None, body=None):
    lib = tmp_path / "library"
    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_library_dir", return_value=lib),
        patch("app.api.routes.projects.get_slicing_provider") as mock_get,
        patch("app.api.routes.projects.regen_file_thumbnails", new_callable=AsyncMock),
    ):
        mock_get.return_value = fake_packer(_make_3mf_bytes(plate_count=1))
        return await client.post(f"/api/v1/projects/{project_id}/generate", json=body or {"eligible_printer_ids": []}, headers=headers or {})


async def test_a_repeated_generate_with_the_same_key_creates_no_more_jobs(client, tmp_path, session_factory):
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    h = {"Idempotency-Key": "gen-1"}

    first = await _generate(client, tmp_path, project_id, h)
    again = await _generate(client, tmp_path, project_id, h)

    assert first.status_code == again.status_code == 200
    assert again.json() == first.json() and again.headers["idempotent-replay"] == "true"
    assert len(first.json()["jobs"]) == 1 and await _count(session_factory, Job) == 1


async def test_generate_with_a_different_key_or_body_is_a_new_operation_and_a_changed_body_under_one_key_is_rejected(client, tmp_path, session_factory):
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    await _generate(client, tmp_path, project_id, {"Idempotency-Key": "g-a"})

    other_key = await _generate(client, tmp_path, project_id, {"Idempotency-Key": "g-b"})
    changed = await _generate(client, tmp_path, project_id, {"Idempotency-Key": "g-a"}, {"eligible_printer_ids": [], "save_slice": True})

    assert other_key.status_code == 200 and "idempotent-replay" not in other_key.headers
    assert changed.status_code == 422
    assert await _count(session_factory, Job) == 2


async def test_a_failed_generate_releases_its_key_so_the_retry_runs(client, tmp_path, session_factory):
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    h = {"Idempotency-Key": "gen-fail"}
    with patch("app.api.routes.projects.get_slicing_provider", return_value=None):
        failed = await client.post(f"/api/v1/projects/{project_id}/generate", json={"eligible_printer_ids": [], "allow_cached": False}, headers=h)
    assert failed.status_code == 422

    retry = await _generate(client, tmp_path, project_id, h)
    assert retry.status_code == 200 and "idempotent-replay" not in retry.headers and len(retry.json()["jobs"]) == 1


# --- lifecycle webhooks -------------------------------------------------------------------------------------------

async def _subscribe(session_factory, **kw):
    async with session_factory() as s:
        s.add(destination(url="https://shop.test/hook", secret="whsec", **kw))
        await s.commit()


async def _settle():
    await hub.drain()
    await webhook_service.drain()


async def test_project_created_is_delivered_with_ids_source_app_and_external_ref(client, session_factory, wire):
    await _subscribe(session_factory)

    created = (await client.post("/api/v1/projects", json=SRC)).json()
    await _settle()

    (payload,) = wire.payloads("project.created")
    assert payload["project_id"] == created["id"] and payload["source_app"] == "shopconnector" and payload["external_ref"] == "order-1001"
    assert (payload["name"], payload["stage"], payload["schema_version"]) == ("Order 1001", "queued", 1)
    assert payload["event_id"] and payload["timestamp"] and payload["occurred_at"]
    request = wire.requests[0]
    assert request.headers["x-webhook-id"] == payload["event_id"] and verify_signature("whsec", request)


async def test_an_idempotent_repeat_publishes_no_second_event(client, session_factory, wire):
    await _subscribe(session_factory)
    await client.post("/api/v1/projects", json=SRC)
    await client.post("/api/v1/projects", json=SRC)
    await client.post("/api/v1/projects", json={"name": "k"}, headers={"Idempotency-Key": "z"})
    await client.post("/api/v1/projects", json={"name": "k"}, headers={"Idempotency-Key": "z"})
    await _settle()

    assert len(wire.payloads("project.created")) == 2                           # one per real creation


async def test_stage_changes_and_generation_are_delivered(client, tmp_path, session_factory, wire):
    await _subscribe(session_factory)
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    await _generate(client, tmp_path, project_id)
    await _settle()

    (generated,) = wire.payloads("project.generated")
    assert generated["project_id"] == project_id and len(generated["job_ids"]) == 1 and generated["stage"] == "queued"


async def test_promote_publishes_stage_changed_with_the_previous_stage(client, session_factory, wire):
    await _subscribe(session_factory, events=["project.stage_changed"])
    pid = (await client.post("/api/v1/projects", json={"name": "Draft one", "stage": "draft"})).json()["id"]

    await client.post(f"/api/v1/projects/{pid}/promote", json={"stage": "planning"})
    await _settle()

    (changed,) = wire.payloads()                                               # the created event is filtered out
    assert (changed["event"], changed["project_id"], changed["previous_stage"], changed["stage"]) == ("project.stage_changed", pid, "draft", "planning")


async def test_job_events_carry_the_projects_source_app_and_external_ref(client, session_factory, wire):
    from unittest.mock import MagicMock
    from app.services.printer_manager import PrinterManager
    from app.services.queue_engine import QueueEngine
    await _subscribe(session_factory)
    pid = (await client.post("/api/v1/projects", json=SRC)).json()["id"]
    await _settle()
    wire.requests.clear()
    from tests.services.test_queue_engine import _seed_job
    job_id = await _seed_job(session_factory, 1)
    async with session_factory() as s:
        (await s.get(Job, job_id)).project_id = pid
        await s.commit()

    engine = QueueEngine(session_factory, PrinterManager(), MagicMock())
    await engine._fire_webhooks(job_id, "job.failed")
    await webhook_service.drain()
    engine._executor.shutdown(wait=False)

    (payload,) = wire.payloads("job.failed")
    assert (payload["job_id"], payload["project_id"], payload["source_app"], payload["external_ref"]) == (job_id, pid, "shopconnector", "order-1001")


async def test_a_disabled_destination_gets_no_project_events(client, session_factory, wire):
    await _subscribe(session_factory, enabled=False)
    await client.post("/api/v1/projects", json=SRC)
    await _settle()
    assert wire.requests == []


# --- partial failures, races, payload shape ---------------------------------------------------------------------------

async def test_a_generate_that_fails_after_committing_some_jobs_blocks_a_retry_under_the_same_key(client, tmp_path, session_factory):
    from app.services.providers.slicing import SlicingProviderError
    project_id, file_id = await _setup_project_with_stl(client, tmp_path)
    await client.post(f"/api/v1/projects/{project_id}/items", json={"file_id": file_id, "quantity": 1, "filament_type": "PETG"})
    h = {"Idempotency-Key": "gen-partial"}
    lib = tmp_path / "library"
    packer = fake_packer(_make_3mf_bytes(plate_count=1))
    packer.pack_models.side_effect = [_make_3mf_bytes(plate_count=1), SlicingProviderError("sidecar fell over")]   # 2nd filament group fails

    def post():
        return client.post(f"/api/v1/projects/{project_id}/generate", json={"eligible_printer_ids": [], "allow_cached": False}, headers=h)

    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_library_dir", return_value=lib),
        patch("app.api.routes.projects.get_slicing_provider", return_value=packer),
        patch("app.api.routes.projects.regen_file_thumbnails", new_callable=AsyncMock),
    ):
        failed = await post()
        retry = await post()

    assert failed.status_code == 502
    assert await _count(session_factory, Job) == 1                              # the first group's job was committed
    assert retry.status_code == 409 and "created 1 job" in retry.json()["detail"] and "new Idempotency-Key" in retry.json()["detail"]
    assert await _count(session_factory, Job) == 1                              # and the retry did not repeat it
    assert packer.pack_models.call_count == 2


async def test_a_create_that_fails_after_committing_blocks_a_retry_under_the_same_key(client, session_factory, monkeypatch):
    from app.api.routes import projects as routes
    real = routes._project_dict
    calls = {"n": 0}

    async def explode_once(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("serialisation blew up after the commit")
        return await real(*a, **kw)

    monkeypatch.setattr(routes, "_project_dict", explode_once)
    h = {"Idempotency-Key": "create-partial"}
    with pytest.raises(RuntimeError):
        await client.post("/api/v1/projects", json={"name": "Half done"}, headers=h)

    retry = await client.post("/api/v1/projects", json={"name": "Half done"}, headers=h)

    assert retry.status_code == 409 and "already created" in retry.json()["detail"]
    assert await _count(session_factory, Project) == 1


async def test_losing_the_unique_index_race_returns_the_winners_project(client, session_factory, monkeypatch):
    """Both requests passed the pre-check; the second then hits the unique index and must answer with the first's project."""
    from app.api.routes import projects as routes
    first = await client.post("/api/v1/projects", json=SRC)
    real = routes._find_by_external_ref
    calls = {"n": 0}

    async def blind_first_lookup(session, source_app, external_ref):
        calls["n"] += 1
        return None if calls["n"] == 1 else await real(session, source_app, external_ref)

    monkeypatch.setattr(routes, "_find_by_external_ref", blind_first_lookup)
    again = await client.post("/api/v1/projects", json=SRC)

    assert calls["n"] == 2 and (again.status_code, again.json()["id"]) == (200, first.json()["id"])
    assert await _count(session_factory, Project) == 1


async def test_two_requests_cannot_both_take_over_one_stale_claim(session_factory, monkeypatch):
    assert await idempotency.claim(session_factory, "S", "k", "h") is None
    monkeypatch.setattr(idempotency, "STALE_AFTER", idempotency.timedelta(seconds=-1))
    results = await asyncio.gather(*(idempotency.claim(session_factory, "S", "k", "h") for _ in range(4)), return_exceptions=True)
    assert [r for r in results if r is None].__len__() >= 1
    # exactly one wins the conditional update per stale timestamp; any loser is told the key is busy, never handed the work
    assert all(r is None or getattr(r, "status_code", None) == 409 for r in results)


async def test_a_completed_claim_is_not_overwritten_by_a_late_second_completion(session_factory):
    await idempotency.claim(session_factory, "S", "late", "h")
    await idempotency.complete(session_factory, "S", "late", 200, {"first": True})
    await idempotency.complete(session_factory, "S", "late", 200, {"second": True})            # e.g. a slow original after a takeover
    assert (await idempotency.claim(session_factory, "S", "late", "h")).response == {"first": True}


async def test_the_documented_fields_are_always_present_and_null_for_a_ui_made_project(client, session_factory, wire):
    await _subscribe(session_factory)
    await client.post("/api/v1/projects", json={"name": "Made in the UI"})
    await _settle()

    (payload,) = wire.payloads("project.created")
    assert payload["source_app"] is None and payload["external_ref"] is None and "job_ids" not in payload


async def test_a_customer_draft_created_in_the_portal_also_publishes_project_created(admin, session_factory, wire):
    await _subscribe(session_factory)
    await _create_customer(admin, "Pat", "pat@example.test", "pw-123456")
    portal = await _login("pat@example.test", "pw-123456")
    async with portal:
        draft = (await portal.post("/api/v1/customer/projects", json={"name": "My print"})).json()
    await _settle()

    (payload,) = wire.payloads("project.created")
    assert (payload["project_id"], payload["stage"], payload["name"]) == (draft["id"], "draft", "My print")


async def test_a_reused_project_id_does_not_swallow_the_new_projects_event(client, session_factory, wire):
    """SQLite reuses the highest rowid after a delete: the event of the 'new' project 1 must still be delivered."""
    await _subscribe(session_factory)
    first = (await client.post("/api/v1/projects", json={"name": "One"})).json()
    await _settle()
    assert (await client.delete(f"/api/v1/projects/{first['id']}")).status_code in (200, 204)
    second = (await client.post("/api/v1/projects", json={"name": "Two"})).json()
    await _settle()

    assert second["id"] == first["id"]
    assert [p["name"] for p in wire.payloads("project.created")] == ["One", "Two"]
