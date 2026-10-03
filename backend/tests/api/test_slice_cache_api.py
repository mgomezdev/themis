"""Slicing cache over the API (BIZ-191..193)."""
from app.models import Job


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
