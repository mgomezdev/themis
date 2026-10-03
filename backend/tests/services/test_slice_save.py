"""Saving a production slice to the library as a cached version (BIZ-192)."""
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models import GcodeFile, Job, JobPrinterConfig, Printer, SlicedVersion, UploadedFile
from app.services import slice_cache
from app.services.queue_engine import QueueEngine
from app.services.slicer_service import SliceRequest
from tests.services.test_queue_engine import _install_fake_put, _make_mock_printer_manager
from tests.waiting import settle_background_tasks, wait_until

MACHINE = "Test Machine Preset"
GCODE = b"; filament used [g] = 12.5\n; estimated printing time (normal mode) = 1h 2m 3s\nG28\n"
FINGERPRINT = slice_cache.SlicerFingerprint("presethash", "2.3.1")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "Prints").mkdir(parents=True)
    (library / "Prints" / "Benchy.3mf").write_bytes(b"model-bytes")
    monkeypatch.setenv("THEMIS_LIBRARY_DIR", str(library))
    monkeypatch.setenv("THEMIS_DATA_DIR", str(tmp_path / "data"))
    with patch("app.services.slice_cache.current_fingerprint", return_value=FINGERPRINT):
        yield library


async def _seed(factory, *, save=True, name=None, content_hash="srchash", printer_type="elegoo_centauri",
                filament_type="PETG") -> int:
    async with factory() as s:
        if await s.get(Printer, 1) is None:
            s.add(Printer(id=1, name="P1", printer_type=printer_type, connection_config={},
                          current_orca_printer_profile=MACHINE,
                          loaded_filaments=[{"slot": 0, "type": "PETG", "color": "#000000",
                                             "filament_profile": "Generic PETG"}]))
            await s.flush()
        f = (await s.execute(select(UploadedFile).where(UploadedFile.relative_path == "Prints/Benchy.3mf"))).scalar()
        if f is None:
            f = UploadedFile(original_filename="Benchy.3mf", relative_path="Prints/Benchy.3mf", folder="/Prints",
                             content_hash=content_hash, plates=[{"plate_number": 1}], uploaded_at=_now())
            s.add(f)
            await s.flush()
        j = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status="queued", created_at=_now(),
                updated_at=_now(), save_slice=save, save_slice_name=name)
        s.add(j)
        await s.flush()
        s.add(JobPrinterConfig(job_id=j.id, printer_id=1, print_profile="0.20mm Standard",
                               filament_type=filament_type, filament_color="#000000"))
        await s.commit()
        return j.id


def _engine(factory, tmp_path, *, archive=False):
    mgr = _make_mock_printer_manager([1])
    client = mgr.get_client.return_value
    client.orca_export_args.side_effect = (lambda base: ["--export-3mf", f"{base}.gcode.3mf"]) if archive else (lambda base: [])
    slicer = MagicMock()
    slicer._data_dir = tmp_path / "data"

    def fake_slice(req: SliceRequest):
        out = tmp_path / "data" / "gcode" / str(req.job_id)
        out.mkdir(parents=True, exist_ok=True)
        path = out / (req.export_args[1] if req.export_args else f"out_j{req.job_id}.gcode")
        if archive:
            import zipfile
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("Metadata/plate_1.gcode", GCODE)
        else:
            path.write_bytes(GCODE)
        return str(path)

    slicer.slice.side_effect = fake_slice
    qe = QueueEngine(factory, mgr, slicer)
    _install_fake_put(qe)
    return qe, mgr


async def _run_to_printing(qe, factory, job_id):
    await qe._process_queue()

    async def _printing():
        async with factory() as s:
            return (await s.get(Job, job_id)).status == "printing"
    await wait_until(_printing, what="job to start printing")
    await settle_background_tasks()


async def _versions(factory):
    async with factory() as s:
        return (await s.execute(select(SlicedVersion).order_by(SlicedVersion.id))).scalars().all()


