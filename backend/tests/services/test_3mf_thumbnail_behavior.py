"""Thumbnail behavior of the offline 3MF reader and of thumbnail_regen (BIZ-258, docs/architecture/3mf-thumbnails.md).

Pins what a user gets when a 3MF has no / partial / corrupt thumbnails and when Laminus (or OrcaSlicer) is unavailable.
Real files: docs/reference 3mfs; synthetic archives for the cases no reference file covers.
"""
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models import UploadedFile
from app.services import thumbnail_regen
from app.services.library_scanner import LibraryScanner
from app.services.providers.slicing import get_slicing_provider
from app.services.three_mf_parser import parse_sliced_archive, parse_three_mf

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


def test_a_plate_whose_png_is_missing_is_dropped_when_other_plates_have_one(tmp_path):
    # Known limitation (follow-up): plates are discovered from Metadata/plate_N.png first; model_settings.config is only
    # consulted when NO plate PNG exists. A partially stripped file silently loses plates, so regen never sees them either.
    f = _zip(tmp_path / "m.3mf", {"Metadata/model_settings.config": TWO_PLATES, "Metadata/plate_1.png": PNG + b"1"})
    out = parse_three_mf(f, thumbnail_dir=str(tmp_path / "t"))
    assert [p.plate_number for p in out] == [1]
    assert Path(out[0].thumbnail_path).read_bytes() == PNG + b"1"


def test_reference_file_with_one_plate_thumbnail_stripped_loses_that_plate(tmp_path):
    src = REF / "elegoo blank 2 plates.3mf"
    stripped = tmp_path / "stripped.3mf"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(stripped, "w") as zout:
        for n in zin.namelist():
            if n != "Metadata/plate_2.png":
                zout.writestr(n, zin.read(n))
    out = parse_three_mf(str(stripped), thumbnail_dir=str(tmp_path / "t"))
    assert [p.plate_number for p in out] == [1]   # same limitation on a real Orca-written file


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
    with patch.object(thumbnail_regen.subprocess, "run", side_effect=AssertionError("orca launched")):
        with_thumbs = scanner._parse_plates(ref, 1)
        without = scanner._parse_plates(bare, 2)
    assert [Path(p["thumbnail_path"]).name for p in with_thumbs] == ["plate_1.png", "plate_2.png"]
    assert [p["thumbnail_path"] for p in without] == [None, None]    # the scan leaves them for regen; it never renders


# ---- regen with the slicer unavailable ---------------------------------------------------------------------

@pytest.mark.parametrize("laminus_url", [None, "http://127.0.0.1:9"])   # not configured / configured but unreachable
async def test_regen_leaves_plates_untouched_when_orca_is_unavailable(monkeypatch, tmp_path, session_factory, laminus_url):
    """regen shells out to the local Orca binary and never consults Laminus, so the sidecar state is irrelevant; a
    missing binary (the Docker app container has none) must be a silent no-op, not an exception."""
    if laminus_url:
        monkeypatch.setenv("LAMINUS_SIDECAR_URL", laminus_url)
    monkeypatch.setenv("ORCA_EXECUTABLE", str(tmp_path / "no-such-orca"))
    lib, cache = tmp_path / "lib", tmp_path / "cache"
    lib.mkdir()
    _zip(lib / "m.3mf", {"Metadata/model_settings.config": TWO_PLATES})
    monkeypatch.setattr(thumbnail_regen, "get_library_dir", lambda: lib)
    monkeypatch.setattr(thumbnail_regen, "get_filecache_dir", lambda: cache)
    plates = [{"plate_number": 1, "thumbnail_path": None}, {"plate_number": 2, "thumbnail_path": None}]
    async with session_factory() as s:
        s.add(UploadedFile(id=1, original_filename="m.3mf", relative_path="m.3mf", plates=plates,
                           uploaded_at="2026-01-01T00:00:00"))
        await s.commit()
    original = thumbnail_regen._session_factory
    thumbnail_regen.set_session_factory(session_factory)
    try:
        await thumbnail_regen.regen_file_thumbnails(1)
    finally:
        thumbnail_regen.set_session_factory(original)
    async with session_factory() as s:
        assert (await s.get(UploadedFile, 1)).plates == plates
    assert list((cache / "1" / "thumbnails").iterdir()) == []
