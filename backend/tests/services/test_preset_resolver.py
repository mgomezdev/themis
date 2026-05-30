import json

import pytest

from app.services.preset_resolver import PresetResolver, PresetNotFoundError


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_config_tree(tmp_path):
    """Minimal OrcaSlicer-like config dir: a system base + sub, and a user leaf
    that inherits across folders (incl. a nested subdir + a backup to ignore)."""
    root = tmp_path / "orca"
    # system base (root of chain) in a nested vendor subfolder
    _write(root / "system/Vendor/machine/base/common.json",
           {"name": "common", "type": "machine", "nozzle_diameter": ["0.4"], "bed_temp": "60"})
    _write(root / "system/Vendor/machine/ECC/sub.json",
           {"name": "sub", "type": "machine", "inherits": "common", "bed_temp": "65", "printer_model": "Foo"})
    # signed-in user account folder with the leaf preset (a thin diff)
    _write(root / "user/123456/machine/leaf.json",
           {"name": "leaf", "from": "User", "inherits": "sub", "printer_settings_id": "Vendor", "host": "1.2.3.4"})
    # a backup folder that must be ignored
    _write(root / "user_backup-v2.3.0/123456/machine/leaf.json",
           {"name": "leaf", "inherits": "sub", "host": "SHOULD_NOT_WIN"})
    return str(root)


def test_resolves_inheritance_chain_and_overrides(tmp_path):
    r = PresetResolver(_make_config_tree(tmp_path))
    cfg = r.resolve("leaf", "machine")
    assert cfg["nozzle_diameter"] == ["0.4"]   # from root base
    assert cfg["bed_temp"] == "65"             # sub overrides base
    assert cfg["printer_model"] == "Foo"       # from sub
    assert cfg["host"] == "1.2.3.4"            # from leaf
    assert cfg["type"] == "machine" and cfg["from"] == "User"
    assert "inherits" not in cfg


def test_printer_settings_id_fixed_to_preset_name(tmp_path):
    r = PresetResolver(_make_config_tree(tmp_path))
    cfg = r.resolve("leaf", "machine")
    # leaf stored printer_settings_id="Vendor" but the project needs the preset name
    assert cfg["printer_settings_id"] == "leaf"


def test_backup_folders_ignored(tmp_path):
    r = PresetResolver(_make_config_tree(tmp_path))
    cfg = r.resolve("leaf", "machine")
    assert cfg["host"] == "1.2.3.4"  # the real user leaf, not the backup copy


def test_not_found_raises(tmp_path):
    r = PresetResolver(_make_config_tree(tmp_path))
    with pytest.raises(PresetNotFoundError):
        r.resolve("nope", "machine")


def test_unknown_category_raises(tmp_path):
    r = PresetResolver(_make_config_tree(tmp_path))
    with pytest.raises(ValueError):
        r.resolve("leaf", "widget")
