import json, zipfile
from app.services.three_mf_parser import parse_model_filaments


def _mk_3mf(tmp_path, project_settings: dict):
    p = tmp_path / "m.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        z.writestr("Metadata/project_settings.config", json.dumps(project_settings))
    return str(p)


def test_parse_model_filaments_multi(tmp_path):
    f = _mk_3mf(tmp_path, {
        "filament_colour": ["#FFFFFF", "#F78E0E", "#003776"],
        "filament_type": ["PLA", "PLA", "PETG"],
    })
    assert parse_model_filaments(f) == [
        {"index": 1, "color": "#FFFFFF", "type": "PLA"},
        {"index": 2, "color": "#F78E0E", "type": "PLA"},
        {"index": 3, "color": "#003776", "type": "PETG"},
    ]


def test_parse_model_filaments_single(tmp_path):
    f = _mk_3mf(tmp_path, {"filament_colour": ["#888888"], "filament_type": ["PLA"]})
    assert len(parse_model_filaments(f)) == 1


def test_parse_model_filaments_none_when_absent(tmp_path):
    f = _mk_3mf(tmp_path, {})
    assert parse_model_filaments(f) == []
