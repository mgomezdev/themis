"""BIZ-365: a printer that fails the same part `waive_after` times is waived for that part in every project; waived printers
skip jobs carrying it; per-printer flag / stop thresholds; a test print jumps the queue and its results can clear the waiver."""
import json
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from app.models import Job, JobPrinterConfig, PartWaiver, Printer
from app.services.queue_engine import QueueEngine
from tests.fake_providers import FakeProjectConstructor
from tests.services.test_queue_engine import _install_fake_put, _make_mock_printer_manager

STL = b"solid part\nendsolid\n"


def _patches(tmp_path):
    return (patch("app.config.get_library_dir", return_value=tmp_path / "library"),
            patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()))


async def _project_job(client, upload_3mf, printers, tmp_path, name="P", qty=1):
    pid = (await client.post("/api/v1/projects", json={"name": name})).json()["id"]
    fid = await upload_3mf(filename="part.stl", data=STL)                # same bytes in every project: the same part
    item = (await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "quantity": qty})).json()["id"]
    a, b = _patches(tmp_path)
    with a, b:
        r = await client.post(f"/api/v1/projects/{pid}/generate", json={"eligible_printer_ids": printers, "process_preset": "0.2"})
    assert r.status_code == 200, r.text
    return pid, item, r.json()["jobs"][0]["id"]


async def _fail_on(client, session_factory, tmp_path, job_id, printer_id, item):
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.status, job.assigned_printer_id = "complete", printer_id
        (await s.get(Printer, printer_id)).awaiting_plate_clear = True
        await s.commit()
    a, b = _patches(tmp_path)
    with a, b:
        r = await client.post(f"/api/v1/printers/{printer_id}/plate-cleared", json={
            "job_id": job_id, "results": [{"project_item_id": item, "quantity_failed": 1}]})
    assert r.status_code == 200, r.text
    return r.json()


async def _waive(client, upload_3mf, create_printer, session_factory, tmp_path):
    """Printer A fails the part in two different projects → waived. Returns (a, b, waiver_id)."""
    await client.put("/api/v1/settings/queue", json={"part_retry_limit": 5})          # keep it requeueing, never held
    a = await create_printer(name="A")
    b = await create_printer(name="B")
    for n in ("P1", "P2"):
        _, item, job = await _project_job(client, upload_3mf, [a, b], tmp_path, name=n)
        await _fail_on(client, session_factory, tmp_path, job, a, item)
    (w,) = (await client.get(f"/api/v1/printers/{a}/waivers")).json()
    return a, b, w["id"]


