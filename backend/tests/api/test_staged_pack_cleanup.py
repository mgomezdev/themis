"""GH #269: a pack staged under `_tmp_` whose jobs never commit is discarded; one that jobs reference is kept."""
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import Job, Project, SlicedVersion, UploadedFile
from app.services import pack_staging
from app.services.artifact_store import LocalDiskArtifactStore
from tests.api.test_pack_move_failure import _project_with_two_packs
from tests.fake_providers import FakeProjectConstructor

pytestmark = pytest.mark.usefixtures("fixed_groups")


def _staged_on_disk(root):
    base = root / pack_staging.PACK_FOLDER
    return sorted(p for p in base.rglob("*") if p.is_file() and "/_tmp_" in p.as_posix()) if base.exists() else []


async def test_a_pack_whose_jobs_fail_to_commit_is_discarded_and_an_earlier_pack_is_kept(
        client, upload_3mf, create_printer, session_factory, tmp_path, monkeypatch):
    from app.api.routes import project_generate
    pid, printer = await _project_with_two_packs(client, upload_3mf, create_printer)
    real = project_generate.model_targets.materialize_job
    calls, seen = [], []

    async def fail_on_the_second_pack(session, job_id):
        calls.append(job_id)
        if len(calls) == 2:                     # each pack has one job: this is the second pack's, before its commit
            async with session_factory() as other:  # the staged row is committed but not offered for reuse yet
                staged = (await other.execute(select(UploadedFile).where(UploadedFile.relative_path.like("%/_tmp_%")))).scalars().all()
            seen.extend((f.relative_path, f.pack_recipe_hash) for f in staged)
            raise RuntimeError("boom")
        return await real(session, job_id)

    monkeypatch.setattr(project_generate.model_targets, "materialize_job", fail_on_the_second_pack)
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()), \
         pytest.raises(RuntimeError, match="boom"):
        await client.post(f"/api/v1/projects/{pid}/generate", json={"eligible_printer_ids": [printer], "process_preset": "0.20mm"})
    assert len(calls) == 2 and len(seen) == 1 and seen[0][1] is None
    async with session_factory() as s:
        jobs = (await s.execute(select(Job).where(Job.project_id == pid))).scalars().all()
        packs = (await s.execute(select(UploadedFile).where(UploadedFile.folder.like(f"/{pack_staging.PACK_FOLDER}%")))).scalars().all()
    assert len(jobs) == 1 and [p.id for p in packs] == [jobs[0].uploaded_file_id]      # the second pack's row is gone
    assert "/_tmp_" not in packs[0].relative_path and (tmp_path / "library" / packs[0].relative_path).is_file()
    assert _staged_on_disk(tmp_path / "library") == []                                   # ... and so is its file


async def _staged(session_factory, store, name: str, *, row: bool) -> tuple[str, int | None]:
    import io
    key = f"{pack_staging.STAGING_PREFIX}{name}/p.3mf"
    store.put(key, io.BytesIO(b"pk"))
    if not row:
        return key, None
    async with session_factory() as s:
        f = UploadedFile(original_filename="p.3mf", plates=[], uploaded_at="2026-01-01T00:00:00", relative_path=key,
                         folder=f"/{pack_staging.PACK_FOLDER}", size_bytes=2, content_hash="x" + name, mtime=0.0, missing=False)
        s.add(f)
        await s.commit()
        return key, f.id


