"""Printing a cached version instead of slicing, when a printer claims the job (BIZ-193)."""
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import GcodeFile, Job, QueueConfig, SlicedVersion, UploadedFile
from app.services import slice_cache
from tests.services.test_slice_save import (  # noqa: F401 — `env` is a fixture
    FINGERPRINT, GCODE, MACHINE, _engine, _run_to_printing, _seed, env,
)

CACHED = b"; cached\n; filament used [g] = 3.0\n; estimated printing time (normal mode) = 10m 0s\nG28\n"
INPUTS = slice_cache.CacheKeyInputs(
    source_content_hash="srchash", plate_number=1, machine_preset=MACHINE, process_preset="0.20mm Standard",
    filament_presets=("Generic PETG",), extra_config={}, tool_index=None, filament_map=None, artifact_kind="gcode")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _add_version(factory, library: Path, *, inputs=INPUTS, preset_hash="presethash", version="2.3.1",
                       name="Benchy cached.gcode", on_disk=True) -> int:
    if on_disk:
        (library / "Prints" / name).write_bytes(CACHED)
    async with factory() as s:
        source = (await s.execute(select(UploadedFile).where(UploadedFile.relative_path == "Prints/Benchy.3mf"))).scalar()
        if source is None:   # the same row _seed reuses
            st = (library / "Prints" / "Benchy.3mf").stat()
            source = UploadedFile(original_filename="Benchy.3mf", relative_path="Prints/Benchy.3mf", folder="/Prints",
                                  content_hash="srchash", plates=[{"plate_number": 1}], uploaded_at=_now(),
                                  size_bytes=st.st_size, mtime=st.st_mtime)
            s.add(source)
            await s.flush()
        disk = library / "Prints" / name
        st = disk.stat() if on_disk else None
        f = UploadedFile(original_filename=name, relative_path=f"Prints/{name}", folder="/Prints",
                         content_hash="cachedhash", plates=[{"plate_number": 1}], uploaded_at=_now(),
                         size_bytes=st.st_size if st else 0, mtime=st.st_mtime if st else 0.0)
        s.add(f)
        await s.flush()
        v = SlicedVersion(file_id=f.id, source_file_id=source.id, source_content_hash=inputs.source_content_hash,
                          plate_number=1, machine_preset=inputs.machine_preset, process_preset=inputs.process_preset,
                          filament_presets=list(inputs.filament_presets), extra_config=inputs.extra_config,
                          artifact_kind=inputs.artifact_kind, cache_key=slice_cache.cache_key(inputs),
                          preset_content_hash=preset_hash, slicer_version=version, created_at=_now())
        s.add(v)
        await s.commit()
        return v.id


async def _seed_allowing(factory, **kw) -> int:
    job_id = await _seed(factory, save=kw.pop("save", False), **kw)
    async with factory() as s:
        (await s.get(Job, job_id)).allow_cached_slice = True
        await s.commit()
    return job_id


async def _set_policy(factory, use_latest: bool):
    async with factory() as s:
        s.add(QueueConfig(id=1, check_interval_minutes=5, snapshot_interval_seconds=2, estimates_enabled=False,
                          slice_cache_use_latest_settings=use_latest))
        await s.commit()


async def _job(factory, job_id) -> Job:
    async with factory() as s:
        return await s.get(Job, job_id)


