import json
import zipfile
import pytest
from pathlib import Path
from app.services.three_mf_parser import parse_three_mf, PlateInfo


def _make_three_mf(tmp_path: Path, plates: list[dict], with_thumbnails: bool = True) -> Path:
    """Build a minimal 3MF ZIP with slice_info.config and optional thumbnails."""
    path = tmp_path / "test.3mf"
    slice_info = {"plate": plates}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", json.dumps(slice_info))
        if with_thumbnails:
            for p in plates:
                zf.writestr(f"Metadata/plate_{p['index']}.png", b"\x89PNG\r\n\x1a\n")
    return path


def test_parse_single_plate(tmp_path):
    f = _make_three_mf(tmp_path, [{"index": 1, "prediction": 3600, "weight": [42.1]}])
    plates = parse_three_mf(str(f))
    assert len(plates) == 1
    assert plates[0].plate_number == 1
    assert plates[0].estimated_time == 3600
    assert abs(plates[0].filament_g - 42.1) < 0.01


def test_parse_two_plates(tmp_path):
    f = _make_three_mf(tmp_path, [
        {"index": 1, "prediction": 3600, "weight": [42.1]},
        {"index": 2, "prediction": 1800, "weight": [21.5]},
    ])
    plates = parse_three_mf(str(f))
    assert len(plates) == 2
    assert plates[1].plate_number == 2
    assert abs(plates[1].filament_g - 21.5) < 0.01


def test_thumbnail_extracted(tmp_path):
    f = _make_three_mf(tmp_path, [{"index": 1, "prediction": 0, "weight": [0]}])
    thumb_dir = tmp_path / "thumbs"
    plates = parse_three_mf(str(f), thumbnail_dir=str(thumb_dir))
    assert plates[0].thumbnail_path is not None
    assert Path(plates[0].thumbnail_path).exists()


def test_no_thumbnail_when_missing_in_zip(tmp_path):
    f = _make_three_mf(tmp_path, [{"index": 1, "prediction": 0, "weight": [0]}], with_thumbnails=False)
    plates = parse_three_mf(str(f))
    assert plates[0].thumbnail_path is None


def test_multiple_filament_weights_summed(tmp_path):
    f = _make_three_mf(tmp_path, [{"index": 1, "prediction": 100, "weight": [10.0, 5.5]}])
    plates = parse_three_mf(str(f))
    assert abs(plates[0].filament_g - 15.5) < 0.01


def test_missing_slice_info_returns_defaults(tmp_path):
    # 3MF without slice_info.config but with a thumbnail
    path = tmp_path / "bare.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/plate_1.png", b"\x89PNG")
    plates = parse_three_mf(str(path))
    assert len(plates) == 1
    assert plates[0].plate_number == 1
    assert plates[0].estimated_time == 0
    assert plates[0].filament_g == 0.0


_RELS_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"'
    ' Target="{target}" Id="rel0"/>'
    "</Relationships>"
)


def test_no_plate_metadata_falls_back_to_single_plate(tmp_path):
    # Non-Bambu 3MFs (e.g. PrusaSlicer) have no slice_info or plate PNGs.
    # Parser discovers the model via _rels/.rels and returns one plate.
    path = tmp_path / "prusa.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("_rels/.rels", _RELS_TEMPLATE.format(target="/3D/3dmodel.model"))
        zf.writestr("3D/3dmodel.model", "<model/>")
    plates = parse_three_mf(str(path))
    assert len(plates) == 1
    assert plates[0].plate_number == 1
    assert plates[0].estimated_time == 0
    assert plates[0].filament_g == 0.0


def test_non_standard_model_filename_falls_back_to_single_plate(tmp_path):
    # 3MF with a non-standard model path (e.g. 3D/model.model) is still
    # discovered via _rels/.rels per the spec and treated as a single plate.
    path = tmp_path / "alt_name.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("_rels/.rels", _RELS_TEMPLATE.format(target="/3D/model.model"))
        zf.writestr("3D/model.model", "<model/>")
    plates = parse_three_mf(str(path))
    assert len(plates) == 1
    assert plates[0].plate_number == 1


def test_model_settings_config_plate_discovery(tmp_path):
    # Pre-slice 3MFs (e.g. gridfinity-customizer) carry plate assignments
    # only in Metadata/model_settings.config XML — no thumbnails or slice_info.
    model_settings = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<config>"
        '<plate><id value="1"/><objects_id><id value="2"/></objects_id></plate>'
        '<plate><id value="2"/><objects_id><id value="3"/></objects_id></plate>'
        '<plate><id value="3"/><objects_id><id value="4"/></objects_id></plate>'
        "</config>"
    )
    path = tmp_path / "gridfinity.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("_rels/.rels", _RELS_TEMPLATE.format(target="/3D/model.model"))
        zf.writestr("3D/model.model", "<model/>")
        zf.writestr("Metadata/model_settings.config", model_settings)
    plates = parse_three_mf(str(path))
    assert len(plates) == 3
    assert [p.plate_number for p in plates] == [1, 2, 3]


def test_no_3d_model_returns_empty(tmp_path):
    # A ZIP with no _rels/.rels and no recognised plate metadata returns nothing.
    path = tmp_path / "empty.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("junk.txt", "hello")
    plates = parse_three_mf(str(path))
    assert plates == []


def test_malformed_slice_info_uses_defaults(tmp_path):
    path = tmp_path / "bad.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", "NOT VALID JSON {{{")
        zf.writestr("Metadata/plate_1.png", b"\x89PNG\r\n\x1a\n")
    plates = parse_three_mf(str(path))
    assert len(plates) == 1
    assert plates[0].plate_number == 1
    assert plates[0].estimated_time == 0
    assert plates[0].filament_g == 0.0
