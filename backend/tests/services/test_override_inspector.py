import zipfile

from app.services.override_inspector import inspect_overrides


def _make_3mf(tmp_path, project=None, model_xml=None):
    p = tmp_path / "src.3mf"
    import json
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        if project is not None:
            z.writestr("Metadata/project_settings.config", json.dumps(project))
        if model_xml is not None:
            z.writestr("Metadata/model_settings.config", model_xml)
    return str(p)


def test_no_embedded_settings_means_no_findings(tmp_path):
    src = _make_3mf(tmp_path)  # geometry only
    res = inspect_overrides(src, {"enable_support": "1"}, printer_slots=1)
    assert res["has_embedded_settings"] is False
    assert res["has_findings"] is False


def test_detects_curated_setting_change(tmp_path):
    src = _make_3mf(tmp_path, project={"enable_support": "1", "layer_height": "0.2", "speed": "ignored"})
    generated = {"enable_support": "0", "layer_height": "0.2", "speed": "999"}
    res = inspect_overrides(src, generated, printer_slots=1)
    keys = {c["key"] for c in res["setting_changes"]}
    assert keys == {"enable_support"}          # changed + curated
    assert res["setting_changes"][0] == {"key": "enable_support", "from": "1", "to": "0"}
    assert res["has_findings"] is True
    # 'speed' changed but is not curated -> not surfaced as noise
    # 'layer_height' unchanged -> not surfaced


def test_slot_warning_when_file_uses_more_slots_than_printer(tmp_path):
    model = '<config><object id="1">' \
            '<part id="1"><metadata key="extruder" value="1"/></part>' \
            '<part id="2"><metadata key="extruder" value="3"/></part>' \
            '</object></config>'
    src = _make_3mf(tmp_path, project={"enable_support": "0"}, model_xml=model)
    res = inspect_overrides(src, {"enable_support": "0"}, printer_slots=1)
    assert res["slot_warning"] == {"used_slots": 3, "printer_slots": 1}
    assert res["has_findings"] is True


def test_no_slot_warning_when_within_capacity(tmp_path):
    model = '<config><object id="1"><part id="1"><metadata key="extruder" value="1"/></part></object></config>'
    src = _make_3mf(tmp_path, project={"enable_support": "0"}, model_xml=model)
    res = inspect_overrides(src, {"enable_support": "0"}, printer_slots=4)
    assert res["slot_warning"] is None
    assert res["has_findings"] is False
