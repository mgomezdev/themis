"""Slicing cache over the API (BIZ-191..193)."""
from tests.fake_providers import fake_packer
import hashlib
import os
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import GcodeFile, Job, JobPrinterConfig, SlicedVersion, UploadedFile
from app.services import slice_cache
from app.services.library_scanner import library_abs_path
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
        patch("app.api.routes.projects.get_slicing_provider") as mock_get,
        patch("app.api.routes.projects.regen_file_thumbnails", new_callable=AsyncMock),
    ):
        mock_get.return_value = fake_packer(packed)
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


# ---- reuse (BIZ-193) -----------------------------------------------------------------------------------------------

P1S = "Bambu Lab P1S 0.4"


async def _version_for(session_factory, library, model_id, *, plate=1, name="cached.gcode.3mf", machine=P1S,
                       preset_hash="ph", version="2.3.1", on_disk=True, filament_type="PETG",
                       extra_config=None, source_hash=None) -> tuple[int, int]:
    """A cached version of `model_id` -> (version id, cached file id)."""
    async with session_factory() as s:
        model = await s.get(UploadedFile, model_id)
        folder = library_abs_path(library, model.relative_path).parent
        if on_disk:
            (folder / name).write_bytes(make_sliced_archive(((plate, 2.0, 5),)))
        rel = (folder / name).relative_to(library).as_posix()
        f = UploadedFile(original_filename=name, relative_path=rel, folder=model.folder, content_hash="fh",
                         plates=[{"plate_number": plate}], uploaded_at="2026-10-03T00:00:00")
        s.add(f)
        await s.flush()
        v = SlicedVersion(file_id=f.id, source_file_id=model_id, source_content_hash=source_hash or model.content_hash,
                          plate_number=plate, machine_preset=machine, process_preset="0.20mm",
                          filament_presets=["Generic PETG"], extra_config=extra_config or {"curr_bed_type": "Cool Plate"},
                          artifact_kind="gcode_3mf", cache_key=f"k{plate}{name}", preset_content_hash=preset_hash,
                          slicer_version=version, filament_type=filament_type, filament_color="#112233",
                          estimated_seconds=300, filament_grams=2.0, created_at="2026-10-03T00:00:00")
        s.add(v)
        await s.commit()
        return v.id, f.id


async def test_sliced_versions_lists_a_models_versions_with_their_flags(
        client, library, upload_3mf, create_printer, session_factory):
    await create_printer(current_orca_printer_profile=P1S, orca_printer_profiles=[P1S])   # a Bambu: takes archives
    model = await upload_3mf("benchy.3mf")
    other = await upload_3mf("other.3mf", make_3mf_bytes_variant())
    fresh, fresh_file = await _version_for(session_factory, library, model, name="fresh.gcode.3mf",
                                           extra_config={"curr_bed_type": "Cool Plate", "wall_loops": "3"})
    stale, _ = await _version_for(session_factory, library, model, name="stale.gcode.3mf", preset_hash="OLD")
    await _version_for(session_factory, library, model, plate=2, name="plate2.gcode.3mf", machine="Other Printer")
    await _version_for(session_factory, library, model, name="gone.gcode.3mf", on_disk=True)
    await _version_for(session_factory, library, other, name="not-mine.gcode.3mf")
    async with session_factory() as s:   # one version's file went missing
        f = (await s.execute(select(UploadedFile).where(UploadedFile.original_filename == "gone.gcode.3mf"))).scalar_one()
        f.missing = True
        await s.commit()

    body = (await client.get(f"/api/v1/files/{model}/sliced-versions")).json()
    assert [v["name"] for v in body] == ["plate2.gcode.3mf", "stale.gcode.3mf", "fresh.gcode.3mf"]
    by = {v["name"]: v for v in body}
    assert {k: by["fresh.gcode.3mf"][k] for k in ("id", "file_id", "kind", "plate_number", "machine_preset",
                                                  "filament_type", "filament_color", "bed_type", "overrides",
                                                  "estimated_seconds", "filament_grams", "source_changed",
                                                  "stale", "stale_reasons", "printable_now")} == {
        "id": fresh, "file_id": fresh_file, "kind": "gcode_3mf", "plate_number": 1, "machine_preset": P1S,
        "filament_type": "PETG", "filament_color": "#112233", "bed_type": "Cool Plate", "overrides": {"wall_loops": "3"},
        "estimated_seconds": 300, "filament_grams": 2.0, "source_changed": False, "stale": False, "stale_reasons": [],
        "printable_now": True}
    assert (by["stale.gcode.3mf"]["stale"], by["stale.gcode.3mf"]["stale_reasons"]) == (True, ["presets_changed"])
    assert by["plate2.gcode.3mf"]["printable_now"] is False   # no "Other Printer" in the fleet
    assert [v["name"] for v in (await client.get(f"/api/v1/files/{model}/sliced-versions?plate=2")).json()] == [
        "plate2.gcode.3mf"]


