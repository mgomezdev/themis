"""Slicing cache over the API (BIZ-191..193)."""
import hashlib
import os
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import GcodeFile, Job, JobPrinterConfig, SlicedVersion, UploadedFile
from app.services import slice_cache
from tests.conftest import make_sliced_archive


async def test_job_details_carry_the_cache_flags_and_the_last_decision(client, create_job, session_factory):
    job_id = await create_job()
    info = {"decision": "hit", "reason": None, "cache_key": "k" * 64, "stale": True, "stale_reasons": ["presets_changed"]}
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.save_slice, job.save_slice_name, job.allow_cached_slice = True, "Benchy PETG", True
        job.sliced_version_id, job.slice_cache_info = 4, info
        await s.commit()

    body = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()

    assert {k: body[k] for k in ("save_slice", "save_slice_name", "allow_cached_slice", "sliced_version_id",
                                 "slice_cache_info")} == {
        "save_slice": True, "save_slice_name": "Benchy PETG", "allow_cached_slice": True, "sliced_version_id": 4,
        "slice_cache_info": info}


async def test_a_new_job_defaults_to_no_saving_and_no_cache_reuse(client, create_job):
    body = (await client.get(f"/api/v1/jobs/{await create_job()}/details")).json()
    assert (body["save_slice"], body["save_slice_name"], body["allow_cached_slice"], body["sliced_version_id"],
            body["slice_cache_info"]) == (False, None, False, None, None)


async def test_sliced_version_ids_are_never_reused(client, create_job, session_factory):
    """jobs.sliced_version_id is a plain int, so a deleted version's id must never be handed to a newer, unrelated
    version (which the job would then silently print): sliced_versions is AUTOINCREMENT on both install paths."""
    from sqlalchemy import text
    from app.models import SlicedVersion, UploadedFile

    async def add_version(s) -> int:
        f = UploadedFile(original_filename="v.gcode", relative_path="v.gcode", folder="/", plates=[], uploaded_at="t")
        s.add(f)
        await s.flush()
        v = SlicedVersion(file_id=f.id, machine_preset="M", process_preset="P", filament_presets=["F"],
                          extra_config={}, artifact_kind="gcode", cache_key="k", created_at="t")
        s.add(v)
        await s.flush()
        return v.id

    async with session_factory() as s:
        first = await add_version(s)
        await s.commit()
    async with session_factory() as s:
        await s.delete(await s.get(SlicedVersion, first))
        await s.commit()
    async with session_factory() as s:
        assert await add_version(s) > first
        ddl = (await s.execute(text("SELECT sql FROM sqlite_master WHERE name='sliced_versions'"))).scalar_one()
    assert "AUTOINCREMENT" in ddl.upper()


# ---- saving (BIZ-192) ----------------------------------------------------------------------------------------------

