"""Characterization of today's 3MF behavior (BIZ-259, docs/architecture/3mf-ownership.md).

Pins what the live callers get from three_mf_parser / the format-provider methods, so the
moves and deletions in the ownership doc's follow-ups can be done without changing user-visible output.
Real files: docs/reference 3mfs (Orca-written, unsliced). Synthetic archives cover what no reference file has
(instances, sliced plates, estimates).
"""
import json
import zipfile
from pathlib import Path

import pytest

from app.services.providers.contract import OverrideInspection, PlateEstimate, SettingChange, SlotWarning
from app.services.providers.slicing import get_format_provider, get_slicing_provider
from app.services.three_mf_parser import (
    parse_embedded_settings, parse_model_filaments, parse_sliced_archive, parse_three_mf, source_has_project_settings,
)

REF = Path(__file__).resolve().parents[3] / "docs" / "reference 3mfs"
ELEGOO_2 = REF / "elegoo blank 2 plates.3mf"
ELEGOO_1 = REF / "elegoo blank single plate.3mf"
SNAP_2 = REF / "snapmaker blank 2 plates.3mf"
SNAP_1 = REF / "snapmaker blank single plate.3mf"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Every test runs with no Laminus configured: this layer must work offline."""
    monkeypatch.delenv("LAMINUS_SIDECAR_URL", raising=False)
    assert get_slicing_provider() is None


def _zip(path: Path, files: dict[str, bytes | str]) -> str:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return str(path)


PNG = b"\x89PNG\r\n\x1a\n"
GCODE_SUMMARY = "; filament used [g] = 1.5, 2.5\n; estimated printing time (normal mode) = 1h 2m 3s\n"


# ---- multi-plate + thumbnails (real files) --------------------------------------------------------------

@pytest.mark.parametrize("ref, plates", [(ELEGOO_2, [1, 2]), (SNAP_2, [1, 2]), (ELEGOO_1, [1]), (SNAP_1, [1])])
def test_reference_files_report_their_plates_and_extract_each_plate_thumbnail(ref, plates, tmp_path):
    out = parse_three_mf(str(ref), thumbnail_dir=str(tmp_path / "t"))
    assert [p.plate_number for p in out] == plates
    with zipfile.ZipFile(ref) as z:
        for p in out:
            assert Path(p.thumbnail_path).name == f"plate_{p.plate_number}.png"
            assert Path(p.thumbnail_path).read_bytes() == z.read(f"Metadata/plate_{p.plate_number}.png")


def test_unsliced_reference_files_have_no_estimates(tmp_path):
    # Real Orca slice_info.config is XML with no <plate>; the JSON slice_info path only matters for synthetic files.
    for p in parse_three_mf(str(ELEGOO_2)):
        assert (p.estimated_time, p.filament_g) == (0, 0.0)
        assert p.thumbnail_path is None   # no thumbnail_dir given -> nothing written


def test_json_slice_info_supplies_estimates_for_a_model_3mf(tmp_path):
    f = _zip(tmp_path / "m.3mf", {
        "Metadata/slice_info.config": json.dumps({"plate": [{"index": 1, "prediction": 600, "weight": [1.5, 2.0]}]}),
        "Metadata/plate_1.png": PNG,
    })
    (p,) = parse_three_mf(f)
    assert (p.plate_number, p.estimated_time, p.filament_g) == (1, 600, 3.5)


def test_plates_are_discovered_from_model_settings_when_no_thumbnails(tmp_path):
    f = _zip(tmp_path / "m.3mf", {
        "Metadata/model_settings.config":
            '<config><plate><metadata key="plater_id" value="1"/></plate>'
            '<plate><metadata key="plater_id" value="3"/></plate></config>',
    })
    assert [p.plate_number for p in parse_three_mf(f)] == [1, 3]


def test_global_thumbnail_is_reused_for_every_plate_of_a_non_bambu_file(tmp_path):
    f = _zip(tmp_path / "m.3mf", {
        "Metadata/model_settings.config":
            '<config><plate><id value="1"/></plate><plate><id value="2"/></plate></config>',
        "Metadata/thumbnail.png": PNG + b"x",
    })
    out = parse_three_mf(f, thumbnail_dir=str(tmp_path / "t"))
    assert [Path(p.thumbnail_path).read_bytes() for p in out] == [PNG + b"x"] * 2


# ---- embedded settings + filament slots (real files) ------------------------------------------------------

def test_embedded_settings_of_reference_file_are_the_curated_subset_in_curated_order():
    out = parse_embedded_settings(str(ELEGOO_1))
    by_key = {r["key"]: r for r in out}
    assert by_key["layer_height"] == {"key": "layer_height", "label": "Layer height", "value": "0.16"}
    assert by_key["sparse_infill_density"]["value"] == "15%"
    assert by_key["wall_loops"]["value"] == "2"
    curated = [s.key for s in get_format_provider().curated_settings()]
    assert [r["key"] for r in out] == [k for k in curated if k in by_key]


def test_filament_slots_of_reference_files():
    assert parse_model_filaments(str(ELEGOO_1)) == [{"index": 1, "color": "#FFFFFF", "type": "PLA"}]
    snap = parse_model_filaments(str(SNAP_1))
    assert [(f["index"], f["color"], f["type"]) for f in snap] == [
        (1, "#F3D6E5", "PETG"), (2, "#080A0D", "PLA"), (3, "#080A0D", "PLA"), (4, "#FFFFFF", "PLA")]


def test_filament_slots_empty_without_project_settings_or_for_non_zip(tmp_path):
    assert parse_model_filaments(_zip(tmp_path / "a.3mf", {"3D/3dmodel.model": "<model/>"})) == []
    bad = tmp_path / "b.3mf"
    bad.write_bytes(b"not a zip")
    assert parse_model_filaments(str(bad)) == []
    assert parse_embedded_settings(str(bad)) == []
    assert parse_three_mf(str(bad)) == []


# ---- project-settings detection + override inspection (provider-owned) -----------------------------------

def test_reference_files_count_as_projects_and_bare_geometry_does_not(tmp_path):
    assert all(source_has_project_settings(r) for r in (ELEGOO_1, ELEGOO_2, SNAP_1, SNAP_2))
    assert not source_has_project_settings(_zip(tmp_path / "bare.3mf", {"3D/3dmodel.model": "<model/>"}))
    assert not source_has_project_settings(tmp_path / "missing.3mf")


def test_inspect_overrides_on_reference_file_reports_curated_diffs_offline():
    res = get_format_provider().inspect_overrides(
        str(ELEGOO_1), {"layer_height": "0.2", "wall_loops": "2", "not_curated": "x"}, 1)
    assert res.has_embedded_settings is True
    assert res.setting_changes == (SettingChange("layer_height", "0.16", "0.2"),)
    assert res.slot_warning is None and res.has_findings is True


def test_inspect_overrides_reads_per_object_and_per_part_extruders(tmp_path):
    """Object/instance metadata Themis reads today: only the highest `extruder` key in model_settings.config."""
    model_settings = (
        '<config><object id="2"><metadata key="name" value="a"/><metadata key="extruder" value="1"/>'
        '<part id="3" subtype="normal_part"><metadata key="extruder" value="3"/></part></object>'
        '<plate><metadata key="plater_id" value="1"/>'
        '<model_instance><metadata key="object_id" value="2"/><metadata key="instance_id" value="0"/>'
        '</model_instance></plate></config>')
    f = _zip(tmp_path / "o.3mf", {"Metadata/project_settings.config": "{}",
                                  "Metadata/model_settings.config": model_settings})
    fmt = get_format_provider()
    assert fmt.inspect_overrides(f, {}, 2).slot_warning == SlotWarning(used_slots=3, printer_slots=2)
    assert fmt.inspect_overrides(f, {}, 3).slot_warning is None


def test_inspect_overrides_on_bare_geometry_has_no_embedded_settings(tmp_path):
    f = _zip(tmp_path / "bare.3mf", {"3D/3dmodel.model": "<model/>"})
    assert get_format_provider().inspect_overrides(f, {"layer_height": "0.2"}, 1) == OverrideInspection(
        has_embedded_settings=False, setting_changes=(), slot_warning=None)


# ---- sliced archives (.gcode.3mf): estimates + thumbnails -------------------------------------------------

def _sliced(tmp_path, slice_info: str | None):
    files = {
        "Metadata/plate_1.gcode": GCODE_SUMMARY, "Metadata/plate_1.png": PNG + b"1",
        "Metadata/plate_2.gcode": "; filament used [g] = 9.0\n; estimated printing time (normal mode) = 10m 0s\n",
        "Metadata/plate_2.png": PNG + b"2",
    }
    if slice_info is not None:
        files["Metadata/slice_info.config"] = slice_info
    return _zip(tmp_path / "s.gcode.3mf", files)


def test_sliced_archive_estimates_come_from_slice_info_xml_then_gcode_summary(tmp_path):
    xml = ('<config><plate><metadata key="index" value="1"/><metadata key="prediction" value="100"/>'
           '<metadata key="weight" value="7.5"/></plate></config>')
    p1, p2 = parse_sliced_archive(_sliced(tmp_path, xml), thumbnail_dir=str(tmp_path / "t"))
    assert (p1.plate_number, p1.estimated_time, p1.filament_g) == (1, 100, 7.5)       # slice_info wins
    assert (p2.plate_number, p2.estimated_time, p2.filament_g) == (2, 600, 9.0)       # plate_2 gcode fallback
    assert Path(p1.thumbnail_path).read_bytes() == PNG + b"1"
    assert Path(p2.thumbnail_path).read_bytes() == PNG + b"2"


def test_sliced_archive_without_slice_info_falls_back_to_each_plates_gcode(tmp_path):
    p1, p2 = parse_sliced_archive(_sliced(tmp_path, None))
    assert (p1.estimated_time, p1.filament_g) == (3723, 4.0)
    assert (p2.estimated_time, p2.filament_g) == (600, 9.0)
    assert p1.thumbnail_path is None   # no thumbnail_dir


def test_provider_parse_estimates_selects_plate_inside_archive_and_plain_gcode(tmp_path):
    fmt = get_format_provider()
    archive = _sliced(tmp_path, None)
    assert fmt.parse_estimates(archive, 2) == PlateEstimate(9.0, 600, (9.0,))
    assert fmt.parse_estimates(archive, 1) == PlateEstimate(4.0, 3723, (1.5, 2.5))
    g = tmp_path / "x.gcode"
    g.write_text(GCODE_SUMMARY)
    assert fmt.parse_estimates(str(g)) == PlateEstimate(4.0, 3723, (1.5, 2.5))
    assert fmt.parse_estimates(str(tmp_path / "nope.gcode")) == PlateEstimate(None, None, None)


def test_sliced_archive_corrupt_returns_empty(tmp_path):
    bad = tmp_path / "b.gcode.3mf"
    bad.write_bytes(b"nope")
    assert parse_sliced_archive(str(bad)) == []