def make_3mf_bytes_variant() -> bytes:
    """A different (but valid) model, so it gets its own content hash."""
    import io
    import json
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("Metadata/slice_info.config", date_time=(2026, 1, 1, 0, 0, 0)),
                    json.dumps({"plate": [{"index": 1, "prediction": 99, "weight": [1.0]}]}))
    return buf.getvalue()


async def test_sliced_versions_flags_a_model_changed_since_and_unknown_staleness(
        client, library, upload_3mf, session_factory):
    model = await upload_3mf()
    await _version_for(session_factory, library, model, source_hash="an-older-model")
    with patch("app.services.slice_cache.current_fingerprint", return_value=slice_cache.SlicerFingerprint(None, None)):
        (v,) = (await client.get(f"/api/v1/files/{model}/sliced-versions")).json()
    assert (v["source_changed"], v["stale"], v["printable_now"]) == (True, None, False)


async def test_sliced_versions_404s_for_an_unknown_file(client, library):
    assert (await client.get("/api/v1/files/999/sliced-versions")).status_code == 404


async def test_the_file_list_counts_versions_and_marks_cached_files(client, library, upload_3mf, session_factory):
    model = await upload_3mf()
    version_id, cached = await _version_for(session_factory, library, model)
    await _version_for(session_factory, library, model, name="b.gcode.3mf")

    files = {f["id"]: f for f in (await client.get("/api/v1/files")).json()}

    assert (files[model]["sliced_version_count"], files[model]["sliced_version"]) == (2, None)
    assert files[cached]["sliced_version_count"] == 0
    assert files[cached]["sliced_version"] == {
        "id": version_id, "source_file_id": model, "source_filename": "m.3mf", "plate_number": 1, "machine_preset": P1S,
        "process_preset": "0.20mm", "filament_presets": ["Generic PETG"], "filament_type": "PETG",
        "filament_color": "#112233"}


async def test_a_job_on_a_cached_version_records_the_hand_picked_hit(
        client, library, upload_3mf, create_printer, session_factory, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="app.services.slice_cache")
    pid = await create_printer(current_orca_printer_profile=P1S, orca_printer_profiles=[P1S])
    version_id, cached = await _version_for(session_factory, library, await upload_3mf())

    resp = await _post_job(client, {"uploaded_file_id": cached, "printer_configs": [
        {"printer_id": pid, "filament_type": "PETG", "filament_color": "#112233"}]})

    assert resp.status_code == 201, resp.text
    async with session_factory() as s:
        job = await s.get(Job, resp.json()["id"])
    assert job.sliced_version_id == version_id
    assert (job.slice_cache_info["decision"], job.slice_cache_info["reason"]) == ("hit", "user_selected")
    assert any("reason=user_selected" in r.getMessage() for r in caplog.records)


async def test_a_cached_version_is_refused_for_another_printer_model(
        client, library, upload_3mf, create_printer, session_factory):
    other = await create_printer(name="X1C", current_orca_printer_profile="Bambu Lab X1 Carbon 0.4",
                                 orca_printer_profiles=["Bambu Lab X1 Carbon 0.4"])
    _, cached = await _version_for(session_factory, library, await upload_3mf())

    by_printer = await _post_job(client, {"uploaded_file_id": cached, "printer_configs": [
        {"printer_id": other, "filament_type": "any", "filament_color": "any"}]})
    by_target = await _post_job(client, {"uploaded_file_id": cached, "model_targets": [
        {"machine_profile": "Bambu Lab X1 Carbon 0.4"}]})

    assert by_printer.status_code == 422 and "sliced for that printer model" in by_printer.json()["detail"]
    assert by_target.status_code == 422


