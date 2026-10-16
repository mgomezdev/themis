"""The slice / stage / save pipeline reads and writes library bytes only through the ArtifactStore (BIZ-287).

Each test swaps in the in-memory store (no local library directory exists at all), so any leftover path access to the
library fails: the store is the only place the model, the cached slice and the saved copy can be.
"""
import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models import Job, JobPrinterConfig, Printer, SlicedVersion, UploadedFile
from app.services import slice_cache
from app.services.artifact_store import ArtifactExists, LocalDiskArtifactStore
from app.services.library_store import put_file_unique
from app.services.queue_engine import QueueEngine
from app.services.slicer_service import SliceError, SlicerService, SliceRequest
from tests.fake_artifact_store import InMemoryArtifactStore
from tests.services.test_queue_engine import _install_fake_put, _make_mock_printer_manager
from tests.waiting import wait_until

GCODE = b"; filament used [g] = 12.5\n; estimated printing time (normal mode) = 1h 2m 3s\nG28\n"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- put_file_unique: the caller-side naming policy over the store's exclusive create -------------------------------

def test_put_file_unique_takes_the_next_free_name_and_never_overwrites(tmp_path):
    store = LocalDiskArtifactStore(tmp_path / "lib")
    src = tmp_path / "src.gcode"
    src.write_bytes(GCODE)
    store.put("Prints/x.gcode", io.BytesIO(b"older"))
    first = put_file_unique(store, src, "Prints", "x.gcode")
    second = put_file_unique(store, src, "Prints", "x.gcode")
    assert (first.key, second.key) == ("Prints/x (2).gcode", "Prints/x (3).gcode")
    assert store.open("Prints/x.gcode").read() == b"older"
    assert first.sha256 == hashlib.sha256(GCODE).hexdigest() and first.size == len(GCODE)


def test_put_file_unique_keeps_a_sliced_archive_suffix_whole(tmp_path):
    store = LocalDiskArtifactStore(tmp_path / "lib")
    src = tmp_path / "a.bin"
    src.write_bytes(b"z")
    put_file_unique(store, src, "", "x.gcode.3mf")
    assert put_file_unique(store, src, "", "x.gcode.3mf").key == "x (2).gcode.3mf"


def test_put_file_unique_loses_a_race_to_the_exclusive_create_and_retries(tmp_path):
    """Two saves can both see a name free; the store's exclusive create makes the loser take the next one."""
    store = LocalDiskArtifactStore(tmp_path / "lib")
    src = tmp_path / "src.gcode"
    src.write_bytes(GCODE)
    real_put, attempts = store.put_file, []

    def put_after_another_writer_took_the_name(key, path, **kw):
        attempts.append(key)
        if len(attempts) == 1:
            store.put("Prints/x.gcode", io.BytesIO(b"the other save"))   # the racing save lands first
        return real_put(key, path, **kw)

    store.put_file = put_after_another_writer_took_the_name
    info = put_file_unique(store, src, "Prints", "x.gcode")
    assert attempts == ["Prints/x.gcode", "Prints/x (2).gcode"] and info.key == "Prints/x (2).gcode"
    assert store.open("Prints/x.gcode").read() == b"the other save"
    with pytest.raises(ArtifactExists):
        real_put("Prints/x.gcode", src)


# ---- slice_saver on a store with no local library -----------------------------------------------------------------

@pytest.fixture
def stores(tmp_path, monkeypatch):
    library, filecache = InMemoryArtifactStore(), InMemoryArtifactStore()
    monkeypatch.setattr("app.services.slice_saver.get_library_store", lambda: library)
    monkeypatch.setattr("app.services.slice_saver.get_filecache_store", lambda: filecache)
    monkeypatch.setenv("THEMIS_LIBRARY_DIR", str(tmp_path / "no-such-library"))
    monkeypatch.setenv("THEMIS_DATA_DIR", str(tmp_path / "data"))
    library.put("Prints/Benchy.3mf", io.BytesIO(b"model"))
    with patch("app.services.slice_cache.current_fingerprint",
               return_value=slice_cache.SlicerFingerprint("presethash", "2.3.1")):
        yield library, filecache


async def _seed(factory) -> int:
    async with factory() as s:
        s.add(Printer(id=1, name="P1", printer_type="elegoo_centauri", connection_config={},
                      machine_profile_override="M"))
        f = UploadedFile(original_filename="Benchy.3mf", relative_path="Prints/Benchy.3mf", folder="/Prints",
                         content_hash="srchash", plates=[{"plate_number": 1}], uploaded_at=_now(), size_bytes=5, mtime=1.0)
        s.add(f)
        await s.flush()
        j = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status="queued", created_at=_now(),
                updated_at=_now(), save_slice=True)
        s.add(j)
        await s.flush()
        s.add(JobPrinterConfig(job_id=j.id, printer_id=1, print_profile="P", filament_type="PETG", filament_color="#000000"))
        await s.commit()
        return j.id


