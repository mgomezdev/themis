"""Plate-clear defaults: a plate cleared WITHOUT results gets the Settings default for how its print ended (completed -> assume
good, failed -> assume failed, or "ask" to leave it awaiting results); a person's report always wins and can still correct an
assumed-good plate from History; assumed failures are weak evidence (no waivers, no holds); a printer whose prints keep failing
is paused. Each rule is checked for a fixed-group plate and a pool plate where both apply."""
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models import Job, JobItemFailure, JobPrinterConfig, Printer, ProjectItem
from app.services.queue_engine import QueueEngine
from tests.api import test_part_waivers as waivers
from tests.api import test_pools as pools
from tests.api import test_print_results as results
from tests.fake_providers import FakeProjectConstructor
from tests.services.test_queue_engine import _install_fake_put, _make_mock_printer_manager

# New groups pack at generate ("fixed"); the pool plates below switch their group to "pool" explicitly.
pytestmark = pytest.mark.usefixtures("fixed_groups")

KINDS = ["fixed", "pool"]
QUEUE = "/api/v1/settings/queue"


# ---- scenario helpers ------------------------------------------------------------------------------------------------

@contextmanager
def _builder(tmp_path, packer=None):
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=packer or FakeProjectConstructor()):
        yield


async def _plate(kind, client, upload_3mf, create_printer, session_factory, tmp_path, qty=3):
    """One plate of `qty` parts claimed for printer A (B is also eligible). Fixed: a generated job; pool: a plate packed at claim."""
    if kind == "fixed":
        pid, item, job_id, a, b = await results._setup(client, upload_3mf, create_printer, tmp_path, qty=qty)
        return SimpleNamespace(kind=kind, pid=pid, item=item, job=job_id, a=a, b=b, gid=None, packer=None, qe=None,
                               tmp_path=tmp_path, sf=session_factory, client=client)
    a = await create_printer(name="A", bed_x_mm=350.0, bed_y_mm=350.0)         # PoolPacker fits bed_x // 100 = 3 parts
    b = await create_printer(name="B", bed_x_mm=350.0, bed_y_mm=350.0)
    packer = pools.PoolPacker()
    pid, gid, item = await pools._pool_project(client, upload_3mf, [a, b], tmp_path, packer, qty=qty)
    qe = pools._engine(session_factory, [a, b])
    await pools._claim(session_factory, qe, a, tmp_path, packer)
    (job,) = await pools._pool_jobs(session_factory, gid)
    assert json.loads(job.project_item_quantities) == {str(item): qty}
    return SimpleNamespace(kind=kind, pid=pid, item=item, job=job.id, a=a, b=b, gid=gid, packer=packer, qe=qe,
                           tmp_path=tmp_path, sf=session_factory, client=client)


async def _finish(session_factory, job_id, printer_id, status="complete"):
    """The print ended (`status`) on `printer_id`, which now waits for its plate to be cleared."""
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.status, job.assigned_printer_id, job.printed_on_printer_id = status, printer_id, printer_id
        job.completed_at = datetime.now(timezone.utc).isoformat()
        (await s.get(Printer, printer_id)).awaiting_plate_clear = True
        await s.commit()


async def _plain_clear(p, printer_id=None):
    with _builder(p.tmp_path, p.packer):
        r = await p.client.post(f"/api/v1/printers/{printer_id or p.a}/plate-cleared")
    assert r.status_code == 200, r.text
    return r


async def _outcome(session_factory, job_id):
    async with session_factory() as s:
        return (await s.get(Job, job_id)).outcome


async def _counts(session_factory, item):
    async with session_factory() as s:
        pi = await s.get(ProjectItem, item)
        return pi.quantity_completed, pi.quantity_failed, pi.quantity_held


async def _project_jobs(session_factory, pid):
    return await results._project_jobs(session_factory, pid)


async def _pool_stats(p):
    return (await p.client.get(f"/api/v1/projects/{p.pid}/groups")).json()[0]["pool"]


async def _set_queue(client, **body):
    r = await client.put(QUEUE, json=body)
    assert r.status_code == 200, r.text


async def _pending(client, printer_id):
    return (await client.get(f"/api/v1/printers/{printer_id}/plate-results")).json()


# ---- 1. settings -----------------------------------------------------------------------------------------------------

async def test_the_plate_clear_settings_default_and_each_changes_alone(client):
    cfg = (await client.get(QUEUE)).json()
    assert (cfg["plate_clear_complete"], cfg["plate_clear_failed"], cfg["pause_after_failed_prints"]) == ("assume_good", "assume_failed", 3)

    for body in ({"plate_clear_complete": "ask"}, {"plate_clear_failed": "ask"}, {"pause_after_failed_prints": 7}):
        before = (await client.get(QUEUE)).json()
        await _set_queue(client, **body)
        after = (await client.get(QUEUE)).json()
        (key,) = body
        assert after[key] == body[key]
        assert {k: v for k, v in after.items() if k != key} == {k: v for k, v in before.items() if k != key}    # nothing else moved

    await _set_queue(client, plate_clear_complete="assume_good", plate_clear_failed="assume_failed", pause_after_failed_prints=0)
    cfg = (await client.get(QUEUE)).json()
    assert (cfg["plate_clear_complete"], cfg["plate_clear_failed"], cfg["pause_after_failed_prints"]) == ("assume_good", "assume_failed", 0)