async def _generate(client, tmp_path, project_id, body):
    from tests.api.test_projects_api import _make_3mf_bytes
    lib = tmp_path / "library"
    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_library_dir", return_value=lib),
        patch("app.api.routes.projects.get_slicing_provider") as mock_get,
        patch("app.api.routes.projects.regen_file_thumbnails", new_callable=AsyncMock),
    ):
        mock_get.return_value = fake_packer(_make_3mf_bytes(plate_count=2))
        resp = await client.post(f"/api/v1/projects/{project_id}/generate", json={"eligible_printer_ids": [], **body})
    assert resp.status_code == 200, resp.text
    return resp.json(), mock_get.return_value.pack_models


async def test_regenerating_an_identical_project_reuses_its_pack(client, tmp_path, session_factory, caplog):
    import logging
    from tests.api.test_projects_api import _setup_project_with_stl
    caplog.set_level(logging.INFO, logger="app.services.slice_cache")
    project_id, _ = await _setup_project_with_stl(client, tmp_path)

    first, pack1 = await _generate(client, tmp_path, project_id, {})
    second, pack2 = await _generate(client, tmp_path, project_id, {})

    pack1.assert_called_once()
    pack2.assert_not_called()
    assert second["files"][0]["id"] == first["files"][0]["id"]
    assert (first["files"][0]["pack_reused"], second["files"][0]["pack_reused"]) == (False, True)
    assert {j["uploaded_file_id"] for j in second["jobs"]} == {first["files"][0]["id"]}
    async with session_factory() as s:
        assert all([(await s.get(Job, j["id"])).allow_cached_slice for j in first["jobs"] + second["jobs"]])
    new, reused = _cache_line(caplog, "pack_new"), _cache_line(caplog, "pack_reused")
    assert new.split("recipe_hash=")[1].split()[0] == reused.split("recipe_hash=")[1].split()[0]


def _cache_line(caplog, event) -> str:
    (rec,) = [r for r in caplog.records if f"slice_cache event={event}" in r.getMessage()]
    return rec.getMessage()


async def test_regenerating_without_the_cache_always_repacks(client, tmp_path, session_factory):
    from tests.api.test_projects_api import _setup_project_with_stl
    project_id, _ = await _setup_project_with_stl(client, tmp_path)

    first, _ = await _generate(client, tmp_path, project_id, {"allow_cached": False})
    second, pack2 = await _generate(client, tmp_path, project_id, {"allow_cached": False})

    pack2.assert_called_once()
    assert second["files"][0]["id"] != first["files"][0]["id"] and second["files"][0]["pack_reused"] is False
    async with session_factory() as s:
        assert not any([(await s.get(Job, j["id"])).allow_cached_slice for j in second["jobs"]])


async def test_changing_a_quantity_makes_a_new_pack(client, tmp_path, session_factory):
    from app.models import ProjectItem
    from tests.api.test_projects_api import _setup_project_with_stl
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    first, _ = await _generate(client, tmp_path, project_id, {})
    async with session_factory() as s:
        item = (await s.execute(select(ProjectItem).where(ProjectItem.project_id == project_id))).scalar_one()
        item.quantity = 2
        await s.commit()

    second, pack2 = await _generate(client, tmp_path, project_id, {})

    pack2.assert_called_once()
    assert second["files"][0]["id"] != first["files"][0]["id"]


async def test_the_legacy_cleanup_never_deletes_a_reusable_pack(client, tmp_path, session_factory):
    from app.models import Project
    from tests.api.test_projects_api import _setup_project_with_stl
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    first, _ = await _generate(client, tmp_path, project_id, {})
    pack_id = first["files"][0]["id"]
    async with session_factory() as s:   # an old install pointed result_file_id at it, and its jobs are done
        (await s.get(Project, project_id)).result_file_id = pack_id
        for j in first["jobs"]:
            (await s.get(Job, j["id"])).status = "complete"
        await s.commit()

    second, pack2 = await _generate(client, tmp_path, project_id, {})

    pack2.assert_not_called()
    async with session_factory() as s:
        pack = await s.get(UploadedFile, pack_id)
    assert pack is not None and (tmp_path / "library" / pack.relative_path).exists()


