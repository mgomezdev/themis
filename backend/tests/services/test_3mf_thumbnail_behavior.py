"""Thumbnail behavior of the offline 3MF reader (BIZ-258/284, docs/architecture/3mf-thumbnails.md).

Pins what a user gets when a 3MF has no / partial / corrupt thumbnails and when Laminus (or OrcaSlicer) is unavailable.
Real files: docs/reference 3mfs; synthetic archives for the cases no reference file covers.
"""
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.library_scanner import LibraryScanner
from app.services.providers.slicing import get_slicing_provider
from app.services.three_mf_parser import parse_sliced_archive, parse_three_mf
from tests.conftest import slice_info_xml

REF = Path(__file__).resolve().parents[3] / "docs" / "reference 3mfs"
PNG = b"\x89PNG\r\n\x1a\n"
TWO_PLATES = '<config><plate><metadata key="plater_id" value="1"/></plate><plate><metadata key="plater_id" value="2"/></plate></config>'


@pytest.fixture(autouse=True)
def no_laminus(monkeypatch):
    monkeypatch.delenv("LAMINUS_SIDECAR_URL", raising=False)
    assert get_slicing_provider() is None


def _zip(path: Path, files: dict[str, bytes | str]) -> str:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return str(path)


# ---- missing thumbnails: plates are still listed, thumbnail_path is None ---------------------------------

def test_model_3mf_without_any_thumbnail_still_lists_its_plates_with_no_thumbnail(tmp_path):
    f = _zip(tmp_path / "m.3mf", {"Metadata/model_settings.config": TWO_PLATES})
    out = parse_three_mf(f, thumbnail_dir=str(tmp_path / "t"))
    assert [(p.plate_number, p.thumbnail_path) for p in out] == [(1, None), (2, None)]
    assert list((tmp_path / "t").iterdir()) == []


def test_a_plate_whose_png_is_missing_is_kept_when_other_plates_have_one(tmp_path):
    # BIZ-284: plates are the union of PNG names and model_settings.config, so a partially stripped file keeps every plate.
    f = _zip(tmp_path / "m.3mf", {"Metadata/model_settings.config": TWO_PLATES, "Metadata/plate_1.png": PNG + b"1"})
    out = parse_three_mf(f, thumbnail_dir=str(tmp_path / "t"))
    assert [p.plate_number for p in out] == [1, 2]
    assert Path(out[0].thumbnail_path).read_bytes() == PNG + b"1"
    assert out[1].thumbnail_path is None


def test_a_plate_only_named_by_slice_info_is_kept_alongside_png_plates(tmp_path):
    f = _zip(tmp_path / "m.3mf", {"Metadata/slice_info.config": slice_info_xml([(1, 60, 1.0), (2, 90, 2.0)]),
                                  "Metadata/plate_1.png": PNG})
    out = parse_three_mf(f, thumbnail_dir=str(tmp_path / "t"))
    assert [(p.plate_number, p.estimated_time, p.thumbnail_path is None) for p in out] == [(1, 60, False), (2, 90, True)]


def test_reference_file_with_one_plate_thumbnail_stripped_keeps_that_plate(tmp_path):
    src = REF / "elegoo blank 2 plates.3mf"
    stripped = tmp_path / "stripped.3mf"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(stripped, "w") as zout:
        for n in zin.namelist():
            if n != "Metadata/plate_2.png":
                zout.writestr(n, zin.read(n))
    out = parse_three_mf(str(stripped), thumbnail_dir=str(tmp_path / "t"))
    assert [p.plate_number for p in out] == [1, 2]   # real Orca-written file: plate 2 survives without its PNG
    assert [Path(p.thumbnail_path).name if p.thumbnail_path else None for p in out] == ["plate_1.png", None]


def test_each_reference_file_with_all_plate_pngs_stripped_still_lists_every_plate(tmp_path):
    for name, plates in [("elegoo blank 2 plates.3mf", [1, 2]), ("snapmaker blank 2 plates.3mf", [1, 2])]:
        stripped = tmp_path / name
        with zipfile.ZipFile(REF / name) as zin, zipfile.ZipFile(stripped, "w") as zout:
            for n in zin.namelist():
                if not (n.startswith("Metadata/plate_") and n.endswith(".png")):
                    zout.writestr(n, zin.read(n))
        assert [p.plate_number for p in parse_three_mf(str(stripped))] == plates