@pytest.mark.asyncio
async def test_a_flagged_job_saves_its_slice_next_to_the_model(session_factory, tmp_path, env, caplog):
    caplog.set_level(logging.INFO, logger="app.services.slice_cache")
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed(session_factory)

    await _run_to_printing(qe, session_factory, job_id)

    saved = env / "Prints" / "Benchy - PETG - 0.20mm Standard - Test Machine Preset.gcode"
    assert saved.read_bytes() == GCODE
    (version,) = await _versions(session_factory)
    async with session_factory() as s:
        f = await s.get(UploadedFile, version.file_id)
        job = await s.get(Job, job_id)
        gcode = (await s.execute(select(GcodeFile).where(GcodeFile.job_id == job_id))).scalar_one()
    assert (f.relative_path, f.folder, f.missing) == ("Prints/" + saved.name, "/Prints", False)
    assert f.content_hash and f.plates and f.plates[0]["estimated_time"] == 3723
    expected_inputs = slice_cache.CacheKeyInputs(
        source_content_hash="srchash", plate_number=1, machine_preset=MACHINE, process_preset="0.20mm Standard",
        filament_presets=("Generic PETG",), extra_config={}, tool_index=None, filament_map=None, artifact_kind="gcode")
    assert gcode.slice_inputs == expected_inputs.as_dict()
    assert version.cache_key == slice_cache.cache_key(expected_inputs)
    assert (version.source_content_hash, version.machine_preset, version.process_preset, version.filament_presets,
            version.artifact_kind, version.preset_content_hash, version.slicer_version, version.filament_type,
            version.estimated_seconds, version.filament_grams, version.created_from_job_id) == (
        "srchash", MACHINE, "0.20mm Standard", ["Generic PETG"], "gcode", "presethash", "2.3.1", "PETG",
        3723, 12.5, job_id)
    assert job.slice_cache_info["save"]["outcome"] == "saved"
    assert job.slice_cache_info["save"]["sliced_version_id"] == version.id
    lines = [r.getMessage() for r in caplog.records if "event=saved" in r.getMessage()]
    assert len(lines) == 1 and f"cache_key={version.cache_key}" in lines[0] and f"cached_file_hash={f.content_hash}" in lines[0]


@pytest.mark.asyncio
async def test_an_unflagged_job_saves_nothing_but_records_its_slice_inputs(session_factory, tmp_path, env):
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed(session_factory, save=False)

    await _run_to_printing(qe, session_factory, job_id)

    assert await _versions(session_factory) == []
    assert sorted(p.name for p in (env / "Prints").iterdir()) == ["Benchy.3mf"]
    async with session_factory() as s:
        gcode = (await s.execute(select(GcodeFile).where(GcodeFile.job_id == job_id))).scalar_one()
        assert gcode.slice_inputs["source_content_hash"] == "srchash"
        assert (await s.get(Job, job_id)).slice_cache_info is None


@pytest.mark.asyncio
async def test_the_display_name_is_the_filename_made_safe(session_factory, tmp_path, env):
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed(session_factory, name='  Benchy: "PETG" / v2  ')

    await _run_to_printing(qe, session_factory, job_id)

    assert (env / "Prints" / "Benchy PETG v2.gcode").read_bytes() == GCODE


@pytest.mark.asyncio
async def test_a_second_identical_slice_is_not_saved_twice(session_factory, tmp_path, env):
    qe, mgr = _engine(session_factory, tmp_path)
    first = await _seed(session_factory)
    await _run_to_printing(qe, session_factory, first)
    async with session_factory() as s:   # the printer finishes and is cleared for the next job
        (await s.get(Job, first)).status = "complete"
        await s.commit()
    second = await _seed(session_factory)

    await _run_to_printing(qe, session_factory, second)

    (version,) = await _versions(session_factory)
    async with session_factory() as s:
        info = (await s.get(Job, second)).slice_cache_info
    assert info["save"]["outcome"] == "duplicate" and info["save"]["sliced_version_id"] == version.id
    assert len([p for p in (env / "Prints").iterdir() if p.suffix == ".gcode"]) == 1