def _cache_lines(caplog, event):
    return [r for r in caplog.records if f"slice_cache event={event}" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_matching_version_is_printed_and_slicing_skipped(session_factory, tmp_path, env, caplog):
    caplog.set_level(logging.INFO, logger="app.services.slice_cache")
    version_id = await _add_version(session_factory, env)
    qe, mgr = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_not_called()
    job = await _job(session_factory, job_id)
    assert job.sliced_version_id == version_id
    assert (job.actual_filament_grams, job.actual_seconds) == (3.0, 600)
    info = job.slice_cache_info
    assert (info["decision"], info["sliced_version_id"], info["cached_file_hash"], info["cache_key"],
            info["stale"], info["policy"]) == ("hit", version_id, "cachedhash", slice_cache.cache_key(INPUTS),
                                               False, "use_latest")
    async with session_factory() as s:
        staged = (await s.execute(select(GcodeFile).where(GcodeFile.job_id == job_id))).scalar_one().path
    assert Path(staged).read_bytes() == CACHED and Path(staged) != env / "Prints" / "Benchy cached.gcode"
    assert mgr.get_client.return_value.start_print.call_args.args[0] == f"Benchy_cached_j{job_id}.gcode"
    (line,) = _cache_lines(caplog, "hit_slice_skipped")
    assert line.levelno == logging.INFO
    assert f"cache_key={slice_cache.cache_key(INPUTS)}" in line.getMessage()
    assert "cached_file_hash=cachedhash" in line.getMessage() and f"sliced_version_id={version_id}" in line.getMessage()


@pytest.mark.asyncio
async def test_a_job_that_does_not_allow_the_cache_slices(session_factory, tmp_path, env):
    await _add_version(session_factory, env)
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed(session_factory, save=False)

    await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_called_once()
    job = await _job(session_factory, job_id)
    assert (job.sliced_version_id, job.slice_cache_info) == (None, None)


@pytest.mark.asyncio
async def test_no_matching_version_slices_and_says_why(session_factory, tmp_path, env, caplog):
    caplog.set_level(logging.INFO, logger="app.services.slice_cache")
    other = slice_cache.CacheKeyInputs(**{**INPUTS.as_dict(), "filament_presets": ("Generic PLA",)})
    await _add_version(session_factory, env, inputs=other)   # a PLA version: not this PETG slice
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_called_once()
    info = (await _job(session_factory, job_id)).slice_cache_info
    assert (info["decision"], info["reason"]) == ("miss", "no_version")
    assert "reason=no_version" in _cache_lines(caplog, "miss")[0].getMessage()


@pytest.mark.asyncio
async def test_a_version_whose_file_is_gone_is_a_miss(session_factory, tmp_path, env):
    await _add_version(session_factory, env, on_disk=False)
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_called_once()
    assert (await _job(session_factory, job_id)).slice_cache_info["reason"] == "file_missing"


@pytest.mark.asyncio
async def test_a_stale_version_is_resliced_when_using_the_latest_settings(session_factory, tmp_path, env, caplog):
    caplog.set_level(logging.INFO, logger="app.services.slice_cache")
    await _add_version(session_factory, env, preset_hash="OLD")
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_called_once()
    info = (await _job(session_factory, job_id)).slice_cache_info
    assert (info["decision"], info["reason"], info["stale"], info["stale_reasons"], info["policy"]) == (
        "miss", "stale_resliced", True, ["presets_changed"], "use_latest")
    assert (info["preset_content_hash_stored"], info["preset_content_hash_current"]) == ("OLD", "presethash")


@pytest.mark.asyncio
async def test_a_stale_version_still_prints_when_pinned_and_is_flagged(session_factory, tmp_path, env, caplog):
    caplog.set_level(logging.INFO, logger="app.services.slice_cache")
    await _set_policy(session_factory, use_latest=False)
    version_id = await _add_version(session_factory, env, version="2.2.0")
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_not_called()
    job = await _job(session_factory, job_id)
    assert job.sliced_version_id == version_id
    assert (job.slice_cache_info["stale"], job.slice_cache_info["stale_reasons"], job.slice_cache_info["policy"]) == (
        True, ["slicer_version_changed"], "pin_cached")
    (line,) = _cache_lines(caplog, "hit_slice_skipped")
    assert line.levelno == logging.WARNING and "stale=true" in line.getMessage() and "policy=pin_cached" in line.getMessage()


@pytest.mark.parametrize("use_latest", [True, False])
@pytest.mark.asyncio
async def test_unknown_staleness_still_uses_the_version(session_factory, tmp_path, env, use_latest):
    await _set_policy(session_factory, use_latest=use_latest)
    version_id = await _add_version(session_factory, env, preset_hash="OLD")
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    with patch("app.services.slice_cache.current_fingerprint", return_value=slice_cache.SlicerFingerprint(None, None)):
        await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_not_called()
    job = await _job(session_factory, job_id)
    assert job.sliced_version_id == version_id and job.slice_cache_info["stale"] is None


@pytest.mark.asyncio
async def test_a_hit_is_not_saved_again(session_factory, tmp_path, env):
    await _add_version(session_factory, env)
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory, save=True)

    await _run_to_printing(qe, session_factory, job_id)

    async with session_factory() as s:
        assert len((await s.execute(select(SlicedVersion))).scalars().all()) == 1
    assert "save" not in (await _job(session_factory, job_id)).slice_cache_info


@pytest.mark.asyncio
async def test_reslicing_a_stale_version_saves_a_fresh_one_that_wins_next_time(session_factory, tmp_path, env):
    stale_id = await _add_version(session_factory, env, preset_hash="OLD")
    qe, _ = _engine(session_factory, tmp_path)
    first = await _seed_allowing(session_factory, save=True)
    await _run_to_printing(qe, session_factory, first)
    async with session_factory() as s:
        fresh = (await s.execute(select(SlicedVersion).where(SlicedVersion.id != stale_id))).scalar_one()
        (await s.get(Job, first)).status = "complete"
        await s.commit()
    assert fresh.cache_key == slice_cache.cache_key(INPUTS) and fresh.preset_content_hash == "presethash"

    second = await _seed_allowing(session_factory)
    await _run_to_printing(qe, session_factory, second)

    assert (await _job(session_factory, second)).sliced_version_id == fresh.id
    assert qe._slicer.slice.call_count == 1   # only the first job sliced