async def test_a_job_printing_a_cached_version_cannot_be_flagged_to_save(client, library, create_job, session_factory):
    job_id = await create_job()
    async with session_factory() as s:
        (await s.get(Job, job_id)).sliced_version_id = 1   # the dispatch-time lookup picked a cached version
        await s.commit()
    resp = await client.patch(f"/api/v1/jobs/{job_id}/save-slice", json={"save_slice": True})
    assert resp.status_code == 422
    assert (await client.patch(f"/api/v1/jobs/{job_id}/save-slice", json={"save_slice": False})).status_code == 200


# ---- review follow-ups (BIZ-193) ---------------------------------------------------------------------------------

async def test_editing_a_cached_version_job_onto_another_model_is_refused(
        client, library, upload_3mf, create_printer, session_factory):
    pid = await create_printer(current_orca_printer_profile=P1S, orca_printer_profiles=[P1S])
    other = await create_printer(name="X1C", current_orca_printer_profile="Bambu Lab X1 Carbon 0.4",
                                 orca_printer_profiles=["Bambu Lab X1 Carbon 0.4"])
    _, cached = await _version_for(session_factory, library, await upload_3mf())
    job_id = (await _post_job(client, {"uploaded_file_id": cached, "printer_configs": [
        {"printer_id": pid, "filament_type": "any", "filament_color": "any"}]})).json()["id"]

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.patch(f"/api/v1/jobs/{job_id}/configs", json={"printer_configs": [
            {"printer_id": other, "filament_type": "any", "filament_color": "any"}]})

    assert resp.status_code == 422


async def test_the_version_count_ignores_versions_whose_file_is_missing(client, library, upload_3mf, session_factory):
    model = await upload_3mf()
    _, gone = await _version_for(session_factory, library, model, name="gone.gcode.3mf")
    await _version_for(session_factory, library, model, name="here.gcode.3mf")
    async with session_factory() as s:
        (await s.get(UploadedFile, gone)).missing = True
        await s.commit()

    files = {f["id"]: f for f in (await client.get("/api/v1/files")).json()}

    assert files[model]["sliced_version_count"] == 1


async def test_rewriting_an_stl_without_a_rescan_makes_a_new_pack(client, tmp_path, session_factory):
    from tests.api.test_projects_api import _setup_project_with_stl
    project_id, stl_id = await _setup_project_with_stl(client, tmp_path)
    first, _ = await _generate(client, tmp_path, project_id, {})
    stl = await _stl_path(session_factory, tmp_path, stl_id)
    stl.write_bytes(stl.read_bytes() + b"\n")   # re-exported in place; the index still has the old hash

    second, pack2 = await _generate(client, tmp_path, project_id, {})

    pack2.assert_called_once()
    assert second["files"][0]["pack_reused"] is False


async def _stl_path(session_factory, tmp_path, stl_id):
    async with session_factory() as s:
        return tmp_path / "library" / (await s.get(UploadedFile, stl_id)).relative_path


async def test_a_pack_edited_on_disk_is_not_reused(client, tmp_path, session_factory):
    from tests.api.test_projects_api import _setup_project_with_stl
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    first, _ = await _generate(client, tmp_path, project_id, {})
    pack = await _stl_path(session_factory, tmp_path, first["files"][0]["id"])
    pack.write_bytes(pack.read_bytes() + b"tampered")

    second, pack2 = await _generate(client, tmp_path, project_id, {})

    pack2.assert_called_once()
    assert second["files"][0]["pack_reused"] is False


async def test_a_smaller_bed_makes_a_new_pack(client, tmp_path, session_factory, create_printer):
    from tests.api.test_projects_api import _setup_project_with_stl
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    small = await create_printer(name="mini", bed_x_mm=180, bed_y_mm=180)
    await _generate(client, tmp_path, project_id, {})

    second, pack2 = await _generate(client, tmp_path, project_id, {"eligible_printer_ids": [small]})

    pack2.assert_called_once()
    assert second["files"][0]["pack_reused"] is False
