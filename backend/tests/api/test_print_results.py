"""BIZ-364: print results on plate clear. Clearing a plate records what printed per part; failed parts are requeued as a new pack
in the same print group (same printers and profiles); a part that has failed on `part_retry_limit` different printers is held for
a person (Reprint / Drop); every failure is in the project's failure log."""
import json
from unittest.mock import patch

from sqlalchemy import select

from app.models import Job, JobPrinterConfig, Printer, ProjectItem
from tests.fake_providers import FakeProjectConstructor
import pytest

# These tests exercise packing at generate: pin new groups to "fixed" (the shipped default is "pool").
pytestmark = pytest.mark.usefixtures("fixed_groups")



async def _setup(client, upload_3mf, create_printer, tmp_path, qty=3):
    """A project with one STL × qty generated onto printer A (and B eligible). Returns (project_id, item_id, job_id, a, b)."""
    a = await create_printer(name="A")
    b = await create_printer(name="B")
    pid = (await client.post("/api/v1/projects", json={"name": "Results"})).json()["id"]
    fid = await upload_3mf(filename="part.stl", data=b"solid part\nendsolid\n")
    item = (await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "quantity": qty})).json()["id"]
    r = await _generate(client, pid, tmp_path, eligible_printer_ids=[a, b], process_preset="0.20mm")
    return pid, item, r.json()["jobs"][0]["id"], a, b


async def _generate(client, pid, tmp_path, **body):
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        r = await client.post(f"/api/v1/projects/{pid}/generate", json=body)
    assert r.status_code == 200, r.text
    return r


async def _printed(session_factory, job_id, printer_id):
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.status, job.assigned_printer_id = "complete", printer_id
        (await s.get(Printer, printer_id)).awaiting_plate_clear = True
        await s.commit()


async def _clear(client, tmp_path, printer_id, job_id, failures: dict[int, int]):
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        return await client.post(f"/api/v1/printers/{printer_id}/plate-cleared", json={
            "job_id": job_id, "results": [{"project_item_id": k, "quantity_failed": v} for k, v in failures.items()]})


async def _project_jobs(session_factory, pid):
    async with session_factory() as s:
        return (await s.execute(select(Job).where(Job.project_id == pid).order_by(Job.id))).scalars().all()


async def test_clearing_with_a_partial_failure_records_it_and_requeues_exactly_the_failed_parts(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    pid, item, job_id, a, b = await _setup(client, upload_3mf, create_printer, tmp_path)
    await _printed(session_factory, job_id, a)

    r = await _clear(client, tmp_path, a, job_id, {item: 1})
    assert r.status_code == 200, r.text
    assert r.json()["requeued_job_ids"] and r.json()["held"] == []

    async with session_factory() as s:
        pi = await s.get(ProjectItem, item)
        assert (pi.quantity_completed, pi.quantity_failed, pi.quantity_held) == (2, 1, 0)
        assert (await s.get(Printer, a)).awaiting_plate_clear is False
    jobs = await _project_jobs(session_factory, pid)
    new = [j for j in jobs if j.id in r.json()["requeued_job_ids"]]
    assert [json.loads(j.project_item_quantities) for j in new] == [{str(item): 1}]
    async with session_factory() as s:
        cfgs = (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == new[0].id))).scalars().all()
    assert sorted((c.printer_id, c.print_profile) for c in cfgs) == [(a, "0.20mm"), (b, "0.20mm")]   # same printers + profile

    log = (await client.get(f"/api/v1/projects/{pid}/failures")).json()
    from tests.api.test_response_contracts import assert_carries
    assert_carries("project_failure", log[0])
    assert [(e["project_item_id"], e["printer_id"], e["quantity_failed"], e["resolution"]) for e in log] == [(item, a, 1, "requeued")]
    assert log[0]["file_name"] == "part.stl" and log[0]["printer_name"] == "A" and log[0]["created_at"]