@pytest.mark.parametrize("body", [
    {"plate_clear_complete": "assume_failed"}, {"plate_clear_complete": "nope"},
    {"plate_clear_failed": "assume_good"}, {"plate_clear_failed": ""},
    {"pause_after_failed_prints": -1}, {"pause_after_failed_prints": 21}, {"pause_after_failed_prints": "many"},
])
async def test_invalid_plate_clear_settings_are_refused_and_change_nothing(client, body):
    await _set_queue(client, plate_clear_complete="ask", plate_clear_failed="ask", pause_after_failed_prints=5)
    before = (await client.get(QUEUE)).json()
    assert (await client.put(QUEUE, json=body)).status_code == 422
    assert (await client.get(QUEUE)).json() == before


async def test_the_pause_setting_accepts_its_bounds(client):
    for n in (0, 20):
        await _set_queue(client, pause_after_failed_prints=n)
        assert (await client.get(QUEUE)).json()["pause_after_failed_prints"] == n


# ---- 2. completed print + plain clear --------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", KINDS)
async def test_a_completed_plate_cleared_without_results_is_assumed_good_and_credited(
        kind, client, upload_3mf, create_printer, session_factory, tmp_path):
    p = await _plate(kind, client, upload_3mf, create_printer, session_factory, tmp_path)
    await _finish(session_factory, p.job, p.a)
    assert (await _pending(client, p.a))["job_id"] == p.job and (await _pending(client, p.a))["print_failed"] is False

    r = await _plain_clear(p)

    assert r.json()["assumed"] == [{"job_id": p.job, "result": "assumed_good"}]
    assert await _outcome(session_factory, p.job) == "assumed_good"
    assert await _counts(session_factory, p.item) == (3, 0, 0)
    assert (await _pending(client, p.a))["job_id"] is None                           # nothing left to report
    async with session_factory() as s:
        assert (await s.get(Printer, p.a)).awaiting_plate_clear is False
        rows = (await s.execute(select(JobItemFailure).where(JobItemFailure.job_id == p.job))).scalars().all()
    assert [(x.quantity_failed, x.quantity_on_plate) for x in rows] == [(0, 3)]
    if kind == "pool":
        assert await _pool_stats(p) == {"done": 3, "printing": 0, "left": 0}
    else:
        assert len(await _project_jobs(session_factory, p.pid)) == 1                 # nothing requeued


# ---- 3. failed print + plain clear -----------------------------------------------------------------------------------

async def test_a_failed_fixed_plate_cleared_without_results_is_assumed_failed_and_printed_again(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    p = await _plate("fixed", client, upload_3mf, create_printer, session_factory, tmp_path)
    await _finish(session_factory, p.job, p.a, "failed")
    pending = await _pending(client, p.a)
    assert pending["job_id"] == p.job and pending["print_failed"] is True

    r = await _plain_clear(p)

    (entry,) = r.json()["assumed"]
    assert (entry["job_id"], entry["result"]) == (p.job, "assumed_failed") and entry["requeued_job_ids"]
    assert await _outcome(session_factory, p.job) == "assumed_failed"
    assert await _counts(session_factory, p.item) == (0, 3, 0)
    jobs = await _project_jobs(session_factory, p.pid)
    new = [j for j in jobs if j.id != p.job]
    assert sorted(j.id for j in new) == sorted(entry["requeued_job_ids"])
    assert [(j.status, json.loads(j.project_item_quantities)) for j in new] == [("queued", {str(p.item): 3})]
    async with session_factory() as s:                                               # same eligibility as the failed plate
        cfgs = (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == new[0].id))).scalars().all()
    assert sorted((c.printer_id, c.print_profile) for c in cfgs) == [(p.a, "0.20mm"), (p.b, "0.20mm")]
    assert (await _pending(client, p.a))["job_id"] is None


