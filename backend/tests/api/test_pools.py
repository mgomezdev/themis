"""BIZ-362: a pool group is one queue entry; each idle printer packs the remaining parts that fit its own bed into its next plate."""
import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models import Job, JobPrinterConfig, Printer, ProjectGroup
from app.services.providers import contract as c
from app.services.queue_engine import QueueEngine
from tests.fake_providers import FakeProjectConstructor, FakeSlicingProvider
from tests.services.test_queue_engine import _install_fake_put, _make_mock_printer_manager
from tests.slicing_env import slicing_provider


class PoolPacker(FakeProjectConstructor):
    """`next_plate` fits bed_x // 100 parts (nothing below 100 mm), taken from the inputs in order."""
    def __init__(self, fail_times: int = 0):
        super().__init__()
        self.next_calls: list = []
        self.fail_times = fail_times

    def next_plate(self, request, output_dir, *, max_parts=None, timeout=c.DEFAULT_PRODUCE_TIMEOUT_S, cancel=None):
        self.next_calls.append(request)
        if self.fail_times:
            self.fail_times -= 1
            raise c.ProviderError(c.ErrorCode.FAILED, "sidecar hiccup")
        room = int(request.target.bed_mm[0] // 100)
        if max_parts:
            room = min(room, max_parts)
        if room == 0:
            raise c.NothingFits("nothing fits this bed")
        consumed = {}
        for i, inp in enumerate(request.inputs):
            take = min(room, inp.quantity)
            if take:
                consumed[i] = take
                room -= take
        out = output_dir / "next.3mf"
        out.write_bytes(b"PK-next")
        return c.Artifact(out, c.ArtifactKind.PROJECT_3MF, self.identity), consumed


async def _pool_project(client, upload_3mf, printers, tmp_path, packer, qty=10):
    pid = (await client.post("/api/v1/projects", json={"name": "Pool"})).json()["id"]
    (g,) = (await client.get(f"/api/v1/projects/{pid}/groups")).json()
    r = await client.patch(f"/api/v1/projects/{pid}/groups/{g['id']}", json={
        "strategy": "pool", "targets": [{"target_key": str(p), "process_profile": "0.2"} for p in printers]})
    assert r.status_code == 200, r.text
    fid = await upload_3mf(filename="part.stl", data=b"solid part\nendsolid\n")
    item = (await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "quantity": qty})).json()["id"]
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=packer):
        r = await client.post(f"/api/v1/projects/{pid}/generate", json={})
    assert r.status_code == 200, r.text
    assert r.json()["jobs"] == []                                   # a pool is a queue entry, not jobs
    return pid, g["id"], item


async def _claim(session_factory, qe, printer_id, tmp_path, packer):
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.services.providers.construction.get_project_constructor", return_value=packer), \
         slicing_provider(FakeSlicingProvider()):
        async with session_factory() as s:
            await qe._try_claim_for_printer(s, printer_id, slice_only=True)
        await qe.settle_pool_packs()


def _engine(session_factory, printers):
    qe = QueueEngine(session_factory, _make_mock_printer_manager(printers), MagicMock())
    _install_fake_put(qe)
    return qe


async def _pool_jobs(session_factory, group_id):
    async with session_factory() as s:
        return (await s.execute(select(Job).where(Job.pool_group_id == group_id).order_by(Job.id))).scalars().all()


async def test_each_printer_gets_a_plate_sized_to_its_bed_and_nothing_is_reserved_twice(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    small = await create_printer(name="Small", bed_x_mm=180.0, bed_y_mm=180.0)
    mid = await create_printer(name="Mid", bed_x_mm=256.0, bed_y_mm=256.0)
    big = await create_printer(name="Big", bed_x_mm=350.0, bed_y_mm=350.0)
    packer = PoolPacker()
    pid, gid, item = await _pool_project(client, upload_3mf, [small, mid, big], tmp_path, packer, qty=5)
    qe = _engine(session_factory, [small, mid, big])
    for p in (small, mid, big):
        await _claim(session_factory, qe, p, tmp_path, packer)
    jobs = await _pool_jobs(session_factory, gid)
    got = {}
    async with session_factory() as s:
        for j in jobs:
            (cfg,) = (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == j.id))).scalars().all()
            got[cfg.printer_id] = (json.loads(j.project_item_quantities)[str(item)], cfg.print_profile)
    assert got == {small: (1, "0.2"), mid: (2, "0.2"), big: (2, "0.2")}            # 1 + 2 + the 2 left (of 3 room)
    assert [i.quantity for i in packer.next_calls[-1].inputs] == [2]               # big was offered only what was left

    await _claim(session_factory, qe, small, tmp_path, packer)                      # nothing left: no new plate
    assert len(await _pool_jobs(session_factory, gid)) == 3
    stats = (await client.get(f"/api/v1/projects/{pid}/groups")).json()[0]["pool"]
    assert stats == {"done": 0, "printing": 5, "left": 0}