async def test_a_part_failing_on_two_printers_is_held_and_can_be_reprinted_or_dropped(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    pid, item, job_id, a, b = await _setup(client, upload_3mf, create_printer, tmp_path, qty=2)
    await _printed(session_factory, job_id, a)
    retry = (await _clear(client, tmp_path, a, job_id, {item: 1})).json()["requeued_job_ids"][0]
    await _printed(session_factory, retry, b)

    r = await _clear(client, tmp_path, b, retry, {item: 1})                 # 2nd printer → the default limit of 2
    assert r.json()["requeued_job_ids"] == [] and r.json()["held"] == [{"project_item_id": item, "quantity": 1}]
    async with session_factory() as s:
        assert (await s.get(ProjectItem, item)).quantity_held == 1
    log = (await client.get(f"/api/v1/projects/{pid}/failures")).json()
    assert [(e["printer_id"], e["resolution"]) for e in log] == [(a, "requeued"), (b, "held")]

    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        rp = await client.post(f"/api/v1/projects/{pid}/items/{item}/held", json={"action": "reprint"})
    assert rp.status_code == 200, rp.text
    assert len(rp.json()["requeued_job_ids"]) == 1
    async with session_factory() as s:
        assert (await s.get(ProjectItem, item)).quantity_held == 0
    assert [e["resolution"] for e in (await client.get(f"/api/v1/projects/{pid}/failures")).json()] == ["requeued", "reprinted"]


async def test_dropping_a_held_part(client, upload_3mf, create_printer, session_factory, tmp_path):
    await client.put("/api/v1/settings/queue", json={"part_retry_limit": 1})
    assert (await client.get("/api/v1/settings/queue")).json()["part_retry_limit"] == 1
    pid, item, job_id, a, _ = await _setup(client, upload_3mf, create_printer, tmp_path, qty=1)
    await _printed(session_factory, job_id, a)
    assert (await _clear(client, tmp_path, a, job_id, {item: 1})).json()["held"] == [{"project_item_id": item, "quantity": 1}]
    r = await client.post(f"/api/v1/projects/{pid}/items/{item}/held", json={"action": "drop"})
    assert r.status_code == 200 and r.json()["requeued_job_ids"] == []
    async with session_factory() as s:
        assert (await s.get(ProjectItem, item)).quantity_held == 0
    assert [e["resolution"] for e in (await client.get(f"/api/v1/projects/{pid}/failures")).json()] == ["dropped"]
    assert (await client.post(f"/api/v1/projects/{pid}/items/{item}/held", json={"action": "drop"})).status_code == 409


async def test_failing_twice_on_the_same_printer_is_not_held(client, upload_3mf, create_printer, session_factory, tmp_path):
    pid, item, job_id, a, _ = await _setup(client, upload_3mf, create_printer, tmp_path, qty=1)
    await _printed(session_factory, job_id, a)
    retry = (await _clear(client, tmp_path, a, job_id, {item: 1})).json()["requeued_job_ids"][0]
    await _printed(session_factory, retry, a)
    r = await _clear(client, tmp_path, a, retry, {item: 1})
    assert r.json()["held"] == [] and len(r.json()["requeued_job_ids"]) == 1


async def test_nothing_failed_requeues_nothing_and_a_plain_clear_still_works(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    pid, item, job_id, a, b = await _setup(client, upload_3mf, create_printer, tmp_path)
    await _printed(session_factory, job_id, a)
    r = await _clear(client, tmp_path, a, job_id, {})
    assert r.json()["requeued_job_ids"] == [] and r.json()["held"] == []
    async with session_factory() as s:
        assert (await s.get(ProjectItem, item)).quantity_completed == 3
    async with session_factory() as s:
        (await s.get(Printer, b)).awaiting_plate_clear = True
        await s.commit()
    assert (await client.post(f"/api/v1/printers/{b}/plate-cleared")).status_code == 200


async def test_results_for_a_job_of_another_printer_are_refused(client, upload_3mf, create_printer, session_factory, tmp_path):
    pid, item, job_id, a, b = await _setup(client, upload_3mf, create_printer, tmp_path)
    await _printed(session_factory, job_id, a)
    assert (await _clear(client, tmp_path, b, job_id, {item: 1})).status_code == 422


async def test_plate_results_offers_the_unreported_project_job_then_nothing(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    pid, item, job_id, a, _ = await _setup(client, upload_3mf, create_printer, tmp_path)
    assert (await client.get(f"/api/v1/printers/{a}/plate-results")).json()["job_id"] is None      # still queued
    await _printed(session_factory, job_id, a)
    from tests.api.test_response_contracts import assert_carries
    r = (await client.get(f"/api/v1/printers/{a}/plate-results")).json()
    assert_carries("plate_results", r)
    assert r == {"job_id": job_id, "project_id": pid, "parts": [{"project_item_id": item, "file_name": "part.stl", "quantity": 3}],
                 "test_print_waiver_id": None, "print_failed": False}
    await _clear(client, tmp_path, a, job_id, {})
    assert (await client.get(f"/api/v1/printers/{a}/plate-results")).json()["job_id"] is None      # reported


async def test_reporting_results_needs_jobs_write_and_a_finished_project_job(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    from app.models import ApiKey
    from app.services.api_key_service import generate_key, hash_key
    pid, item, job_id, a, _ = await _setup(client, upload_3mf, create_printer, tmp_path)
    assert (await _clear(client, tmp_path, a, job_id, {item: 1})).status_code == 422          # still queued: not on this printer
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.assigned_printer_id, job.status = a, "printing"
        await s.commit()
    assert (await _clear(client, tmp_path, a, job_id, {item: 1})).status_code == 409          # not finished yet
    await _printed(session_factory, job_id, a)
    raw, prefix = generate_key()
    async with session_factory() as s:
        s.add(ApiKey(name="ctl", key_prefix=prefix, key_hash=hash_key(raw), scopes=["printers:control"], enabled=True,
                     created_at="2026-01-01T00:00:00"))
        await s.commit()
    body = {"job_id": job_id, "results": [{"project_item_id": item, "quantity_failed": 1}]}
    r = await client.post(f"/api/v1/printers/{a}/plate-cleared", json=body, headers={"X-Api-Key": raw})
    assert r.status_code == 403
    assert (await client.post(f"/api/v1/printers/{a}/plate-cleared", headers={"X-Api-Key": raw})).status_code == 200   # plain clear


async def test_results_are_reported_once(client, upload_3mf, create_printer, session_factory, tmp_path):
    pid, item, job_id, a, _ = await _setup(client, upload_3mf, create_printer, tmp_path)
    await _printed(session_factory, job_id, a)
    assert (await _clear(client, tmp_path, a, job_id, {item: 1})).status_code == 200
    async with session_factory() as s:
        (await s.get(Printer, a)).awaiting_plate_clear = True
        await s.commit()
    assert (await _clear(client, tmp_path, a, job_id, {item: 1})).status_code == 409
    assert (await client.put(f"/api/v1/jobs/{job_id}/outcome", json={"failures": []})).status_code == 409   # no re-review
    async with session_factory() as s:
        assert (await s.get(ProjectItem, item)).quantity_failed == 1
    assert (await _clear(client, tmp_path, a, job_id, {9999: 1})).status_code in (409, 422)


async def test_a_requeue_that_cannot_be_packed_holds_the_parts_and_still_clears_the_plate(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    from app.services.providers.contract import ErrorCode, ProviderError
    pid, item, job_id, a, _ = await _setup(client, upload_3mf, create_printer, tmp_path)
    await _printed(session_factory, job_id, a)
    broken = FakeProjectConstructor(outputs=[ProviderError(ErrorCode.FAILED, "builder down")])
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=broken):
        r = await client.post(f"/api/v1/printers/{a}/plate-cleared", json={
            "job_id": job_id, "results": [{"project_item_id": item, "quantity_failed": 2}]})
    assert r.status_code == 200, r.text
    assert r.json()["requeued_job_ids"] == [] and r.json()["held"] == [{"project_item_id": item, "quantity": 2}]
    assert r.json()["requeue_errors"]
    async with session_factory() as s:
        pi = await s.get(ProjectItem, item)
        assert (pi.quantity_completed, pi.quantity_failed, pi.quantity_held) == (1, 2, 2)
        assert (await s.get(Printer, a)).awaiting_plate_clear is False
    assert [e["resolution"] for e in (await client.get(f"/api/v1/projects/{pid}/failures")).json()] == ["held"]


async def test_after_a_reprint_failures_count_afresh(client, upload_3mf, create_printer, session_factory, tmp_path):
    pid, item, job_id, a, b = await _setup(client, upload_3mf, create_printer, tmp_path, qty=1)
    await _printed(session_factory, job_id, a)
    retry = (await _clear(client, tmp_path, a, job_id, {item: 1})).json()["requeued_job_ids"][0]
    await _printed(session_factory, retry, b)
    assert (await _clear(client, tmp_path, b, retry, {item: 1})).json()["held"]        # A + B: held
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        reprint = (await client.post(f"/api/v1/projects/{pid}/items/{item}/held", json={"action": "reprint"})).json()["requeued_job_ids"][0]
    await _printed(session_factory, reprint, a)
    r = await _clear(client, tmp_path, a, reprint, {item: 1})                            # first failure after the reprint
    assert r.json()["held"] == [] and len(r.json()["requeued_job_ids"]) == 1
    async with session_factory() as s:
        assert (await s.get(ProjectItem, item)).quantity_failed == 3                      # failed attempts, not outstanding


# --- GH #227: an interruption between the committed results and the repack -------------------------------------------

class _ProcessDied(BaseException):
    """Process loss: not an application error, so nothing in the request turns it into a hold."""


class _Interrupted(RuntimeError):
    """The request is cut off as the repack starts (a BaseException would not survive the ASGI test transport)."""


async def _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path, failed=2):
    """Clear with `failed` parts failed, the process dying as the repack starts (the results and the clear are committed)."""
    from app.services import print_results
    pid, item, job_id, a, _ = await _setup(client, upload_3mf, create_printer, tmp_path)
    await _printed(session_factory, job_id, a)
    real = print_results.requeue_parts
    with patch.object(print_results, "requeue_reported", side_effect=_Interrupted):
        with pytest.raises(_Interrupted):
            await _clear(client, tmp_path, a, job_id, {item: failed})
    assert len(await _project_jobs(session_factory, pid)) == 1                 # the gap: no replacement yet
    return pid, item, job_id, a, real


async def _pending(session_factory, job_id) -> int:
    from app.models import JobItemFailure
    async with session_factory() as s:
        return sum((await s.execute(select(JobItemFailure.requeue_pending).where(JobItemFailure.job_id == job_id))).scalars())


async def test_retrying_an_interrupted_clear_requeues_the_failed_parts_exactly_once(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    pid, item, job_id, a, _ = await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)
    assert await _pending(session_factory, job_id) == 2

    retry = await _clear(client, tmp_path, a, job_id, {item: 2})
    assert retry.status_code == 200, retry.text
    jobs = await _project_jobs(session_factory, pid)
    assert len(jobs) == 2 and retry.json()["requeued_job_ids"] == [jobs[1].id]
    assert json.loads(jobs[1].project_item_quantities) == {str(item): 2}
    assert await _pending(session_factory, job_id) == 0
    async with session_factory() as s:                                         # recorded once: nothing counted twice
        pi = await s.get(ProjectItem, item)
        assert (pi.quantity_completed, pi.quantity_failed, pi.quantity_held) == (1, 2, 0)

    again = await _clear(client, tmp_path, a, job_id, {item: 2})
    assert again.status_code == 409 and len(await _project_jobs(session_factory, pid)) == 2


async def test_startup_resumes_an_interrupted_requeue_once(client, upload_3mf, create_printer, session_factory, tmp_path):
    from app.services import print_results
    pid, item, job_id, _, _ = await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        assert await print_results.resume_pending(session_factory) == [job_id]
        assert await print_results.resume_pending(session_factory) == []       # nothing left: a second boot repacks nothing
    jobs = await _project_jobs(session_factory, pid)
    assert len(jobs) == 2 and json.loads(jobs[1].project_item_quantities) == {str(item): 2}


async def test_the_history_report_also_resumes_an_interrupted_requeue(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    pid, item, job_id, _, _ = await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        r = await client.post(f"/api/v1/jobs/{job_id}/results", json={"results": [{"project_item_id": item, "quantity_failed": 2}]})
    assert r.status_code == 200, r.text
    assert len(await _project_jobs(session_factory, pid)) == 2


async def test_a_pack_and_the_decrement_of_its_parts_commit_together(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    """Dying right after the replacement pack is queued (before anything else) must not repack it again on recovery."""
    from app.services import print_results
    pid, item, job_id, a, real = await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)

    async def pack_then_die(*args, **kwargs):
        await real(*args, **kwargs)
        raise _ProcessDied

    with patch.object(print_results, "requeue_parts", side_effect=pack_then_die), \
         patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        with pytest.raises(_ProcessDied):
            await print_results.resume_pending(session_factory)
    assert len(await _project_jobs(session_factory, pid)) == 2 and await _pending(session_factory, job_id) == 0
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        assert await print_results.resume_pending(session_factory) == []
    assert len(await _project_jobs(session_factory, pid)) == 2


async def test_a_resumed_requeue_that_cannot_be_packed_holds_the_parts(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    from app.services import print_results
    from app.services.providers.contract import ErrorCode, ProviderError
    pid, item, job_id, _, _ = await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)
    broken = FakeProjectConstructor(outputs=[ProviderError(ErrorCode.FAILED, "builder down")])
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=broken):
        await print_results.resume_pending(session_factory)
    async with session_factory() as s:
        assert (await s.get(ProjectItem, item)).quantity_held == 2
    assert [e["resolution"] for e in (await client.get(f"/api/v1/projects/{pid}/failures")).json()] == ["held"]
    assert await _pending(session_factory, job_id) == 0 and len(await _project_jobs(session_factory, pid)) == 1


async def test_concurrent_resumes_queue_the_failed_parts_once(client, upload_3mf, create_printer, session_factory, tmp_path):
    """A retry while the startup sweep (or another retry) is still packing must not queue the same parts twice."""
    import asyncio
    from app.services import print_results
    pid, item, job_id, _, _ = await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        await asyncio.gather(print_results.resume_pending(session_factory), print_results.resume_pending(session_factory))
    jobs = await _project_jobs(session_factory, pid)
    assert len(jobs) == 2 and json.loads(jobs[1].project_item_quantities) == {str(item): 2}
    assert await _pending(session_factory, job_id) == 0


async def test_a_concurrent_repack_that_lost_the_claim_queues_nothing(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    """The claim itself, not only the in-process lock: a repack whose parts another one committed first rolls its pack back."""
    from app.models import JobItemFailure
    from app.services import print_results
    pid, item, job_id, _, real = await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)

    async def other_repack_won(session, source, parts, **kwargs):
        async with session_factory() as other:                 # another process committed the repack meanwhile
            row = (await other.execute(select(JobItemFailure).where(JobItemFailure.job_id == job_id))).scalar_one()
            row.requeue_pending = 0
            await other.commit()
        return await real(session, source, parts, **kwargs)

    with patch.object(print_results, "requeue_parts", side_effect=other_repack_won), \
         patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        out = await print_results.resume_pending(session_factory)
    assert out == [job_id] and len(await _project_jobs(session_factory, pid)) == 1
    async with session_factory() as s:
        assert (await s.get(ProjectItem, item)).quantity_held == 0       # the winner owns the parts: nothing held twice


async def test_a_pack_whose_configs_cannot_be_written_holds_the_parts_and_leaves_no_undispatchable_job(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    from app.services import model_targets, print_results
    pid, item, job_id, _, _ = await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)
    with patch.object(model_targets, "materialize_job", side_effect=RuntimeError("disk I/O error")), \
         patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        await print_results.resume_pending(session_factory)
    assert len(await _project_jobs(session_factory, pid)) == 1                  # the half-built pack's jobs rolled back
    async with session_factory() as s:
        assert (await s.get(ProjectItem, item)).quantity_held == 2
    assert await _pending(session_factory, job_id) == 0


async def test_the_startup_sweep_leaves_parts_pending_while_the_builder_is_not_ready(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    from fastapi import HTTPException
    from app.api.routes import project_generate
    from app.services import print_results
    pid, item, job_id, _, _ = await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)
    with patch.object(project_generate, "_require_ready", side_effect=HTTPException(503, "builder starting")), \
         patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        assert await print_results.resume_pending(session_factory) == [job_id]
    assert await _pending(session_factory, job_id) == 2
    async with session_factory() as s:
        assert (await s.get(ProjectItem, item)).quantity_held == 0
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        assert await print_results.resume_pending(session_factory) == [job_id]  # builder up: the next try repacks
    assert len(await _project_jobs(session_factory, pid)) == 2 and await _pending(session_factory, job_id) == 0


# --- GH #249: recovery gaps ---------------------------------------------------------------------------------------------

async def test_a_plain_clear_finishes_an_earlier_interrupted_requeue_of_that_printer(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    pid, item, job_id, a, _ = await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        r = await client.post(f"/api/v1/printers/{a}/plate-cleared")            # the QR / home-automation clear, no body
    assert r.status_code == 200, r.text
    jobs = await _project_jobs(session_factory, pid)
    assert len(jobs) == 2 and r.json()["requeued_job_ids"] == [jobs[1].id]
    assert await _pending(session_factory, job_id) == 0


async def test_the_per_job_repack_locks_do_not_accumulate(client, upload_3mf, create_printer, session_factory, tmp_path):
    from app.services import print_results
    await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        await print_results.resume_pending(session_factory)
    assert print_results._requeue_locks == {}


async def test_the_startup_sweep_keeps_retrying_after_a_failed_pass(session_factory, monkeypatch):
    from app.services import print_results
    passes = []

    async def flaky(_factory):
        passes.append(1)
        if len(passes) == 1:
            raise RuntimeError("database is locked")
        return [7] if len(passes) == 2 else []

    monkeypatch.setattr(print_results, "resume_pending", flaky)
    woke = []
    await print_results.resume_pending_with_retries(session_factory, attempts=5, delay_s=0, on_resumed=lambda: woke.append(1))
    assert len(passes) == 3 and woke == [1]                                   # failed, resumed, then nothing left: stop


async def test_a_failure_after_the_parts_were_queued_is_not_reported_as_a_requeue_failure(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    from app.api.routes import projects as project_routes
    from app.services import print_results
    pid, item, job_id, _, _ = await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)
    store = project_routes.get_library_store

    def store_whose_move_fails(*args, **kwargs):
        real = store(*args, **kwargs)
        if kwargs.get("prune_empty"):
            def broken_move(*_a, **_k):
                raise OSError("disk full")
            real.move = broken_move
        return real

    with patch.object(project_routes, "get_library_store", side_effect=store_whose_move_fails), \
         patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        async with session_factory() as s:
            out = await print_results.requeue_reported(s, await s.get(Job, job_id))
    assert "requeue_errors" not in out and out["held"] == []
    assert len(await _project_jobs(session_factory, pid)) == 2 and await _pending(session_factory, job_id) == 0
    async with session_factory() as s:
        assert (await s.get(ProjectItem, item)).quantity_held == 0



async def _second_pending_plate(session_factory, client, tmp_path, pid, item, printer_id) -> int:
    """Another finished plate of the same project on the same printer whose failed part was recorded but never repacked."""
    from app.models import JobItemFailure
    jobs = (await _generate(client, pid, tmp_path, eligible_printer_ids=[printer_id], process_preset="0.20mm")).json()["jobs"]
    job_id = jobs[0]["id"]
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.status, job.assigned_printer_id = "complete", printer_id
        s.add(JobItemFailure(job_id=job_id, project_item_id=item, quantity_failed=1, quantity_on_plate=1, printer_id=printer_id,
                             created_at="t", resolution="requeued", requeue_pending=1))
        await s.commit()
    return job_id


async def test_retrying_a_report_also_finishes_the_other_interrupted_plates_of_that_printer(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    pid, item, job_id, a, _ = await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)
    other = await _second_pending_plate(session_factory, client, tmp_path, pid, item, a)
    before = len(await _project_jobs(session_factory, pid))
    retry = await _clear(client, tmp_path, a, job_id, {item: 2})
    assert retry.status_code == 200, retry.text
    assert await _pending(session_factory, job_id) == 0 and await _pending(session_factory, other) == 0
    assert len(await _project_jobs(session_factory, pid)) == before + 2 and len(retry.json()["requeued_job_ids"]) == 2


async def test_a_plain_clear_leaves_parts_pending_while_the_builder_is_still_starting(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    from fastapi import HTTPException
    from app.api.routes import project_generate
    pid, item, job_id, a, _ = await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)
    with patch.object(project_generate, "_require_ready", side_effect=HTTPException(503, "builder starting")), \
         patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        r = await client.post(f"/api/v1/printers/{a}/plate-cleared")
    assert r.status_code == 200, r.text
    assert await _pending(session_factory, job_id) == 2
    async with session_factory() as s:
        assert (await s.get(ProjectItem, item)).quantity_held == 0
