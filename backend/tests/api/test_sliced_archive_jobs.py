"""Library files and jobs on a pre-sliced .gcode.3mf archive (BIZ-190): a sliced archive is not a sliceable model —
it's printed as-is, only by vendors that ingest that archive (Bambu)."""
import io
import zipfile
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import Job, JobPrinterConfig
from app.services.library_scanner import file_kind, is_presliced_name
from app.services.model_targets import accepts_file

P1S = "Bambu Lab P1S 0.4"


def _plate_gcode(grams: float, mins: int) -> bytes:
    return (f"; filament used [g] = {grams}\n; estimated printing time (normal mode) = {mins}m 0s\nG28\n").encode()


def make_sliced_archive(plates=((1, 4.0, 10), (2, 7.5, 20))) -> bytes:
    """A Bambu-style sliced archive: `Metadata/plate_N.gcode` (+ a thumbnail for plate 1). Fixed zip timestamps so
    the content hash is stable."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        def put(name, data):
            zf.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), data)
        for num, grams, mins in plates:
            put(f"Metadata/plate_{num}.gcode", _plate_gcode(grams, mins))
        put("Metadata/plate_1.png", b"\x89PNG-archive")
    return buf.getvalue()


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("THEMIS_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("THEMIS_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "library"


async def _post(client, body):
    with patch("app.api.routes.jobs.queue_engine"):
        return await client.post("/api/v1/jobs", json=body)


def _cfg(pid):
    return {"printer_id": pid, "filament_type": "any", "filament_color": "any"}


@pytest.mark.parametrize("name,kind", [
    ("part.gcode.3mf", "gcode_3mf"), ("PART.GCODE.3MF", "gcode_3mf"), ("part.gcode", "gcode"),
    ("part.3mf", "3mf"), ("part.stl", "stl"), ("gcode.3mf.stl", "stl"),
])
def test_file_kind(name, kind):
    assert file_kind(name) == kind
    assert is_presliced_name(name) == (kind in ("gcode", "gcode_3mf"))


@pytest.mark.parametrize("printer_type,name,ok", [
    ("bambu", "a.gcode.3mf", True), ("bambu", "a.gcode", False), ("bambu", "a.3mf", True),
    ("elegoo_centauri", "a.gcode.3mf", False), ("elegoo_centauri", "a.gcode", True), ("elegoo_centauri", "a.stl", True),
    ("unknown_vendor", "a.gcode.3mf", False), ("unknown_vendor", "a.gcode", True),
])
def test_accepts_file(printer_type, name, ok):
    assert accepts_file(printer_type, name) is ok


async def test_upload_reads_each_archive_plate_and_its_thumbnail_without_regenerating(client, library, upload_3mf):
    with patch("app.api.routes.files.regen_file_thumbnails") as regen:
        file_id = await upload_3mf("part.gcode.3mf", make_sliced_archive())

    plates = (await client.get(f"/api/v1/files/{file_id}/plates")).json()["plates"]
    assert [(p["plate_number"], p["estimated_time"], p["filament_g"]) for p in plates] == [(1, 600, 4.0), (2, 1200, 7.5)]
    listed = next(f for f in (await client.get("/api/v1/files")).json() if f["id"] == file_id)
    assert listed["kind"] == "gcode_3mf"
    assert [t["plate_number"] for t in listed["plate_thumbnails"]] == [1]
    regen.assert_not_called()


async def test_upload_of_a_plain_3mf_still_regenerates_thumbnails(client, library, upload_3mf):
    with patch("app.api.routes.files.regen_file_thumbnails") as regen:
        file_id = await upload_3mf()
    regen.assert_called_once()
    listed = next(f for f in (await client.get("/api/v1/files")).json() if f["id"] == file_id)
    assert listed["kind"] == "3mf"


async def test_archive_job_on_bambu_is_not_sliced_and_reads_its_plate_estimate(
        client, library, upload_3mf, create_printer, session_factory):
    from app.services.override_inspector import CURATED_KEYS
    pid = await create_printer()   # Bambu
    resp = await _post(client, {
        "uploaded_file_id": await upload_3mf("part.gcode.3mf", make_sliced_archive()),
        "plate_number": 2,
        "printer_configs": [_cfg(pid)],
        "overrides": {sorted(CURATED_KEYS)[0]: "0.1"},
    })

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["overrides"] is None
    assert (body["estimate_status"], body["estimate_seconds"], body["estimate_filament_grams"]) == ("done", 1200, 7.5)
    async with session_factory() as s:
        assert (await s.get(Job, body["id"])).overrides is None


async def test_archive_job_refused_for_a_raw_gcode_printer(client, library, upload_3mf, create_printer):
    centauri = await create_printer(printer_type="elegoo_centauri")
    resp = await _post(client, {
        "uploaded_file_id": await upload_3mf("part.gcode.3mf", make_sliced_archive()),
        "printer_configs": [_cfg(centauri)],
    })
    assert resp.status_code == 422
    assert "sliced .gcode.3mf archive" in resp.json()["detail"]


async def test_archive_target_only_materializes_onto_archive_printers(
        client, library, upload_3mf, create_printer, session_factory):
    bambu = await create_printer(name="bambu", current_orca_printer_profile=P1S, orca_printer_profiles=[P1S])
    await create_printer(name="centauri", printer_type="elegoo_centauri",
                         current_orca_printer_profile=P1S, orca_printer_profiles=[P1S])

    resp = await _post(client, {
        "uploaded_file_id": await upload_3mf("part.gcode.3mf", make_sliced_archive()),
        "model_targets": [{"machine_profile": P1S}],
    })

    assert resp.status_code == 201, resp.text
    async with session_factory() as s:
        rows = (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == resp.json()["id"]))).scalars().all()
    assert [r.printer_id for r in rows] == [bambu]


async def test_archive_target_refused_when_only_raw_gcode_printers_have_the_model(
        client, library, upload_3mf, create_printer):
    await create_printer(printer_type="elegoo_centauri", current_orca_printer_profile=P1S, orca_printer_profiles=[P1S])
    resp = await _post(client, {
        "uploaded_file_id": await upload_3mf("part.gcode.3mf", make_sliced_archive()),
        "model_targets": [{"machine_profile": P1S}],
    })
    assert resp.status_code == 422


async def test_verify_slice_refused_for_archive_job(client, library, upload_3mf, create_printer):
    pid = await create_printer()
    job_id = (await _post(client, {
        "uploaded_file_id": await upload_3mf("part.gcode.3mf", make_sliced_archive()),
        "printer_configs": [_cfg(pid)],
    })).json()["id"]

    resp = await client.post(f"/api/v1/jobs/{job_id}/verify-slice", json={"printer_id": pid})

    assert resp.status_code == 422


async def test_rescan_indexes_a_hand_added_archive_as_sliced(client, library, tmp_path):
    library.mkdir(parents=True, exist_ok=True)
    (library / "dropped.gcode.3mf").write_bytes(make_sliced_archive(((3, 1.5, 5),)))
    with patch("app.config.get_library_dir", return_value=library), \
         patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"):
        assert (await client.post("/api/v1/files/rescan")).status_code == 200
    listed = next(f for f in (await client.get("/api/v1/files")).json() if f["original_filename"] == "dropped.gcode.3mf")
    assert listed["kind"] == "gcode_3mf"
    plates = (await client.get(f"/api/v1/files/{listed['id']}/plates")).json()["plates"]
    assert [(p["plate_number"], p["estimated_time"], p["filament_g"]) for p in plates] == [(3, 300, 1.5)]