def _inputs():
    return slice_cache.CacheKeyInputs(
        source_content_hash="srchash", plate_number=1, machine_preset="M", process_preset="P",
        filament_presets=("F",), extra_config={}, tool_index=None, filament_map=None, artifact_kind="gcode")


async def _save(factory, tmp_path, job_id, name="out.gcode"):
    from app.services import slice_saver
    gcode = tmp_path / name
    gcode.write_bytes(GCODE)
    async with factory() as s:
        return await slice_saver.save_slice_version(
            s, job_id=job_id, printer_id=1, artifact_path=str(gcode), inputs=_inputs())


@pytest.mark.asyncio
async def test_a_saved_slice_is_published_to_the_store_with_its_streamed_hash(session_factory, tmp_path, stores):
    library, _ = stores
    job_id = await _seed(session_factory)

    version = await _save(session_factory, tmp_path, job_id)

    assert version is not None
    async with session_factory() as s:
        row = await s.get(UploadedFile, version.file_id)
    assert row.relative_path.startswith("Prints/") and row.relative_path.endswith(".gcode")
    assert library.open(row.relative_path).read() == GCODE
    assert (row.content_hash, row.size_bytes) == (hashlib.sha256(GCODE).hexdigest(), len(GCODE))
    assert row.mtime == library.stat(row.relative_path).mtime and row.folder == "/Prints"
    assert row.original_filename == row.relative_path.rpartition("/")[2]
    assert not list((tmp_path / "no-such-library").rglob("*.gcode"))   # nothing reached a local library


@pytest.mark.asyncio
async def test_a_second_save_under_the_same_name_takes_a_numbered_key(session_factory, tmp_path, stores):
    library, _ = stores
    job_id = await _seed(session_factory)
    with patch("app.services.slice_saver.safe_display_name", return_value="same"):
        first = await _save(session_factory, tmp_path, job_id)
        # a different cache key so it is not a duplicate, but the same display name
        with patch("app.services.slice_saver.slice_cache.cache_key", return_value="otherkey"):
            second = await _save(session_factory, tmp_path, job_id, "out2.gcode")
    assert first and second
    assert sorted(k.key for k in library.list("Prints")) == [
        "Prints/Benchy.3mf", "Prints/same (2).gcode", "Prints/same.gcode"]


@pytest.mark.asyncio
async def test_a_duplicate_save_removes_its_copy_from_the_store(session_factory, tmp_path, stores):
    library, _ = stores
    job_id = await _seed(session_factory)
    first = await _save(session_factory, tmp_path, job_id)
    before = sorted(k.key for k in library.list())

    again = await _save(session_factory, tmp_path, job_id, "out2.gcode")

    assert again is not None and again.id == first.id
    assert sorted(k.key for k in library.list()) == before   # the second copy was deleted again


@pytest.mark.asyncio
async def test_a_failed_save_removes_the_copy_and_its_thumbnails_from_the_stores(session_factory, tmp_path, stores):
    library, filecache = stores
    job_id = await _seed(session_factory)

    def publish_a_thumbnail_then_fail(self, key, file_id):
        filecache.put(f"{file_id}/thumbnails/plate_1.png", io.BytesIO(b"png"))
        raise RuntimeError("parse blew up")

    with patch("app.services.slice_saver.LibraryScanner.parse_plates", publish_a_thumbnail_then_fail):
        assert await _save(session_factory, tmp_path, job_id) is None

    assert [k.key for k in library.list()] == ["Prints/Benchy.3mf"]
    assert list(filecache.list()) == []
    async with session_factory() as s:
        assert (await s.execute(select(SlicedVersion))).first() is None
        assert [f.relative_path for f in (await s.execute(select(UploadedFile))).scalars().all()] == ["Prints/Benchy.3mf"]


@pytest.mark.asyncio
async def test_a_cleanup_failure_is_logged_and_does_not_mask_the_save_outcome(session_factory, tmp_path, stores, caplog):
    library, _ = stores
    job_id = await _seed(session_factory)
    library.fail_delete = True
    with patch("app.services.slice_saver.LibraryScanner.parse_plates", side_effect=RuntimeError("parse blew up")):
        assert await _save(session_factory, tmp_path, job_id) is None
    async with session_factory() as s:
        info = (await s.get(Job, job_id)).slice_cache_info
    assert info["save"]["outcome"] == "failed" and "parse blew up" in info["save"]["error"]
    assert any("could not remove the unregistered slice copy" in r.getMessage() for r in caplog.records)