GCODE = b"; filament used [g] = 2.0\n; estimated printing time (normal mode) = 5m 0s\nG28\n"


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("THEMIS_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("THEMIS_DATA_DIR", str(tmp_path / "data"))
    with patch("app.services.slice_cache.current_fingerprint", return_value=slice_cache.SlicerFingerprint("ph", "2.3.1")):
        yield tmp_path / "library"


async def _post_job(client, body):
    with patch("app.api.routes.jobs.queue_engine"):
        return await client.post("/api/v1/jobs", json=body)


async def test_create_job_with_save_slice_stores_the_flag_and_trimmed_name(client, library, upload_3mf, create_printer):
    resp = await _post_job(client, {
        "uploaded_file_id": await upload_3mf(), "save_slice": True, "save_slice_name": "  Benchy PETG  ",
        "printer_configs": [{"printer_id": await create_printer(), "print_profile": "0.20mm",
                             "filament_type": "any", "filament_color": "any"}],
    })
    assert resp.status_code == 201, resp.text
    details = (await client.get(f"/api/v1/jobs/{resp.json()['id']}/details")).json()
    assert (details["save_slice"], details["save_slice_name"]) == (True, "Benchy PETG")


async def test_save_slice_is_refused_for_a_pre_sliced_file(client, library, upload_3mf, create_printer):
    resp = await _post_job(client, {
        "uploaded_file_id": await upload_3mf("part.gcode.3mf", make_sliced_archive()), "save_slice": True,
        "printer_configs": [{"printer_id": await create_printer(), "filament_type": "any", "filament_color": "any"}],
    })
    assert resp.status_code == 422


async def test_flagging_a_queued_job_waits_for_its_slice(client, library, create_job, session_factory):
    job_id = await create_job()

    resp = await client.patch(f"/api/v1/jobs/{job_id}/save-slice", json={"save_slice": True, "name": "Keep me"})

    assert resp.status_code == 200, resp.text
    assert (resp.json()["save_slice"], resp.json()["save_slice_name"]) == (True, "Keep me")
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        assert (job.save_slice, job.save_slice_name) == (True, "Keep me")
        assert (await s.execute(select(SlicedVersion))).scalars().all() == []


async def _sliced_job(client, create_job, session_factory, tmp_path, *, inputs=True) -> tuple[int, str]:
    """A job whose slice already exists (status sliced, a GcodeFile row + the artifact on disk)."""
    job_id = await create_job()
    artifact = tmp_path / "data" / "gcode" / str(job_id) / "m_p1_j.gcode"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(GCODE)
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        source = await s.get(UploadedFile, job.uploaded_file_id)
        job.status = "sliced"
        slice_inputs = slice_cache.CacheKeyInputs(
            source_content_hash=source.content_hash, plate_number=1, machine_preset="Bambu Lab P1S 0.4",
            process_preset="0.20mm", filament_presets=("Generic PLA",), extra_config={}, tool_index=None,
            filament_map=None, artifact_kind="gcode")
        s.add(GcodeFile(job_id=job_id, printer_id=(await s.execute(select(JobPrinterConfig.printer_id).where(
            JobPrinterConfig.job_id == job_id))).scalar_one(), path=str(artifact),
            slice_inputs=slice_inputs.as_dict() if inputs else None))
        await s.commit()
    return job_id, slice_cache.cache_key(slice_inputs)


async def test_flagging_an_already_sliced_job_saves_it_now(client, library, create_job, session_factory, tmp_path):
    job_id, key = await _sliced_job(client, create_job, session_factory, tmp_path)

    resp = await client.patch(f"/api/v1/jobs/{job_id}/save-slice", json={"save_slice": True, "name": "Now"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["slice_cache_info"]["save"]["outcome"] == "saved"
    async with session_factory() as s:
        (version,) = (await s.execute(select(SlicedVersion))).scalars().all()
        f = await s.get(UploadedFile, version.file_id)
    assert version.cache_key == key and version.created_from_job_id == job_id
    assert f.original_filename == "Now.gcode"
    assert (library / f.relative_path).read_bytes() == GCODE
    assert f.content_hash == hashlib.sha256(GCODE).hexdigest()


async def test_flagging_a_slice_made_before_inputs_were_recorded_explains_why_it_cant_be_saved(
        client, library, create_job, session_factory, tmp_path):
    job_id, _ = await _sliced_job(client, create_job, session_factory, tmp_path, inputs=False)

    resp = await client.patch(f"/api/v1/jobs/{job_id}/save-slice", json={"save_slice": True})

    save = resp.json()["slice_cache_info"]["save"]
    assert save["outcome"] == "failed" and "inputs were recorded" in save["error"]


async def test_a_flag_that_was_already_on_does_not_save_again(client, library, create_job, session_factory, tmp_path):
    job_id, _ = await _sliced_job(client, create_job, session_factory, tmp_path)
    async with session_factory() as s:
        (await s.get(Job, job_id)).save_slice = True   # the engine owns this save
        await s.commit()

    await client.patch(f"/api/v1/jobs/{job_id}/save-slice", json={"save_slice": True, "name": "renamed"})

    async with session_factory() as s:
        assert (await s.execute(select(SlicedVersion))).scalars().all() == []
        assert (await s.get(Job, job_id)).save_slice_name == "renamed"


async def test_turning_the_flag_off_clears_the_name(client, library, create_job, session_factory):
    job_id = await create_job()
    await client.patch(f"/api/v1/jobs/{job_id}/save-slice", json={"save_slice": True, "name": "x"})

    resp = await client.patch(f"/api/v1/jobs/{job_id}/save-slice", json={"save_slice": False, "name": "ignored"})

    assert (resp.json()["save_slice"], resp.json()["save_slice_name"]) == (False, None)


@pytest.mark.parametrize("status", ["complete", "failed", "cancelled"])
async def test_a_finished_job_cannot_be_flagged(client, library, create_job, session_factory, status):
    job_id = await create_job()
    async with session_factory() as s:
        (await s.get(Job, job_id)).status = status
        await s.commit()
    resp = await client.patch(f"/api/v1/jobs/{job_id}/save-slice", json={"save_slice": True})
    assert resp.status_code == 409
    async with session_factory() as s:
        assert (await s.get(Job, job_id)).save_slice is False


async def test_a_pre_sliced_job_cannot_be_flagged(client, library, upload_3mf, create_printer):
    job_id = (await _post_job(client, {
        "uploaded_file_id": await upload_3mf("part.gcode.3mf", make_sliced_archive()),
        "printer_configs": [{"printer_id": await create_printer(), "filament_type": "any", "filament_color": "any"}],
    })).json()["id"]
    resp = await client.patch(f"/api/v1/jobs/{job_id}/save-slice", json={"save_slice": True})
    assert resp.status_code == 422


async def test_generate_can_flag_every_job_and_gives_the_pack_a_real_hash(client, tmp_path, session_factory):
    from tests.api.test_projects_api import _make_3mf_bytes, _setup_project_with_stl
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    lib = tmp_path / "library"
    packed = _make_3mf_bytes(plate_count=2)
    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_library_dir", return_value=lib),
        patch("app.api.routes.projects.get_laminus_sidecar_url", return_value="http://fake-sidecar"),
        patch("app.api.routes.projects.LaminusSidecarClient") as mock_cls,
        patch("app.api.routes.projects.regen_file_thumbnails", new_callable=AsyncMock),
    ):
        mock_cls.return_value.pack_stls.return_value = packed
        resp = await client.post(f"/api/v1/projects/{project_id}/generate",
                                 json={"eligible_printer_ids": [], "save_slice": True})

    assert resp.status_code == 200, resp.text
    job_ids = [j["id"] for j in resp.json()["jobs"]]
    async with session_factory() as s:
        jobs = [await s.get(Job, i) for i in job_ids]
        pack = await s.get(UploadedFile, jobs[0].uploaded_file_id)
    assert len(jobs) == 2 and all(j.save_slice for j in jobs)
    assert pack.content_hash == hashlib.sha256(packed).hexdigest()


async def test_resending_the_flag_retries_a_failed_save(client, library, create_job, session_factory, tmp_path):
    job_id, _ = await _sliced_job(client, create_job, session_factory, tmp_path)
    with patch("app.services.slice_saver.shutil.copyfileobj", side_effect=OSError("disk full")):
        first = await client.patch(f"/api/v1/jobs/{job_id}/save-slice", json={"save_slice": True})
    assert first.json()["slice_cache_info"]["save"]["outcome"] == "failed"

    retry = await client.patch(f"/api/v1/jobs/{job_id}/save-slice", json={"save_slice": True})

    assert retry.json()["slice_cache_info"]["save"]["outcome"] == "saved"
    async with session_factory() as s:
        assert len((await s.execute(select(SlicedVersion))).scalars().all()) == 1


async def test_flagging_a_job_whose_slice_is_already_gone_just_sets_the_flag(
        client, library, create_job, session_factory, tmp_path):
    job_id, _ = await _sliced_job(client, create_job, session_factory, tmp_path)
    async with session_factory() as s:
        gcode = (await s.execute(select(GcodeFile).where(GcodeFile.job_id == job_id))).scalar_one()
    os.remove(gcode.path)

    resp = await client.patch(f"/api/v1/jobs/{job_id}/save-slice", json={"save_slice": True})

    assert resp.status_code == 200 and resp.json()["save_slice"] is True
    assert resp.json()["slice_cache_info"] is None