async def test_a_printer_nothing_fits_skips_the_pool_and_claims_the_next_job(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    tiny = await create_printer(name="Tiny", bed_x_mm=90.0, bed_y_mm=90.0)
    packer = PoolPacker()
    pid, gid, _ = await _pool_project(client, upload_3mf, [tiny], tmp_path, packer)
    other = (await client.post("/api/v1/projects", json={"name": "Other"})).json()["id"]
    f2 = await upload_3mf(filename="other.stl", data=b"solid other\nendsolid\n")
    await client.post(f"/api/v1/projects/{other}/items", json={"file_id": f2, "quantity": 1})
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        next_job = (await client.post(f"/api/v1/projects/{other}/generate",
                                      json={"eligible_printer_ids": [tiny], "process_preset": "0.2"})).json()["jobs"][0]["id"]
    qe = _engine(session_factory, [tiny])
    await _claim(session_factory, qe, tiny, tmp_path, packer)                     # packs, learns nothing fits
    await _claim(session_factory, qe, tiny, tmp_path, packer)                     # skips the pool, takes the job
    assert await _pool_jobs(session_factory, gid) == []
    async with session_factory() as s:
        assert (await s.get(Job, next_job)).assigned_printer_id == tiny
    assert len(packer.next_calls) == 1                                            # remembered: not asked again


async def test_a_busy_pool_is_skipped_by_another_printer(client, upload_3mf, create_printer, session_factory, tmp_path):
    a = await create_printer(name="A", bed_x_mm=256.0, bed_y_mm=256.0)
    b = await create_printer(name="B", bed_x_mm=256.0, bed_y_mm=256.0)
    packer = PoolPacker()
    pid, gid, _ = await _pool_project(client, upload_3mf, [a, b], tmp_path, packer)
    qe = _engine(session_factory, [a, b])
    qe._pool_busy[gid] = a                                                        # packing for A right now
    with patch("app.services.providers.construction.get_project_constructor", return_value=packer), \
         slicing_provider(FakeSlicingProvider()):
        async with session_factory() as s:
            await qe._try_claim_for_printer(s, b, slice_only=True)
        assert qe._pool_busy == {gid: a} and not qe._pool_tasks                   # B started no pack of its own
        await qe.settle_pool_packs()
    assert packer.next_calls == []


async def test_a_pack_error_backs_off_then_retries(client, upload_3mf, create_printer, session_factory, tmp_path):
    a = await create_printer(name="A", bed_x_mm=256.0, bed_y_mm=256.0)
    packer = PoolPacker(fail_times=1)
    pid, gid, _ = await _pool_project(client, upload_3mf, [a], tmp_path, packer)
    qe = _engine(session_factory, [a])
    await _claim(session_factory, qe, a, tmp_path, packer)
    assert await _pool_jobs(session_factory, gid) == [] and gid in qe._pool_backoff
    await _claim(session_factory, qe, a, tmp_path, packer)                        # still backing off
    assert len(packer.next_calls) == 1
    qe._pool_backoff[gid] = 0                                                     # backoff elapsed
    await _claim(session_factory, qe, a, tmp_path, packer)
    assert len(await _pool_jobs(session_factory, gid)) == 1


async def test_a_failed_pool_part_goes_back_into_the_pool(client, upload_3mf, create_printer, session_factory, tmp_path):
    a = await create_printer(name="A", bed_x_mm=256.0, bed_y_mm=256.0)
    packer = PoolPacker()
    pid, gid, item = await _pool_project(client, upload_3mf, [a], tmp_path, packer, qty=2)
    qe = _engine(session_factory, [a])
    await _claim(session_factory, qe, a, tmp_path, packer)
    (job,) = await _pool_jobs(session_factory, gid)
    async with session_factory() as s:
        j = await s.get(Job, job.id)
        j.status, j.assigned_printer_id = "complete", a
        (await s.get(Printer, a)).awaiting_plate_clear = True
        await s.commit()
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=packer):
        r = await client.post(f"/api/v1/printers/{a}/plate-cleared", json={
            "job_id": job.id, "results": [{"project_item_id": item, "quantity_failed": 1}]})
    assert r.status_code == 200 and r.json()["requeued_job_ids"] == []            # back in the pool, not a new job
    stats = (await client.get(f"/api/v1/projects/{pid}/groups")).json()[0]["pool"]
    assert stats == {"done": 1, "printing": 0, "left": 1}