# ---- staging pre-sliced / cached gcode from the store --------------------------------------------------------------

def _engine(factory, tmp_path):
    slicer = MagicMock()
    slicer._data_dir = tmp_path / "data"
    return QueueEngine(factory, _make_mock_printer_manager([1]), slicer), slicer


def test_staging_copies_the_stored_bytes_into_the_jobs_local_dir(tmp_path, monkeypatch):
    library = InMemoryArtifactStore()
    library.put("Job Uploads/part.gcode.3mf", io.BytesIO(b"archive-bytes"))
    monkeypatch.setattr("app.services.queue_engine.get_library_store", lambda: library)
    qe, _ = _engine(MagicMock(), tmp_path)

    staged = Path(qe._stage_gcode(7, "Job Uploads/part.gcode.3mf", "My Part.gcode.3mf"))

    assert staged == tmp_path / "data" / "gcode" / "7" / "My_Part_j7.gcode.3mf"
    assert staged.read_bytes() == b"archive-bytes"


def test_staging_a_missing_artifact_is_an_oserror_and_leaves_no_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.queue_engine.get_library_store", lambda: InMemoryArtifactStore())
    qe, _ = _engine(MagicMock(), tmp_path)
    with pytest.raises(OSError, match="gone.gcode"):
        qe._stage_gcode(7, "gone.gcode", "gone.gcode")
    assert list((tmp_path / "data" / "gcode" / "7").iterdir()) == []


def test_staging_a_stream_that_fails_midway_removes_the_partial_file(tmp_path, monkeypatch):
    from app.services.artifact_store import ArtifactStoreError
    library = InMemoryArtifactStore()
    library.put("part.gcode", io.BytesIO(b"x" * 10))

    class Dying(io.BytesIO):
        def read(self, n=-1):
            raise ArtifactStoreError("link dropped")

    library.open = lambda key: Dying(b"x")
    monkeypatch.setattr("app.services.queue_engine.get_library_store", lambda: library)
    qe, _ = _engine(MagicMock(), tmp_path)
    with pytest.raises(OSError, match="link dropped"):
        qe._stage_gcode(7, "part.gcode", "part.gcode")
    assert list((tmp_path / "data" / "gcode" / "7").iterdir()) == []


@pytest.mark.asyncio
async def test_a_presliced_job_prints_from_a_store_with_no_local_library(session_factory, tmp_path, monkeypatch):
    library = InMemoryArtifactStore()
    library.put("part.gcode", io.BytesIO(GCODE))
    monkeypatch.setattr("app.services.queue_engine.get_library_store", lambda: library)
    monkeypatch.setenv("THEMIS_LIBRARY_DIR", str(tmp_path / "no-such-library"))
    qe, slicer = _engine(session_factory, tmp_path)
    _install_fake_put(qe)
    async with session_factory() as s:
        s.add(Printer(id=1, name="P1", printer_type="elegoo_centauri", connection_config={}))
        f = UploadedFile(original_filename="part.gcode", relative_path="part.gcode", folder="/", plates=[], uploaded_at=_now())
        s.add(f)
        await s.flush()
        j = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status="queued", eligibility_confirmed=True,
                created_at=_now(), updated_at=_now())
        s.add(j)
        await s.flush()
        s.add(JobPrinterConfig(job_id=j.id, printer_id=1, print_profile="", filament_type="any", filament_color="any"))
        await s.commit()
        job_id = j.id

    await qe._process_queue()

    async def _printing():
        async with session_factory() as s:
            return (await s.get(Job, job_id)).status == "printing"
    await wait_until(_printing, what="the gcode job to start printing")
    slicer.slice.assert_not_called()
    staged = next((tmp_path / "data" / "gcode" / str(job_id)).iterdir())
    assert staged.read_bytes() == GCODE


# ---- slicing input: the library artifact is materialized for the slice ---------------------------------------------

def _slice_request(key, **kw):
    return SliceRequest(job_id=1, source_3mf="", source_key=key, plate_number=1, machine_preset="M",
                        process_preset="P", filament_presets=["F"], **kw)