async def test_two_failures_of_a_part_on_one_printer_waive_it_in_every_project(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    a, b, wid = await _waive(client, upload_3mf, create_printer, session_factory, tmp_path)
    (w,) = (await client.get(f"/api/v1/printers/{a}/waivers")).json()
    from tests.api.test_response_contracts import assert_carries
    assert_carries("part_waiver", w)
    assert (w["file_name"], w["failures"], sorted(w["projects"])) == ("part.stl", 2, ["P1", "P2"])
    assert w["last_failure_at"]
    assert (await client.get(f"/api/v1/printers/{b}/waivers")).json() == []


async def test_one_failure_does_not_waive(client, upload_3mf, create_printer, session_factory, tmp_path):
    a = await create_printer(name="A")
    _, item, job = await _project_job(client, upload_3mf, [a], tmp_path)
    await _fail_on(client, session_factory, tmp_path, job, a, item)
    assert (await client.get(f"/api/v1/printers/{a}/waivers")).json() == []


async def _claim(session_factory, printer_id):
    from tests.fake_providers import FakeSlicingProvider
    from tests.slicing_env import slicing_provider
    qe = QueueEngine(session_factory, _make_mock_printer_manager([printer_id]), MagicMock())
    _install_fake_put(qe)
    with slicing_provider(FakeSlicingProvider()):
        async with session_factory() as s:
            await qe._try_claim_for_printer(s, printer_id, slice_only=True)


async def test_a_waived_printer_skips_jobs_with_the_part_and_takes_other_work(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    a, b, _ = await _waive(client, upload_3mf, create_printer, session_factory, tmp_path)
    pid, item, job = await _project_job(client, upload_3mf, [a], tmp_path, name="P3")          # carries the waived part
    other_pid = (await client.post("/api/v1/projects", json={"name": "Other"})).json()["id"]
    other_file = await upload_3mf(filename="other.stl", data=b"solid other\nendsolid\n")
    await client.post(f"/api/v1/projects/{other_pid}/items", json={"file_id": other_file, "quantity": 1})
    x, y = _patches(tmp_path)
    with x, y:
        other_job = (await client.post(f"/api/v1/projects/{other_pid}/generate",
                                       json={"eligible_printer_ids": [a], "process_preset": "0.2"})).json()["jobs"][0]["id"]
    async with session_factory() as s:          # only these two are claimable for A
        for j in (await s.execute(select(Job).where(Job.id.not_in([job, other_job])))).scalars():
            j.status = "complete"
        await s.commit()
    await _claim(session_factory, a)
    async with session_factory() as s:
        assert (await s.get(Job, job)).assigned_printer_id is None
        assert (await s.get(Job, other_job)).assigned_printer_id == a


async def test_flag_and_stop_thresholds(client, upload_3mf, create_printer, session_factory, tmp_path):
    a, b, _ = await _waive(client, upload_3mf, create_printer, session_factory, tmp_path)
    fleet = {p["id"]: p for p in (await client.get("/api/v1/fleet")).json()}
    assert (fleet[a]["waived_parts"], fleet[a]["waiver_flag"], fleet[a]["waiver_stopped"]) == (1, False, False)
    await client.patch(f"/api/v1/printers/{a}", json={"waiver_flag_at": 1, "waiver_stop_at": 1})
    fleet = {p["id"]: p for p in (await client.get("/api/v1/fleet")).json()}
    assert (fleet[a]["waiver_flag"], fleet[a]["waiver_stopped"]) == (True, True)

    other_pid = (await client.post("/api/v1/projects", json={"name": "Other"})).json()["id"]
    other_file = await upload_3mf(filename="other.stl", data=b"solid other\nendsolid\n")
    await client.post(f"/api/v1/projects/{other_pid}/items", json={"file_id": other_file, "quantity": 1})
    x, y = _patches(tmp_path)
    with x, y:
        other_job = (await client.post(f"/api/v1/projects/{other_pid}/generate",
                                       json={"eligible_printer_ids": [a], "process_preset": "0.2"})).json()["jobs"][0]["id"]
    await _claim(session_factory, a)
    async with session_factory() as s:
        assert (await s.get(Job, other_job)).assigned_printer_id is None          # stopped: nothing is claimed


async def test_a_test_print_jumps_the_queue_and_its_results_can_clear_the_waiver(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    a, b, wid = await _waive(client, upload_3mf, create_printer, session_factory, tmp_path)
    await client.patch(f"/api/v1/printers/{a}", json={"waiver_stop_at": 1, "default_process_profile": None})
    x, y = _patches(tmp_path)
    with x, y:
        r = await client.post(f"/api/v1/printers/{a}/waivers/{wid}/test-print")
    assert r.status_code == 200, r.text
    test_job = r.json()["job_id"]
    async with session_factory() as s:
        cfgs = (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == test_job))).scalars().all()
        assert [(c.printer_id, c.print_profile) for c in cfgs] == [(a, "0.2")]   # this printer; no default: the profile it used
        assert (await s.get(Printer, a)).test_print_job_id == test_job
    await _claim(session_factory, a)                                                  # stopped, but the test print runs
    async with session_factory() as s:
        assert (await s.get(Job, test_job)).assigned_printer_id == a

    async with session_factory() as s:
        job = await s.get(Job, test_job)
        job.status = "complete"
        (await s.get(Printer, a)).awaiting_plate_clear = True
        await s.commit()
    pending = (await client.get(f"/api/v1/printers/{a}/plate-results")).json()
    assert pending["job_id"] == test_job and pending["test_print_waiver_id"] == wid
    x, y = _patches(tmp_path)
    with x, y:
        r = await client.post(f"/api/v1/printers/{a}/plate-cleared", json={"job_id": test_job, "results": [], "clear_waiver": True})
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/v1/printers/{a}/waivers")).json() == []
    async with session_factory() as s:
        assert (await s.get(Printer, a)).test_print_job_id is None


async def test_clear_and_clear_all(client, upload_3mf, create_printer, session_factory, tmp_path):
    a, b, wid = await _waive(client, upload_3mf, create_printer, session_factory, tmp_path)
    assert (await client.delete(f"/api/v1/printers/{a}/waivers/{wid}")).status_code == 204
    assert (await client.get(f"/api/v1/printers/{a}/waivers")).json() == []
    async with session_factory() as s:
        s.add(PartWaiver(printer_id=a, part_key="x:stl:", file_id=None, failures=2, last_failure_at="t", created_at="t"))
        await s.commit()
    assert (await client.delete(f"/api/v1/printers/{a}/waivers")).status_code == 204
    assert (await client.get(f"/api/v1/printers/{a}/waivers")).json() == []
    assert (await client.delete(f"/api/v1/printers/{b}/waivers/{wid}")).status_code == 404


async def test_waive_after_is_a_setting(client):
    assert (await client.get("/api/v1/settings/queue")).json()["waive_after"] == 2
    await client.put("/api/v1/settings/queue", json={"waive_after": 3})
    assert (await client.get("/api/v1/settings/queue")).json()["waive_after"] == 3


async def test_after_a_clear_failures_count_afresh(client, upload_3mf, create_printer, session_factory, tmp_path):
    a, b, wid = await _waive(client, upload_3mf, create_printer, session_factory, tmp_path)
    assert (await client.delete(f"/api/v1/printers/{a}/waivers/{wid}")).status_code == 204
    _, item, job = await _project_job(client, upload_3mf, [a, b], tmp_path, name="P3")
    await _fail_on(client, session_factory, tmp_path, job, a, item)               # one failure since the clear
    assert (await client.get(f"/api/v1/printers/{a}/waivers")).json() == []
    _, item, job = await _project_job(client, upload_3mf, [a, b], tmp_path, name="P4")
    await _fail_on(client, session_factory, tmp_path, job, a, item)               # the second: waived again
    (w,) = (await client.get(f"/api/v1/printers/{a}/waivers")).json()
    assert w["failures"] == 2


async def test_waive_after_changes_the_threshold(client, upload_3mf, create_printer, session_factory, tmp_path):
    await client.put("/api/v1/settings/queue", json={"waive_after": 1, "part_retry_limit": 5})
    a = await create_printer(name="A")
    _, item, job = await _project_job(client, upload_3mf, [a], tmp_path)
    await _fail_on(client, session_factory, tmp_path, job, a, item)
    assert len((await client.get(f"/api/v1/printers/{a}/waivers")).json()) == 1


async def test_a_job_every_eligible_printer_is_waived_for_is_blocked_until_cleared(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    a, b, wid = await _waive(client, upload_3mf, create_printer, session_factory, tmp_path)
    _, _, job = await _project_job(client, upload_3mf, [a], tmp_path, name="Only A")
    mgr = _make_mock_printer_manager([a])
    mgr.liveness.return_value = "online"
    qe = QueueEngine(session_factory, mgr, MagicMock())
    await qe._sweep_unmatched_jobs()
    async with session_factory() as s:
        j = await s.get(Job, job)
        assert j.status == "blocked" and "waived" in j.block_reason
    await client.delete(f"/api/v1/printers/{a}/waivers/{wid}")
    await qe._sweep_unmatched_jobs()
    async with session_factory() as s:
        assert (await s.get(Job, job)).status == "queued"


async def test_a_pending_test_print_is_all_the_printer_claims_until_it_is_cancelled(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    a, b, wid = await _waive(client, upload_3mf, create_printer, session_factory, tmp_path)
    other_pid = (await client.post("/api/v1/projects", json={"name": "Other"})).json()["id"]
    other_file = await upload_3mf(filename="other.stl", data=b"solid other\nendsolid\n")
    await client.post(f"/api/v1/projects/{other_pid}/items", json={"file_id": other_file, "quantity": 1})
    x, y = _patches(tmp_path)
    with x, y:
        other_job = (await client.post(f"/api/v1/projects/{other_pid}/generate",
                                       json={"eligible_printer_ids": [a], "process_preset": "0.2"})).json()["jobs"][0]["id"]
        test_job = (await client.post(f"/api/v1/printers/{a}/waivers/{wid}/test-print")).json()["job_id"]
        assert (await client.post(f"/api/v1/printers/{a}/waivers/{wid}/test-print")).status_code == 409   # one at a time
    async with session_factory() as s:          # the test job can't run right now: the printer must still not take other work
        (await s.get(Job, test_job)).not_before = "2999-01-01T00:00:00+00:00"
        await s.commit()
    await _claim(session_factory, a)
    async with session_factory() as s:
        assert (await s.get(Job, other_job)).assigned_printer_id is None
    assert (await client.delete(f"/api/v1/printers/{a}/test-print")).status_code == 204
    async with session_factory() as s:
        assert ((await s.get(Job, test_job)).status, (await s.get(Printer, a)).test_print_job_id) == ("cancelled", None)


async def test_a_cancelled_test_print_does_not_strand_the_printer(client, upload_3mf, create_printer, session_factory, tmp_path):
    a, b, wid = await _waive(client, upload_3mf, create_printer, session_factory, tmp_path)
    x, y = _patches(tmp_path)
    with x, y:
        test_job = (await client.post(f"/api/v1/printers/{a}/waivers/{wid}/test-print")).json()["job_id"]
    async with session_factory() as s:
        (await s.get(Job, test_job)).status = "cancelled"
        await s.commit()
    await _claim(session_factory, a)
    async with session_factory() as s:
        assert (await s.get(Printer, a)).test_print_job_id is None