async def test_a_pool_no_eligible_printer_can_take_is_reported(client, upload_3mf, create_printer, session_factory, tmp_path):
    a = await create_printer(name="A", loaded_filaments=[{"slot": 0, "type": "PETG", "subtype": "standard", "color": "#000000"}])
    packer = PoolPacker()
    pid = (await client.post("/api/v1/projects", json={"name": "Pool"})).json()["id"]
    (g,) = (await client.get(f"/api/v1/projects/{pid}/groups")).json()
    await client.patch(f"/api/v1/projects/{pid}/groups/{g['id']}", json={"strategy": "pool", "targets": [{"target_key": str(a)}]})
    fid = await upload_3mf(filename="part.stl", data=b"solid part\nendsolid\n")
    await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "quantity": 2, "filament_type": "PLA",
                                                              "filament_subtype": "any"})
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=packer):
        await client.post(f"/api/v1/projects/{pid}/generate", json={})
    (grp,) = (await client.get(f"/api/v1/projects/{pid}/groups")).json()
    assert grp["pool"]["left"] == 2 and "no eligible printer" in (grp["pool_warning"] or "")


async def test_a_solo_item_in_a_pool_is_still_generated_as_a_job_and_not_pooled(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    a = await create_printer(name="A", bed_x_mm=256.0, bed_y_mm=256.0)
    packer = PoolPacker()
    pid = (await client.post("/api/v1/projects", json={"name": "Pool"})).json()["id"]
    (g,) = (await client.get(f"/api/v1/projects/{pid}/groups")).json()
    await client.patch(f"/api/v1/projects/{pid}/groups/{g['id']}", json={"strategy": "pool", "targets": [{"target_key": str(a)}]})
    f1 = await upload_3mf(filename="part.stl", data=b"solid part\nendsolid\n")
    f2 = await upload_3mf(filename="solo.stl", data=b"solid solo\nendsolid\n")
    await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": f1, "quantity": 3})
    await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": f2, "quantity": 2, "pack_rule": "solo"})
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=packer):
        r = await client.post(f"/api/v1/projects/{pid}/generate", json={})
    assert r.status_code == 200, r.text
    assert len(r.json()["jobs"]) >= 1                                               # the Solo item is packed now
    async with session_factory() as s:
        assert all(j.pool_group_id is None for j in (await s.execute(select(Job))).scalars())
    (grp,) = (await client.get(f"/api/v1/projects/{pid}/groups")).json()
    assert grp["pool"] == {"done": 0, "printing": 0, "left": 3}                     # only the Split item waits in the pool


async def test_the_queue_lists_active_pools_with_their_counts_and_labels_their_plates(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    a = await create_printer(name="A", bed_x_mm=256.0, bed_y_mm=256.0)
    packer = PoolPacker()
    pid, gid, _ = await _pool_project(client, upload_3mf, [a], tmp_path, packer, qty=3)
    qe = _engine(session_factory, [a])
    await _claim(session_factory, qe, a, tmp_path, packer)
    (pool,) = (await client.get("/api/v1/queue/pools")).json()
    assert {k: pool[k] for k in ("group_id", "project_id", "project_name", "done", "printing", "left", "warning")} == {
        "group_id": gid, "project_id": pid, "project_name": "Pool", "done": 0, "printing": 2, "left": 1, "warning": None}
    queued = [j for j in (await client.get("/api/v1/queue")).json() if j["pool_group_id"] == gid]
    assert len(queued) == 1