def test_slicing_materializes_the_library_artifact_for_the_whole_slice(tmp_path, monkeypatch):
    library = InMemoryArtifactStore()
    library.put("Prints/Benchy.3mf", io.BytesIO(b"model-bytes"))
    monkeypatch.setattr("app.services.slicer_service.get_library_store", lambda: library)
    seen = {}

    def fake_execute(self, req, machine_uuid, process_uuid, filament_uuids, out_dir, provider):
        seen["path"] = Path(req.source_3mf)
        seen["bytes"] = Path(req.source_3mf).read_bytes()
        seen["key_cleared"] = req.source_key is None
        return str(out_dir / "r.gcode")

    svc = SlicerService(data_dir=str(tmp_path))
    with patch("app.services.providers.slicing.configured_server_url", return_value="http://laminus:5000"), \
         patch.object(SlicerService, "_resolve_uuids", return_value=("m", "p", ["f"])), \
         patch.object(SlicerService, "_execute_slice_by_ids", fake_execute):
        result = svc.slice(_slice_request("Prints/Benchy.3mf"))

    assert result.endswith("r.gcode")
    assert seen["bytes"] == b"model-bytes" and seen["path"].name == "Benchy.3mf" and seen["key_cleared"]
    assert not seen["path"].exists()   # the materialized copy is released once the slice is done
    assert library.materialized_dirs and not library.materialized_dirs[0].exists()


def test_slicing_a_model_missing_from_the_library_is_a_slice_error(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.slicer_service.get_library_store", lambda: InMemoryArtifactStore())
    svc = SlicerService(data_dir=str(tmp_path))
    with patch("app.services.providers.slicing.configured_server_url", return_value="http://laminus:5000"):
        with pytest.raises(SliceError, match="missing from the library"):
            svc.slice(_slice_request("Prints/gone.3mf"))


@pytest.mark.asyncio
async def test_the_dispatch_slice_request_names_the_library_key_not_a_path(session_factory, tmp_path):
    qe, _ = _engine(session_factory, tmp_path)
    async with session_factory() as s:
        s.add(Printer(id=1, name="P1", printer_type="elegoo_centauri", connection_config={},
                      machine_profile_override="M"))
        f = UploadedFile(original_filename="Benchy.3mf", relative_path="Prints/Benchy.3mf", folder="/Prints",
                         plates=[], uploaded_at=_now())
        s.add(f)
        await s.flush()
        j = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status="queued", created_at=_now(), updated_at=_now())
        s.add(j)
        await s.flush()
        s.add(JobPrinterConfig(job_id=j.id, printer_id=1, print_profile="P", filament_type="any", filament_color="any"))
        await s.commit()
        from app.services.queue_engine import _slice_params
        cfg = (await s.execute(select(JobPrinterConfig))).scalar_one()
        params = _slice_params(j, cfg, await s.get(Printer, 1), f)
    assert params["source_key"] == "Prints/Benchy.3mf" and "stored_path" not in params
    req, _ = qe._slice_request(1, 1, 1, params)
    assert req.source_key == "Prints/Benchy.3mf"


def test_verify_and_manual_complete_slices_name_the_library_key_not_a_path():
    """jobs._build_slice_request (verify-slice, complete-manually) hands the slicer the library key."""
    from app.api.routes.jobs import _build_slice_request
    printer = Printer(id=1, name="P1", printer_type="elegoo_centauri", connection_config={},
                      machine_profile_override="M", loaded_filaments=[])
    f = UploadedFile(original_filename="Benchy.3mf", relative_path="Prints/Benchy.3mf", folder="/Prints", plates=[],
                     uploaded_at=_now())
    job = Job(id=5, uploaded_file_id=1, plate_number=2, status="queued", created_at=_now(), updated_at=_now())
    cfg = JobPrinterConfig(job_id=5, printer_id=1, print_profile="P", filament_type="any", filament_color="any")

    req = _build_slice_request(job, cfg, printer, f, "M")

    assert (req.source_key, req.source_3mf) == ("Prints/Benchy.3mf", "")
    assert req.plate_number == 2


@pytest.mark.asyncio
async def test_cancelling_a_save_mid_copy_still_removes_the_published_file(session_factory, tmp_path, stores):
    """The copy runs in a worker thread and publishes even if the awaiting task is cancelled; the save must
    wait for it and remove the key, not leave an unregistered file in the library."""
    import asyncio
    import threading
    library, _ = stores
    job_id = await _seed(session_factory)
    started, release = threading.Event(), threading.Event()
    real = library.put_file

    def slow_put_file(key, path, **kw):
        started.set()
        release.wait(5)
        return real(key, path, **kw)

    library.put_file = slow_put_file
    task = asyncio.ensure_future(_save(session_factory, tmp_path, job_id))
    while not started.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [k.key for k in library.list()] == ["Prints/Benchy.3mf"]
