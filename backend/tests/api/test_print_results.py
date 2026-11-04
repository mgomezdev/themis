"""BIZ-364: print results on plate clear. Clearing a plate records what printed per part; failed parts are requeued as a new pack
in the same print group (same printers and profiles); a part that has failed on `part_retry_limit` different printers is held for
a person (Reprint / Drop); every failure is in the project's failure log."""
import json
from unittest.mock import patch

from sqlalchemy import select

from app.models import Job, JobPrinterConfig, Printer, ProjectItem
from tests.fake_providers import FakeProjectConstructor


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
                 "test_print_waiver_id": None}
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