async def test_the_sweep_removes_unreferenced_staged_packs_and_keeps_referenced_ones(session_factory, tmp_path, create_job):
    store = LocalDiskArtifactStore(tmp_path / "lib", prune_empty=True)
    orphan_file, _ = await _staged(session_factory, store, "nofile", row=False)
    orphan_row, orphan_id = await _staged(session_factory, store, "norow", row=True)
    live, live_id = await _staged(session_factory, store, "live", row=True)
    job_id = await create_job(file_id=live_id)
    result, result_id = await _staged(session_factory, store, "result", row=True)
    sliced, sliced_id = await _staged(session_factory, store, "sliced", row=True)
    async with session_factory() as s:
        s.add(Project(name="P", result_file_id=result_id, created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00"))
        gcode = UploadedFile(original_filename="p.gcode", plates=[], uploaded_at="2026-01-01T00:00:00", relative_path="g/p.gcode",
                             folder="/g", size_bytes=1, content_hash="g", mtime=0.0, missing=False)
        s.add(gcode)
        await s.flush()
        s.add(SlicedVersion(file_id=gcode.id, source_file_id=sliced_id, machine_preset="m", process_preset="p",
                            artifact_kind="gcode", cache_key="k", created_at="2026-01-01T00:00:00"))
        await s.commit()
    import io
    store.put(f"{pack_staging.PACK_FOLDER}/7/done.3mf", io.BytesIO(b"pk"))           # a moved pack: not staged

    assert await pack_staging.sweep(session_factory, store) == 2
    assert not store.exists(orphan_file) and not store.exists(orphan_row)
    assert store.exists(live) and store.exists(f"{pack_staging.PACK_FOLDER}/7/done.3mf")
    assert store.exists(result) and store.exists(sliced)
    async with session_factory() as s:
        assert await s.get(UploadedFile, orphan_id) is None
        assert await s.get(UploadedFile, live_id) is not None
        assert await s.get(UploadedFile, result_id) is not None and await s.get(UploadedFile, sliced_id) is not None
        assert (await s.get(Job, job_id)).uploaded_file_id == live_id


async def test_discard_keeps_a_referenced_pack_and_ignores_keys_outside_staging(session_factory, tmp_path, create_job):
    store = LocalDiskArtifactStore(tmp_path / "lib", prune_empty=True)
    live, live_id = await _staged(session_factory, store, "ref", row=True)
    await create_job(file_id=live_id)
    assert await pack_staging.discard_if_unreferenced(session_factory, store, live, live_id) is False
    assert store.exists(live)
    import io
    moved = f"{pack_staging.PACK_FOLDER}/9/p.3mf"
    store.put(moved, io.BytesIO(b"pk"))
    assert await pack_staging.discard_if_unreferenced(session_factory, store, moved, None) is False
    assert store.exists(moved)


async def test_discard_finds_a_committed_row_whose_id_the_caller_never_learnt(session_factory, tmp_path):
    store = LocalDiskArtifactStore(tmp_path / "lib", prune_empty=True)
    key, file_id = await _staged(session_factory, store, "noid", row=True)
    assert await pack_staging.discard_if_unreferenced(session_factory, store, key, None) is True
    async with session_factory() as s:
        assert await s.get(UploadedFile, file_id) is None
    assert not store.exists(key)


async def test_a_requeue_that_lost_its_claim_discards_its_staged_pack(client, upload_3mf, create_printer, session_factory, tmp_path):
    """The lost-claim path catches the exception and keeps using its session after the guard's rollback."""
    from app.models import JobItemFailure, ProjectItem
    from app.services import print_results
    from tests.api.test_print_results import _interrupted_clear, _project_jobs
    pid, item, job_id, _, real = await _interrupted_clear(client, upload_3mf, create_printer, session_factory, tmp_path)

    async def other_repack_won(session, source, parts, **kwargs):
        async with session_factory() as other:
            row = (await other.execute(select(JobItemFailure).where(JobItemFailure.job_id == job_id))).scalar_one()
            row.requeue_pending = 0
            await other.commit()
        return await real(session, source, parts, **kwargs)

    with patch.object(print_results, "requeue_parts", side_effect=other_repack_won), \
         patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()):
        assert await print_results.resume_pending(session_factory) == [job_id]
    assert len(await _project_jobs(session_factory, pid)) == 1
    async with session_factory() as s:
        assert (await s.execute(select(UploadedFile).where(UploadedFile.relative_path.like("%/_tmp_%")))).scalars().all() == []
        assert (await s.get(ProjectItem, item)).quantity_held == 0
    assert _staged_on_disk(tmp_path / "library") == []


async def test_a_failed_slicer_send_repack_leaves_cleanup_to_its_own_transaction(client, upload_3mf, tmp_path, monkeypatch):
    """The slicer-send path is one atomic transaction (its row rolls back, `artifact_cleanup` removes the file): the guard
    must neither roll that session back mid-send nor run a side-session delete against it."""
    from uuid import uuid4
    discarded = []

    async def spy(*args, **kwargs):
        discarded.append(args)
        return False

    monkeypatch.setattr(pack_staging, "discard_if_unreferenced", spy)
    fid = await upload_3mf(filename="a.stl", data=b"solid a\nendsolid\n")
    body = {"send_id": str(uuid4()), "kind": "project", "project_name": "New", "group": {"targets": [{"target_key": "any"}]},
            "items": [{"file_id": fid, "quantity": 1}]}
    pid = (await client.post("/api/v1/slicer-sends", json=body)).json()["created"]["project_id"]
    constructor = FakeProjectConstructor()
    library = tmp_path / "library"
    with patch("app.config.get_library_dir", return_value=library), \
         patch("app.api.routes.projects.get_project_constructor", return_value=constructor):
        assert (await client.post(f"/api/v1/projects/{pid}/generate", json={})).status_code == 200
        from app.api.routes import project_generate

        async def fail_after_staging(*_a, **_k):        # the pack is staged and its row flushed: the guard's window
            raise RuntimeError("disk I/O error")

        monkeypatch.setattr(project_generate.model_targets, "materialize_job", fail_after_staging)
        body.update(update=True, items=[{"file_id": fid, "quantity": 2}])
        with pytest.raises(RuntimeError, match="disk I/O"):
            await client.post("/api/v1/slicer-sends", json=body)
    assert discarded == []
    assert _staged_on_disk(library) == []