def test_sliced_archive_plate_without_png_has_no_thumbnail_but_keeps_its_estimate(tmp_path):
    f = _zip(tmp_path / "s.gcode.3mf", {
        "Metadata/plate_1.gcode": "; filament used [g] = 2.0\n; estimated printing time (normal mode) = 1m 0s\n",
        "Metadata/plate_2.gcode": "; filament used [g] = 3.0\n; estimated printing time (normal mode) = 2m 0s\n",
        "Metadata/plate_1.png": PNG,
    })
    p1, p2 = parse_sliced_archive(f, thumbnail_dir=str(tmp_path / "t"))
    assert p1.thumbnail_path is not None
    assert (p2.thumbnail_path, p2.estimated_time, p2.filament_g) == (None, 120, 3.0)


# ---- corrupt input -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("parse", [parse_three_mf, parse_sliced_archive])
def test_corrupt_archive_yields_no_plates_and_writes_nothing(parse, tmp_path):
    bad = tmp_path / "bad.3mf"
    bad.write_bytes(b"not a zip")
    assert parse(str(bad), thumbnail_dir=str(tmp_path / "t")) == []
    assert not (tmp_path / "t").exists()


def test_a_corrupt_png_inside_a_valid_archive_is_copied_verbatim_not_validated(tmp_path):
    # Known limitation: the reader trusts the bytes; the thumbnail endpoint serves them and the browser shows a broken image.
    f = _zip(tmp_path / "m.3mf", {"Metadata/plate_1.png": b"garbage"})
    (p,) = parse_three_mf(f, thumbnail_dir=str(tmp_path / "t"))
    assert Path(p.thumbnail_path).read_bytes() == b"garbage"


# ---- library scan never needs Orca or Laminus --------------------------------------------------------------

def test_library_scan_reads_embedded_thumbnails_without_launching_orca_or_laminus(tmp_path):
    lib, cache = tmp_path / "lib", tmp_path / "cache"
    lib.mkdir()
    ref = lib / "e.3mf"
    ref.write_bytes((REF / "elegoo blank 2 plates.3mf").read_bytes())
    bare = lib / "bare.3mf"
    _zip(bare, {"Metadata/model_settings.config": TWO_PLATES})
    scanner = LibraryScanner(session=None, library_dir=lib, filecache_dir=cache)
    with patch.object(subprocess, "run", side_effect=AssertionError("orca launched")):
        with_thumbs = scanner.parse_plates("e.3mf", 1)
        without = scanner.parse_plates("bare.3mf", 2)
    assert [Path(p["thumbnail_path"]).name for p in with_thumbs] == ["plate_1.png", "plate_2.png"]
    assert [p["thumbnail_path"] for p in without] == [None, None]    # no thumbnail is rendered for them


# ---- upload spawns nothing (BIZ-284: the Orca-CLI thumbnail regen is gone) --------------------------------

@pytest.mark.parametrize("laminus_url", [None, "http://127.0.0.1:9"])   # not configured / configured but unreachable
async def test_upload_of_a_thumbnail_less_3mf_spawns_no_subprocess_and_leaves_thumbnails_null(
        monkeypatch, client, upload_3mf, laminus_url):
    if laminus_url:
        monkeypatch.setenv("LAMINUS_SIDECAR_URL", laminus_url)
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Metadata/model_settings.config", TWO_PLATES)
    boom = AssertionError("subprocess spawned")
    with patch("subprocess.run", side_effect=boom), patch("subprocess.Popen", side_effect=boom), \
         patch("asyncio.create_subprocess_exec", side_effect=boom):
        file_id = await upload_3mf("bare.3mf", buf.getvalue())
    body = (await client.get(f"/api/v1/files/{file_id}/plates")).json()["plates"]
    assert [(p["plate_number"], p["thumbnail_path"]) for p in body] == [(1, None), (2, None)]
    assert (await client.get("/api/v1/files")).json()[0]["plate_thumbnails"] == []


def test_thumbnail_regen_module_and_orca_executable_config_are_gone():
    import importlib
    from app import config
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.thumbnail_regen")
    assert not hasattr(config, "get_orca_executable")


async def test_upload_of_a_partially_stripped_3mf_keeps_all_plates_in_the_library(client, upload_3mf):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Metadata/model_settings.config", TWO_PLATES)
        z.writestr("Metadata/plate_1.png", PNG + b"1")
    file_id = await upload_3mf("partial.3mf", buf.getvalue())
    plates = (await client.get(f"/api/v1/files/{file_id}/plates")).json()["plates"]
    assert [(p["plate_number"], p["thumbnail_path"] is None) for p in plates] == [(1, False), (2, True)]
    listed = next(f for f in (await client.get("/api/v1/files")).json() if f["id"] == file_id)
    assert [t["plate_number"] for t in listed["plate_thumbnails"]] == [1]