@pytest.mark.asyncio
async def test_a_failed_save_never_stops_the_print(session_factory, tmp_path, env, caplog):
    caplog.set_level(logging.WARNING, logger="app.services.slice_cache")
    qe, mgr = _engine(session_factory, tmp_path)
    job_id = await _seed(session_factory)

    with patch("app.services.slice_saver.shutil.copyfile", side_effect=OSError("disk full")):
        await _run_to_printing(qe, session_factory, job_id)

    mgr.get_client.return_value.start_print.assert_called_once()
    assert await _versions(session_factory) == []
    async with session_factory() as s:
        info = (await s.get(Job, job_id)).slice_cache_info
        files = (await s.execute(select(UploadedFile))).scalars().all()
    assert info["save"]["outcome"] == "failed" and "disk full" in info["save"]["error"]
    assert [f.relative_path for f in files] == ["Prints/Benchy.3mf"]
    assert any("event=save_failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_finishing_the_print_keeps_the_library_copy(session_factory, tmp_path, env):
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed(session_factory)
    await _run_to_printing(qe, session_factory, job_id)
    async with session_factory() as s:
        staged = (await s.execute(select(GcodeFile).where(GcodeFile.job_id == job_id))).scalar_one().path
        (await s.get(Job, job_id)).assigned_printer_id = 1
        await s.commit()

    await qe.handle_print_complete(1)
    await settle_background_tasks()

    assert not os.path.exists(staged)
    (version,) = await _versions(session_factory)
    async with session_factory() as s:
        rel = (await s.get(UploadedFile, version.file_id)).relative_path
    assert (env / rel).read_bytes() == GCODE


@pytest.mark.asyncio
async def test_a_bambu_slice_is_saved_as_a_sliced_archive(session_factory, tmp_path, env):
    qe, _ = _engine(session_factory, tmp_path, archive=True)
    job_id = await _seed(session_factory, printer_type="bambu")

    await _run_to_printing(qe, session_factory, job_id)

    (version,) = await _versions(session_factory)
    async with session_factory() as s:
        f = await s.get(UploadedFile, version.file_id)
    assert version.artifact_kind == "gcode_3mf"
    assert f.original_filename.endswith(".gcode.3mf")
    assert (version.estimated_seconds, version.filament_grams) == (3723, 12.5)


@pytest.mark.asyncio
async def test_a_source_without_a_content_hash_is_not_saved(session_factory, tmp_path, env):
    qe, mgr = _engine(session_factory, tmp_path)
    job_id = await _seed(session_factory, content_hash="")

    await _run_to_printing(qe, session_factory, job_id)

    assert await _versions(session_factory) == []
    async with session_factory() as s:
        info = (await s.get(Job, job_id)).slice_cache_info
    assert info["save"]["outcome"] == "failed" and "content hash" in info["save"]["error"]
    mgr.get_client.return_value.start_print.assert_called_once()


@pytest.mark.asyncio
async def test_a_save_that_fails_midway_leaves_no_half_registered_file(session_factory, tmp_path, env):
    """The copy landed and the file row was flushed when it failed: both are undone, not committed with the outcome."""
    qe, _ = _engine(session_factory, tmp_path)
    job_id = await _seed(session_factory)

    with patch("app.services.slice_saver.LibraryScanner._parse_plates", side_effect=RuntimeError("bad zip")):
        await _run_to_printing(qe, session_factory, job_id)

    async with session_factory() as s:
        files = (await s.execute(select(UploadedFile))).scalars().all()
        info = (await s.get(Job, job_id)).slice_cache_info
    assert [f.relative_path for f in files] == ["Prints/Benchy.3mf"]
    assert sorted(p.name for p in (env / "Prints").iterdir()) == ["Benchy.3mf"]
    assert info["save"]["outcome"] == "failed"
