"""GH #269: a pack staged under `_tmp_` whose jobs never commit is discarded; one that jobs reference is kept."""
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import Job, UploadedFile
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
    calls = []

    async def fail_on_the_second_pack(session, job_id):
        calls.append(job_id)
        if len(calls) == 2:                     # each pack has one job: this is the second pack's, before its commit
            raise RuntimeError("boom")
        return await real(session, job_id)

    monkeypatch.setattr(project_generate.model_targets, "materialize_job", fail_on_the_second_pack)
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=FakeProjectConstructor()), \
         pytest.raises(RuntimeError, match="boom"):
        await client.post(f"/api/v1/projects/{pid}/generate", json={"eligible_printer_ids": [printer], "process_preset": "0.20mm"})
    assert len(calls) == 2
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
    import io
    store.put(f"{pack_staging.PACK_FOLDER}/7/done.3mf", io.BytesIO(b"pk"))           # a moved pack: not staged

    assert await pack_staging.sweep(session_factory, store) == 2
    assert not store.exists(orphan_file) and not store.exists(orphan_row)
    assert store.exists(live) and store.exists(f"{pack_staging.PACK_FOLDER}/7/done.3mf")
    async with session_factory() as s:
        assert await s.get(UploadedFile, orphan_id) is None
        assert await s.get(UploadedFile, live_id) is not None
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