async def test_a_failed_pool_plate_cleared_without_results_puts_its_parts_back_in_the_pool(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    p = await _plate("pool", client, upload_3mf, create_printer, session_factory, tmp_path)
    assert await _pool_stats(p) == {"done": 0, "printing": 3, "left": 0}
    await _finish(session_factory, p.job, p.a, "failed")

    r = await _plain_clear(p)

    assert r.json()["assumed"] == [{"job_id": p.job, "result": "assumed_failed", "requeued_job_ids": []}]
    assert await _outcome(session_factory, p.job) == "assumed_failed"
    assert await _counts(session_factory, p.item) == (0, 3, 0)
    assert len(await pools._pool_jobs(session_factory, p.gid)) == 1                  # no new job until a printer claims
    assert await _pool_stats(p) == {"done": 0, "printing": 0, "left": 3}

    await pools._claim(session_factory, p.qe, p.b, p.tmp_path, p.packer)             # the next idle printer packs them again
    jobs = await pools._pool_jobs(session_factory, p.gid)
    assert len(jobs) == 2
    assert json.loads(jobs[1].project_item_quantities) == {str(p.item): 3}
    assert await _pool_stats(p) == {"done": 0, "printing": 3, "left": 0}


# ---- 4. "ask" leaves the plate awaiting results -----------------------------------------------------------------------

@pytest.mark.parametrize("kind", KINDS)
async def test_with_ask_a_completed_plate_cleared_without_results_stays_awaiting(
        kind, client, upload_3mf, create_printer, session_factory, tmp_path):
    await _set_queue(client, plate_clear_complete="ask")
    p = await _plate(kind, client, upload_3mf, create_printer, session_factory, tmp_path)
    await _finish(session_factory, p.job, p.a)

    r = await _plain_clear(p)

    assert not r.json().get("assumed")
    assert await _outcome(session_factory, p.job) is None
    assert await _counts(session_factory, p.item) == (0, 0, 0)
    assert (await _pending(client, p.a))["job_id"] == p.job                          # still to report
    async with session_factory() as s:
        assert (await s.get(Printer, p.a)).awaiting_plate_clear is False             # the plate itself is clear
    if kind == "pool":
        assert (await _pool_stats(p))["done"] == 0


@pytest.mark.parametrize("kind", KINDS)
async def test_with_ask_a_failed_plate_cleared_without_results_keeps_its_parts_reserved(
        kind, client, upload_3mf, create_printer, session_factory, tmp_path):
    await _set_queue(client, plate_clear_failed="ask")
    p = await _plate(kind, client, upload_3mf, create_printer, session_factory, tmp_path)
    await _finish(session_factory, p.job, p.a, "failed")
    stats_before = await _pool_stats(p) if kind == "pool" else None

    r = await _plain_clear(p)

    assert not r.json().get("assumed")
    assert await _outcome(session_factory, p.job) is None
    assert await _counts(session_factory, p.item) == (0, 0, 0)
    assert (await _pending(client, p.a))["job_id"] == p.job
    if kind == "fixed":
        assert [j.id for j in await _project_jobs(session_factory, p.pid)] == [p.job]          # nothing reprinted
    else:
        assert await _pool_stats(p) == stats_before                                  # left unchanged: the parts stay reserved
        await pools._claim(session_factory, p.qe, p.b, p.tmp_path, p.packer)
        assert len(await pools._pool_jobs(session_factory, p.gid)) == 1              # another printer packs nothing


@pytest.mark.parametrize("setting,status", [("plate_clear_complete", "failed"), ("plate_clear_failed", "complete")])
async def test_each_ask_setting_only_governs_its_own_kind_of_print(
        setting, status, client, upload_3mf, create_printer, session_factory, tmp_path):
    await _set_queue(client, **{setting: "ask"})
    p = await _plate("fixed", client, upload_3mf, create_printer, session_factory, tmp_path)
    await _finish(session_factory, p.job, p.a, status)
    await _plain_clear(p)
    assert await _outcome(session_factory, p.job) == ("assumed_failed" if status == "failed" else "assumed_good")


# ---- 5/6. several plates on one printer ------------------------------------------------------------------------------

async def test_one_plain_clear_settles_every_stacked_plate_by_its_own_default(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    a = await create_printer(name="A")
    b = await create_printer(name="B")
    pid1, item1, job1 = await waivers._project_job(client, upload_3mf, [a, b], tmp_path, name="One", qty=2)
    pid2, item2, job2 = await waivers._project_job(client, upload_3mf, [a, b], tmp_path, name="Two", qty=2)
    await _finish(session_factory, job1, a, "complete")
    await _finish(session_factory, job2, a, "failed")
    p = SimpleNamespace(client=client, tmp_path=tmp_path, packer=None, a=a)

    r = await _plain_clear(p)

    assumed = {x["job_id"]: x for x in r.json()["assumed"]}
    assert assumed[job1]["result"] == "assumed_good" and assumed[job2]["result"] == "assumed_failed"
    assert assumed[job2]["requeued_job_ids"] and "requeued_job_ids" not in assumed[job1]
    assert (await _outcome(session_factory, job1), await _outcome(session_factory, job2)) == ("assumed_good", "assumed_failed")
    assert await _counts(session_factory, item1) == (2, 0, 0)
    assert await _counts(session_factory, item2) == (0, 2, 0)
    assert len(await _project_jobs(session_factory, pid1)) == 1 and len(await _project_jobs(session_factory, pid2)) == 2
    assert (await _pending(client, a))["job_id"] is None


@pytest.mark.parametrize("older_status", ["complete", "failed"])
async def test_a_reported_clear_also_applies_the_default_to_an_older_unreported_plate(
        older_status, client, upload_3mf, create_printer, session_factory, tmp_path):
    a = await create_printer(name="A")
    b = await create_printer(name="B")
    pid1, item1, older = await waivers._project_job(client, upload_3mf, [a, b], tmp_path, name="Older", qty=2)
    pid2, item2, latest = await waivers._project_job(client, upload_3mf, [a, b], tmp_path, name="Latest", qty=2)
    await _finish(session_factory, older, a, older_status)
    await _finish(session_factory, latest, a, "complete")

    r = await results._clear(client, tmp_path, a, latest, {})                        # the dialog, for the latest plate
    assert r.status_code == 200, r.text

    assert await _outcome(session_factory, latest) == "reviewed"                     # a person's report is not overwritten
    assert await _counts(session_factory, item2) == (2, 0, 0)
    (entry,) = r.json()["assumed"]
    assert entry["job_id"] == older
    if older_status == "complete":
        assert (entry["result"], await _outcome(session_factory, older)) == ("assumed_good", "assumed_good")
        assert await _counts(session_factory, item1) == (2, 0, 0)
    else:
        assert (entry["result"], await _outcome(session_factory, older)) == ("assumed_failed", "assumed_failed")
        assert await _counts(session_factory, item1) == (0, 2, 0)
        assert entry["requeued_job_ids"] and len(await _project_jobs(session_factory, pid1)) == 2
    assert (await _pending(client, a))["job_id"] is None


# ---- 7. test prints --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["complete", "failed"])
async def test_a_test_print_is_never_given_an_assumed_result_by_a_plain_clear(
        status, client, upload_3mf, create_printer, session_factory, tmp_path):
    a, b, wid = await waivers._waive(client, upload_3mf, create_printer, session_factory, tmp_path)
    x, y = waivers._patches(tmp_path)
    with x, y:
        test_job = (await client.post(f"/api/v1/printers/{a}/waivers/{wid}/test-print")).json()["job_id"]
    async with session_factory() as s:
        job = await s.get(Job, test_job)
        item = next(int(k) for k in json.loads(job.project_item_quantities))
        pid = job.project_id
    before = await _counts(session_factory, item)
    jobs_before = len(await _project_jobs(session_factory, pid))
    await _finish(session_factory, test_job, a, status)

    p = SimpleNamespace(client=client, tmp_path=tmp_path, packer=None, a=a)
    r = await _plain_clear(p)

    assert not r.json().get("assumed")
    assert await _outcome(session_factory, test_job) not in ("assumed_good", "assumed_failed")
    assert await _counts(session_factory, item) == before                            # an extra copy credits and fails nothing
    assert len(await _project_jobs(session_factory, pid)) == jobs_before             # and nothing is reprinted for it
    async with session_factory() as s:
        assert (await s.execute(select(JobItemFailure).where(JobItemFailure.job_id == test_job))).first() is None


# ---- 8. assumed failures are weak evidence ---------------------------------------------------------------------------

async def _fail_assumed(client, session_factory, tmp_path, job_id, printer_id):
    await _finish(session_factory, job_id, printer_id, "failed")
    r = await _plain_clear(SimpleNamespace(client=client, tmp_path=tmp_path, packer=None, a=printer_id))
    assert r.json()["assumed"][0]["result"] == "assumed_failed"
    return r.json()["assumed"][0]


async def test_assumed_failures_never_waive_a_printer_but_the_same_reported_ones_do(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    await _set_queue(client, part_retry_limit=5)                                     # keep it requeueing, never held
    assert (await client.get(QUEUE)).json()["waive_after"] == 2
    a = await create_printer(name="A")
    b = await create_printer(name="B")
    for name in ("P1", "P2"):
        _, _, job = await waivers._project_job(client, upload_3mf, [a, b], tmp_path, name=name)
        await _fail_assumed(client, session_factory, tmp_path, job, a)
    assert (await client.get(f"/api/v1/printers/{a}/waivers")).json() == []

    # control: the same two failures, reported, waive the printer
    for name in ("P3", "P4"):
        _, item, job = await waivers._project_job(client, upload_3mf, [a, b], tmp_path, name=name)
        await waivers._fail_on(client, session_factory, tmp_path, job, a, item)
    (w,) = (await client.get(f"/api/v1/printers/{a}/waivers")).json()
    assert w["failures"] == 2                                                        # only the reported ones counted


async def test_an_assumed_failure_does_not_add_to_a_reported_one(client, upload_3mf, create_printer, session_factory, tmp_path):
    await _set_queue(client, part_retry_limit=5)
    a = await create_printer(name="A")
    _, _, job = await waivers._project_job(client, upload_3mf, [a], tmp_path, name="P1")
    await _fail_assumed(client, session_factory, tmp_path, job, a)
    _, item, job = await waivers._project_job(client, upload_3mf, [a], tmp_path, name="P2")
    await waivers._fail_on(client, session_factory, tmp_path, job, a, item)          # waive_after is 2: only this one counts
    assert (await client.get(f"/api/v1/printers/{a}/waivers")).json() == []


async def test_a_part_assumed_failed_on_two_printers_is_not_held_and_a_later_reported_failure_counts_only_itself(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    assert (await client.get(QUEUE)).json()["part_retry_limit"] == 2
    a = await create_printer(name="A")
    b = await create_printer(name="B")
    pid, item, job1 = await waivers._project_job(client, upload_3mf, [a, b], tmp_path, qty=1)
    first = await _fail_assumed(client, session_factory, tmp_path, job1, a)
    job2 = first["requeued_job_ids"][0]
    second = await _fail_assumed(client, session_factory, tmp_path, job2, b)         # a second printer: still not held
    assert second["requeued_job_ids"]
    assert await _counts(session_factory, item) == (0, 2, 0)                         # quantity_held stays 0
    log = (await client.get(f"/api/v1/projects/{pid}/failures")).json()
    assert [(e["printer_id"], e["resolution"]) for e in log] == [(a, "requeued"), (b, "requeued")]

    job3 = second["requeued_job_ids"][0]                                             # now a person reports a failure on A
    await _finish(session_factory, job3, a, "complete")
    with _builder(tmp_path):
        r = await client.post(f"/api/v1/printers/{a}/plate-cleared", json={
            "job_id": job3, "results": [{"project_item_id": item, "quantity_failed": 1}]})
    assert r.status_code == 200, r.text
    assert r.json()["held"] == [] and r.json()["requeued_job_ids"]                   # counts itself only: not a 2nd printer
    assert await _counts(session_factory, item) == (0, 3, 0)
    log = (await client.get(f"/api/v1/projects/{pid}/failures")).json()
    assert [e["resolution"] for e in log] == ["requeued", "requeued", "requeued"]


async def test_an_assumed_failure_after_a_reported_one_on_another_printer_is_not_held(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    a = await create_printer(name="A")
    b = await create_printer(name="B")
    pid, item, job1 = await waivers._project_job(client, upload_3mf, [a, b], tmp_path, qty=1)
    reported = await waivers._fail_on(client, session_factory, tmp_path, job1, a, item)        # a person: failed on A
    assert reported["requeued_job_ids"] and reported["held"] == []
    second = await _fail_assumed(client, session_factory, tmp_path, reported["requeued_job_ids"][0], b)   # nobody said: on B
    assert second["requeued_job_ids"]                                                          # a default never holds a part
    assert await _counts(session_factory, item) == (0, 2, 0)
    log = (await client.get(f"/api/v1/projects/{pid}/failures")).json()
    assert [(e["printer_id"], e["resolution"]) for e in log] == [(a, "requeued"), (b, "requeued")]


# ---- 9. POST /jobs/{id}/results --------------------------------------------------------------------------------------

async def _report(client, p, job_id, failures: dict[int, int]):
    with _builder(p.tmp_path, p.packer):
        return await client.post(f"/api/v1/jobs/{job_id}/results", json={
            "results": [{"project_item_id": k, "quantity_failed": v} for k, v in failures.items()]})


@pytest.mark.parametrize("kind", KINDS)
async def test_a_plate_awaiting_results_can_be_reported_late(kind, client, upload_3mf, create_printer, session_factory, tmp_path):
    await _set_queue(client, plate_clear_complete="ask")
    p = await _plate(kind, client, upload_3mf, create_printer, session_factory, tmp_path)
    await _finish(session_factory, p.job, p.a)
    await _plain_clear(p)
    assert await _outcome(session_factory, p.job) is None

    r = await _report(client, p, p.job, {p.item: 1})

    assert r.status_code == 200, r.text
    assert await _outcome(session_factory, p.job) == "reviewed"
    assert await _counts(session_factory, p.item) == (2, 1, 0)
    assert (await _pending(client, p.a))["job_id"] is None
    if kind == "fixed":
        new = [j for j in await _project_jobs(session_factory, p.pid) if j.id != p.job]
        assert [json.loads(j.project_item_quantities) for j in new] == [{str(p.item): 1}]
        assert sorted(j.id for j in new) == sorted(r.json()["requeued_job_ids"])
    else:
        assert r.json()["requeued_job_ids"] == []
        assert len(await pools._pool_jobs(session_factory, p.gid)) == 1
        assert await _pool_stats(p) == {"done": 2, "printing": 0, "left": 1}         # the failed part is back in the pool


@pytest.mark.parametrize("kind", KINDS)
async def test_an_assumed_good_plate_can_be_corrected_once(kind, client, upload_3mf, create_printer, session_factory, tmp_path):
    p = await _plate(kind, client, upload_3mf, create_printer, session_factory, tmp_path)
    await _finish(session_factory, p.job, p.a)
    await _plain_clear(p)
    assert await _counts(session_factory, p.item) == (3, 0, 0)
    assert await _outcome(session_factory, p.job) == "assumed_good"

    r = await _report(client, p, p.job, {p.item: 1})

    assert r.status_code == 200, r.text
    assert await _counts(session_factory, p.item) == (2, 1, 0)                       # dropped from 3, not 3 + 2
    assert await _outcome(session_factory, p.job) == "reviewed"
    async with session_factory() as s:
        rows = (await s.execute(select(JobItemFailure).where(JobItemFailure.job_id == p.job))).scalars().all()
    assert [(x.quantity_failed, x.quantity_on_plate, x.resolution) for x in rows] == [(1, 3, "requeued")]   # replaced, not added to
    if kind == "fixed":
        new = [j for j in await _project_jobs(session_factory, p.pid) if j.id != p.job]
        assert [json.loads(j.project_item_quantities) for j in new] == [{str(p.item): 1}]
    else:
        assert await _pool_stats(p) == {"done": 2, "printing": 0, "left": 1}

    again = await _report(client, p, p.job, {p.item: 2})                             # a person's report is final
    assert again.status_code == 409
    assert await _counts(session_factory, p.item) == (2, 1, 0)


@pytest.mark.parametrize("kind", KINDS)
async def test_an_assumed_failed_plate_cannot_be_reported_or_changed(
        kind, client, upload_3mf, create_printer, session_factory, tmp_path):
    p = await _plate(kind, client, upload_3mf, create_printer, session_factory, tmp_path)
    await _finish(session_factory, p.job, p.a, "failed")
    await _plain_clear(p)
    counts = await _counts(session_factory, p.item)
    n_jobs = len(await _project_jobs(session_factory, p.pid))
    stats = await _pool_stats(p) if kind == "pool" else None

    r = await _report(client, p, p.job, {})
    assert r.status_code == 409

    assert await _outcome(session_factory, p.job) == "assumed_failed"
    assert await _counts(session_factory, p.item) == counts
    assert len(await _project_jobs(session_factory, p.pid)) == n_jobs
    if kind == "pool":
        assert await _pool_stats(p) == stats


async def test_only_a_finished_project_plate_can_be_reported(client, upload_3mf, create_printer, create_job, session_factory, tmp_path):
    p = await _plate("fixed", client, upload_3mf, create_printer, session_factory, tmp_path)
    for status in ("queued", "printing", "cancelled"):
        async with session_factory() as s:
            (await s.get(Job, p.job)).status = status
            await s.commit()
        assert (await _report(client, p, p.job, {p.item: 1})).status_code == 409, status
    assert await _counts(session_factory, p.item) == (0, 0, 0)
    assert await _outcome(session_factory, p.job) is None

    plain = await create_job()                                                      # a job outside any project
    async with session_factory() as s:
        (await s.get(Job, plain)).status = "complete"
        await s.commit()
    assert (await _report(client, p, plain, {})).status_code == 409
    assert (await _report(client, p, 999_999, {})).status_code == 404


async def test_a_part_that_is_not_on_the_plate_is_refused_and_nothing_is_recorded(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    p = await _plate("fixed", client, upload_3mf, create_printer, session_factory, tmp_path)
    await _finish(session_factory, p.job, p.a)
    r = await _report(client, p, p.job, {p.item + 1000: 1})
    assert r.status_code == 422
    assert await _outcome(session_factory, p.job) is None
    assert await _counts(session_factory, p.item) == (0, 0, 0)
    assert len(await _project_jobs(session_factory, p.pid)) == 1
    async with session_factory() as s:
        assert (await s.execute(select(JobItemFailure).where(JobItemFailure.job_id == p.job))).first() is None
    assert (await _pending(client, p.a))["job_id"] == p.job                          # still awaiting results


# ---- 10. an assumed-failed plate is final -----------------------------------------------------------------------------

async def test_an_assumed_failed_plate_cannot_be_requeued_or_re_reviewed(client, upload_3mf, create_printer, session_factory, tmp_path):
    p = await _plate("fixed", client, upload_3mf, create_printer, session_factory, tmp_path)
    await _finish(session_factory, p.job, p.a, "failed")
    await _plain_clear(p)
    n_jobs = len(await _project_jobs(session_factory, p.pid))
    counts = await _counts(session_factory, p.item)

    r = await client.patch(f"/api/v1/jobs/{p.job}/configs", json={"printer_configs": [{"printer_id": p.b, "print_profile": "0.20mm"}]})
    assert r.status_code == 409, r.text
    r = await client.put(f"/api/v1/jobs/{p.job}/outcome", json={"failures": []})
    assert r.status_code == 409, r.text

    async with session_factory() as s:
        job = await s.get(Job, p.job)
        assert (job.status, job.outcome) == ("failed", "assumed_failed")
        cfgs = (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == p.job))).scalars().all()
    assert sorted(c.printer_id for c in cfgs) == [p.a, p.b] and len(cfgs) == 2          # untouched
    assert await _counts(session_factory, p.item) == counts
    assert len(await _project_jobs(session_factory, p.pid)) == n_jobs


# ---- 11. plate-results print_failed ----------------------------------------------------------------------------------

@pytest.mark.parametrize("status,failed", [("failed", True), ("complete", False)])
async def test_plate_results_says_whether_the_print_failed(status, failed, client, upload_3mf, create_printer, session_factory, tmp_path):
    p = await _plate("fixed", client, upload_3mf, create_printer, session_factory, tmp_path)
    await _finish(session_factory, p.job, p.a, status)
    r = await _pending(client, p.a)
    assert r["job_id"] == p.job and r["print_failed"] is failed
    assert (await _pending(client, p.b))["print_failed"] is False                    # nothing to report: not "failed"


# ---- 12. pausing a printer that keeps failing ------------------------------------------------------------------------

async def _engine_for(session_factory, *printers):
    qe = QueueEngine(session_factory, _make_mock_printer_manager(list(printers)), MagicMock())
    _install_fake_put(qe)
    return qe


async def _end_print(client, session_factory, create_job, qe, printer_id, how):
    """A print that started on `printer_id` ends: 'failed' through the engine's failure path, 'complete' through completion."""
    job_id = await create_job(printer_id=printer_id)
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.status, job.assigned_printer_id = "uploading", printer_id
        await s.commit()
    assert await qe.mark_printing(job_id, printer_id, origin="core")      # the real start: status, plate gate, print-start detail
    if how == "failed":
        assert await qe.fail_started_job(job_id, printer_id, reason="spaghetti", origin="core")
    else:
        assert await qe.complete_job(job_id, printer_id, origin="core")
    return job_id


async def _printer_state(client, printer_id):
    detail = (await client.get(f"/api/v1/printers/{printer_id}")).json()
    fleet = {p["id"]: p for p in (await client.get("/api/v1/fleet")).json()}[printer_id]
    assert (detail["queue_on"], detail["queue_paused_reason"]) == (fleet["queue_on"], fleet["queue_paused_reason"])
    return detail["queue_on"], detail["queue_paused_reason"]


@pytest.fixture
def notify():
    with patch("app.services.queue_engine.notify", new_callable=AsyncMock) as n:
        yield n


async def test_a_printer_is_paused_after_three_failed_prints_in_a_row(
        client, create_printer, create_job, session_factory, notify):
    a = await create_printer(name="A")
    qe = await _engine_for(session_factory, a)
    for n in (1, 2):
        await _end_print(client, session_factory, create_job, qe, a, "failed")
        assert await _printer_state(client, a) == (True, None), n                    # not before the third
    notify.assert_not_awaited()

    await _end_print(client, session_factory, create_job, qe, a, "failed")

    assert await _printer_state(client, a) == (False, "3 prints failed in a row")
    notify.assert_awaited_once()
    assert notify.await_args.args[0] == "printer.paused"
    assert "A" in notify.await_args.args[2] and "3 prints failed in a row" in notify.await_args.args[3]


async def test_a_completed_print_between_failures_starts_the_count_again(client, create_printer, create_job, session_factory, notify):
    a = await create_printer(name="A")
    qe = await _engine_for(session_factory, a)
    for how in ("failed", "failed", "complete", "failed", "failed"):
        await _end_print(client, session_factory, create_job, qe, a, how)
    assert await _printer_state(client, a) == (True, None)
    notify.assert_not_awaited()
    await _end_print(client, session_factory, create_job, qe, a, "failed")           # three since the good print
    assert await _printer_state(client, a) == (False, "3 prints failed in a row")


async def test_only_this_printers_own_prints_are_counted(client, create_printer, create_job, session_factory, notify):
    a = await create_printer(name="A")
    b = await create_printer(name="B")
    qe = await _engine_for(session_factory, a, b)
    for printer in (a, b, a, b, a, b):
        await _end_print(client, session_factory, create_job, qe, printer, "failed")
    assert await _printer_state(client, a) == (False, "3 prints failed in a row")
    assert await _printer_state(client, b) == (False, "3 prints failed in a row")
    assert notify.await_count == 2
    await _end_print(client, session_factory, create_job, qe, a, "complete")
    c = await create_printer(name="C")
    await _end_print(client, session_factory, create_job, await _engine_for(session_factory, c), c, "failed")
    assert await _printer_state(client, c) == (True, None)


async def test_zero_never_pauses(client, create_printer, create_job, session_factory, notify):
    await _set_queue(client, pause_after_failed_prints=0)
    a = await create_printer(name="A")
    qe = await _engine_for(session_factory, a)
    for _ in range(5):
        await _end_print(client, session_factory, create_job, qe, a, "failed")
    assert await _printer_state(client, a) == (True, None)
    notify.assert_not_awaited()


async def test_one_pauses_on_the_first_failure(client, create_printer, create_job, session_factory, notify):
    await _set_queue(client, pause_after_failed_prints=1)
    a = await create_printer(name="A")
    qe = await _engine_for(session_factory, a)
    await _end_print(client, session_factory, create_job, qe, a, "failed")
    assert await _printer_state(client, a) == (False, "1 prints failed in a row")
    notify.assert_awaited_once()


async def test_a_printer_whose_queue_is_already_off_is_left_alone(client, create_printer, create_job, session_factory, notify):
    await _set_queue(client, pause_after_failed_prints=1)
    a = await create_printer(name="A")
    await client.patch(f"/api/v1/printers/{a}", json={"queue_on": False})            # a person switched it off
    qe = await _engine_for(session_factory, a)
    await _end_print(client, session_factory, create_job, qe, a, "failed")
    assert await _printer_state(client, a) == (False, None)                          # no reason is invented
    notify.assert_not_awaited()


async def test_switching_the_queue_back_on_resumes_a_paused_printer_and_the_count(
        client, create_printer, create_job, session_factory, notify):
    a = await create_printer(name="A")
    qe = await _engine_for(session_factory, a)
    for _ in range(3):
        await _end_print(client, session_factory, create_job, qe, a, "failed")
    assert await _printer_state(client, a) == (False, "3 prints failed in a row")

    assert (await client.patch(f"/api/v1/printers/{a}", json={"queue_on": True})).status_code == 200
    assert await _printer_state(client, a) == (True, None)

    await _end_print(client, session_factory, create_job, qe, a, "failed")           # 4th failure in a row: pauses again
    assert await _printer_state(client, a) == (False, "3 prints failed in a row")
    assert notify.await_count == 2


async def test_a_person_switching_the_queue_off_leaves_no_reason(client, create_printer):
    a = await create_printer(name="A")
    assert (await client.patch(f"/api/v1/printers/{a}", json={"queue_on": False})).status_code == 200
    assert await _printer_state(client, a) == (False, None)


async def test_a_person_switching_it_off_clears_a_stale_pause_reason(client, create_printer, create_job, session_factory, notify):
    a = await create_printer(name="A")
    qe = await _engine_for(session_factory, a)
    for _ in range(3):
        await _end_print(client, session_factory, create_job, qe, a, "failed")
    await client.patch(f"/api/v1/printers/{a}", json={"queue_on": False})           # still off, now by a person's decision
    assert await _printer_state(client, a) == (False, None)


async def test_a_paused_printer_claims_nothing(client, create_printer, create_job, session_factory, notify):
    a = await create_printer(name="A")
    qe = await _engine_for(session_factory, a)
    for _ in range(3):
        await _end_print(client, session_factory, create_job, qe, a, "failed")
    assert (await _printer_state(client, a))[0] is False
    waiting = await create_job(printer_id=a)
    async with session_factory() as s:
        await qe._try_claim_for_printer(s, a, slice_only=True)
    async with session_factory() as s:
        job = await s.get(Job, waiting)
        assert (job.status, job.assigned_printer_id) == ("queued", None)

    await client.patch(f"/api/v1/printers/{a}", json={"queue_on": True})            # control: the same printer claims once resumed
    from tests.fake_providers import FakeSlicingProvider
    from tests.slicing_env import slicing_provider
    from tests.waiting import cancel_background_tasks
    with slicing_provider(FakeSlicingProvider()):
        async with session_factory() as s:
            await qe._try_claim_for_printer(s, a, slice_only=True)
        await cancel_background_tasks("slice-")
    async with session_factory() as s:
        assert (await s.get(Job, waiting)).assigned_printer_id == a


async def test_a_bookkeeping_error_never_fails_the_plate_clear_itself(client, create_printer, session_factory):
    """The clear is the QR / home-automation hook: if applying the defaults blows up, the printer is still released, nothing
    half-recorded is kept, and the plates stay awaiting results."""
    from unittest.mock import patch
    from app.models import Printer
    a = await create_printer(name="A")
    async with session_factory() as s:
        (await s.get(Printer, a)).awaiting_plate_clear = True
        await s.commit()
    with patch("app.services.print_results.apply_clear_defaults", side_effect=RuntimeError("boom")):
        r = await client.post(f"/api/v1/printers/{a}/plate-cleared")
    assert r.status_code == 200 and r.json() == {"ok": True}
    async with session_factory() as s:
        assert (await s.get(Printer, a)).awaiting_plate_clear is False


# ---- review hardening ----

async def test_upload_or_start_errors_are_not_failed_prints(client, create_printer, create_job, session_factory, notify):
    """A job that failed before its print started (upload / start error) is a failed job, not a failed print: it never counts
    toward the pause."""
    a = await create_printer(name="A")
    qe = await _engine_for(session_factory, a)
    for _ in range(3):
        job_id = await create_job(printer_id=a)
        async with session_factory() as s:
            job = await s.get(Job, job_id)
            job.status, job.assigned_printer_id, job.printed_on_printer_id = "uploading", a, a
            await s.commit()
        await qe._fail_job_post_slice(job_id, a, "upload failed")
        async with session_factory() as s:
            assert (await s.get(Job, job_id)).status == "failed"
    await _end_print(client, session_factory, create_job, qe, a, "failed")        # one real failed print
    assert await _printer_state(client, a) == (True, None)
    for _ in range(2):
        await _end_print(client, session_factory, create_job, qe, a, "failed")
    assert await _printer_state(client, a) == (False, "3 prints failed in a row")


def test_a_channel_can_subscribe_to_the_pause_notification():
    from app.plugins.capabilities.notify_channel import NOTIFY_EVENTS
    assert "printer.paused" in NOTIFY_EVENTS


async def test_plates_finished_before_the_upgrade_are_never_given_a_default(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    """`queue_config.plate_clear_since` (set by the upgrade): the first clear afterwards must not settle months of old
    unreported plates. They stay awaiting results, and a person can still report them from History."""
    from app.models import QueueConfig
    pl = await _plate("fixed", client, upload_3mf, create_printer, session_factory, tmp_path, qty=2)
    item, job_id, a = pl.item, pl.job, pl.a
    await _finish(session_factory, job_id, a, "complete")
    await client.get("/api/v1/settings/queue")                                   # makes sure the settings row exists
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        cfg = await s.get(QueueConfig, 1)
        cfg.plate_clear_since = "2999-01-01T00:00:00+00:00"                        # the upgrade happened after this plate
        completed_at = job.completed_at
        await s.commit()
    assert completed_at is not None

    r = await _plain_clear(pl)
    assert "assumed" not in r.json()
    async with session_factory() as s:
        assert (await s.get(Job, job_id)).outcome is None
        assert (await s.get(ProjectItem, item)).quantity_completed == 0

    with _builder(tmp_path):
        late = await client.post(f"/api/v1/jobs/{job_id}/results", json={"results": []})
    assert late.status_code == 200
    async with session_factory() as s:
        assert (await s.get(Job, job_id)).outcome == "reviewed"
        assert (await s.get(ProjectItem, item)).quantity_completed == 2


async def test_a_reported_plate_or_a_test_print_cannot_be_reported_again_from_history(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    pl = await _plate("fixed", client, upload_3mf, create_printer, session_factory, tmp_path, qty=2)
    item, job_id, a = pl.item, pl.job, pl.a
    await _finish(session_factory, job_id, a, "complete")
    async with session_factory() as s:                                            # what a cleared test print looks like
        (await s.get(Job, job_id)).outcome = "reviewed"
        await s.commit()
    r = await client.post(f"/api/v1/jobs/{job_id}/results", json={"results": []})
    assert r.status_code == 409
    async with session_factory() as s:
        assert (await s.get(ProjectItem, item)).quantity_completed == 0            # an extra copy credits nothing


async def test_the_pause_reason_survives_a_fleet_backup_and_restore(client, create_printer, session_factory):
    from app.models import Printer
    a = await create_printer(name="Paused")
    async with session_factory() as s:
        p = await s.get(Printer, a)
        p.queue_on, p.queue_paused_reason = False, "3 prints failed in a row"
        await s.commit()
    backup = (await client.get("/api/v1/settings/fleet-backup")).json()
    (entry,) = [p for p in backup["printers"] if p["name"] == "Paused"]
    assert (entry["queue_on"], entry["queue_paused_reason"]) == (False, "3 prints failed in a row")


async def test_a_bookkeeping_error_after_a_write_leaves_nothing_half_recorded(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    """The defaults blow up AFTER settling the plate: the savepoint undoes it, the plate still awaits results (History can
    report it) and the printer is released all the same."""
    from app.services import print_results as pr
    pl = await _plate("fixed", client, upload_3mf, create_printer, session_factory, tmp_path, qty=2)
    await _finish(session_factory, pl.job, pl.a, "complete")
    real = pr.apply_clear_defaults

    async def settle_then_fail(session, printer):
        await real(session, printer)
        await session.flush()
        raise RuntimeError("boom")

    with patch("app.services.print_results.apply_clear_defaults", side_effect=settle_then_fail):
        r = await _plain_clear(pl)
    assert r.json() == {"ok": True}
    async with session_factory() as s:
        assert (await s.get(Job, pl.job)).outcome is None
        assert (await s.get(ProjectItem, pl.item)).quantity_completed == 0
        assert (await s.get(Printer, pl.a)).awaiting_plate_clear is False
    assert (await _pending(client, pl.a))["job_id"] == pl.job                    # still offered for a report


async def test_an_assumed_failed_plate_that_cannot_be_repacked_holds_its_parts_and_says_so(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    pl = await _plate("fixed", client, upload_3mf, create_printer, session_factory, tmp_path, qty=2)
    await _finish(session_factory, pl.job, pl.a, "failed")
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=None):       # no builder: the repack fails
        r = await client.post(f"/api/v1/printers/{pl.a}/plate-cleared")
    assert r.status_code == 200, r.text
    (assumed,) = r.json()["assumed"]
    assert assumed["result"] == "assumed_failed" and assumed["requeued_job_ids"] == []
    assert assumed["held"] == [{"project_item_id": pl.item, "quantity": 2}] and assumed["requeue_errors"]
    async with session_factory() as s:
        assert (await s.get(ProjectItem, pl.item)).quantity_held == 2             # a person decides: Reprint or Drop
        assert (await s.get(Printer, pl.a)).awaiting_plate_clear is False