@pytest.mark.asyncio
async def test_an_uncacheable_source_slices(session_factory, tmp_path, env):
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory, content_hash="")
    (env / "Prints" / "Benchy.3mf").unlink()   # nothing on disk to hash either

    await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_called_once()
    assert (await _job(session_factory, job_id)).slice_cache_info["reason"] == "uncacheable"


@pytest.mark.asyncio
async def test_a_lookup_error_never_costs_the_print(session_factory, tmp_path, env):
    await _add_version(session_factory, env)
    qe, mgr = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    with patch("app.services.slice_cache.staleness", side_effect=RuntimeError("boom")):
        await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_called_once()
    mgr.get_client.return_value.start_print.assert_called_once()
    assert (await _job(session_factory, job_id)).slice_cache_info["reason"] == "lookup_error"


# ---- review follow-ups -------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_model_edited_in_place_since_the_last_rescan_misses(session_factory, tmp_path, env):
    """The index still has the old hash; the engine re-hashes the changed file, so the old version no longer matches."""
    await _add_version(session_factory, env)
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)
    (env / "Prints" / "Benchy.3mf").write_bytes(b"re-exported from CAD with different geometry")

    await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_called_once()
    async with session_factory() as s:
        source = (await s.execute(select(UploadedFile).where(UploadedFile.relative_path == "Prints/Benchy.3mf"))).scalar_one()
    assert source.content_hash not in ("srchash", "")
    assert (await _job(session_factory, job_id)).slice_cache_info["reason"] == "no_version"


@pytest.mark.asyncio
async def test_a_cached_file_edited_in_place_is_not_printed(session_factory, tmp_path, env):
    await _add_version(session_factory, env)
    (env / "Prints" / "Benchy cached.gcode").write_bytes(b"; hand-edited\nG28\n")
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_called_once()
    assert (await _job(session_factory, job_id)).slice_cache_info["reason"] == "file_missing"


@pytest.mark.asyncio
async def test_the_newest_unusable_version_falls_back_to_an_older_good_one(session_factory, tmp_path, env):
    older = await _add_version(session_factory, env, name="older.gcode")
    await _add_version(session_factory, env, name="newer.gcode", on_disk=False)
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed_allowing(session_factory)

    await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_not_called()
    assert (await _job(session_factory, job_id)).sliced_version_id == older


@pytest.mark.asyncio
async def test_a_version_of_the_other_artifact_kind_is_a_miss(session_factory, tmp_path, env):
    """A Bambu printer wants a .gcode.3mf: the raw-gcode version of the same slice isn't its cache entry."""
    await _add_version(session_factory, env)   # artifact_kind gcode
    qe, _ = _engine(session_factory, tmp_path, archive=True)
    job_id = await _seed_allowing(session_factory, printer_type="bambu")

    await _run_to_printing(qe, session_factory, job_id)

    qe._slicer.slice.assert_called_once()
    assert (await _job(session_factory, job_id)).slice_cache_info["reason"] == "no_version"


@pytest.mark.parametrize("allow", [True, False])
@pytest.mark.asyncio
async def test_a_later_miss_clears_an_earlier_hit(session_factory, tmp_path, env, allow):
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed(session_factory, save=False)
    async with session_factory() as s:
        job = await s.get(Job, job_id)
        job.allow_cached_slice, job.sliced_version_id = allow, 99   # an earlier dispatch printed version 99
        await s.commit()

    await _run_to_printing(qe, session_factory, job_id)

    assert (await _job(session_factory, job_id)).sliced_version_id is None


@pytest.mark.asyncio
async def test_a_hand_picked_version_blocks_on_a_printer_whose_profile_changed(session_factory, tmp_path, env):
    from app.models import JobPrinterConfig, Printer
    await _add_version(session_factory, env)
    qe, mgr = _engine(session_factory, tmp_path)
    async with session_factory() as s:
        s.add(Printer(id=1, name="P1", printer_type="elegoo_centauri", connection_config={},
                      current_orca_printer_profile="Some Other 0.6 nozzle"))
        cached = (await s.execute(select(UploadedFile).where(UploadedFile.original_filename == "Benchy cached.gcode"))).scalar_one()
        j = Job(uploaded_file_id=cached.id, plate_number=1, queue_position=1.0, status="queued",
                created_at=_now(), updated_at=_now())
        s.add(j)
        await s.flush()
        s.add(JobPrinterConfig(job_id=j.id, printer_id=1, print_profile="", filament_type="any", filament_color="any"))
        await s.commit()
        job_id = j.id

    await qe._process_queue()

    job = await _job(session_factory, job_id)
    assert job.status == "blocked" and "no longer a" in job.block_reason
    mgr.get_client.return_value.start_print.assert_not_called()
