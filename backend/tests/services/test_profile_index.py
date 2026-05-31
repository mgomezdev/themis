import json

from app.services.preset_resolver import PresetResolver
from app.services.profile_index import ProfileIndex


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_tree(tmp_path):
    root = tmp_path / "orca"
    # system machine base (no model) + a real variant with model+nozzle
    _write(root / "system/Acme/machine/common.json",
           {"name": "fdm_common", "type": "machine"})
    _write(root / "system/Acme/machine/M0_4.json",
           {"name": "Acme One 0.4 nozzle", "type": "machine", "inherits": "fdm_common",
            "printer_model": "Acme One", "nozzle_diameter": ["0.4"]})
    _write(root / "system/Acme/machine/M0_6.json",
           {"name": "Acme One 0.6 nozzle", "type": "machine", "inherits": "fdm_common",
            "printer_model": "Acme One", "nozzle_diameter": ["0.6"]})
    # user's leaf machine (host override) inheriting the 0.4 system variant
    _write(root / "user/42/machine/leaf.json",
           {"name": "My Acme", "from": "User", "inherits": "Acme One 0.4 nozzle", "print_host": "1.2.3.4"})
    # process/filament list the SYSTEM machine name in compatible_printers
    _write(root / "system/Acme/process/fine.json",
           {"name": "Fine @Acme", "type": "process", "compatible_printers": ["Acme One 0.4 nozzle"]})
    _write(root / "system/Acme/filament/pla.json",
           {"name": "PLA @Acme", "type": "filament", "compatible_printers": ["Acme One 0.4 nozzle"]})
    return root


def _index(tmp_path):
    return ProfileIndex(PresetResolver(str(_make_tree(tmp_path))))


def test_catalog_lists_real_machines_with_model_and_nozzle(tmp_path):
    cat = _index(tmp_path).machine_catalog()
    names = {c["name"] for c in cat}
    assert {"Acme One 0.4 nozzle", "Acme One 0.6 nozzle", "My Acme"} <= names
    assert "fdm_common" not in names  # base preset has no model/nozzle -> excluded
    m04 = next(c for c in cat if c["name"] == "Acme One 0.4 nozzle")
    assert m04["printer_model"] == "Acme One" and m04["nozzle"] == "0.4"
    assert m04["vendor"] == "Acme" and m04["source"] == "system"
    # user leaf inherits its vendor from the system ancestor
    leaf = next(c for c in cat if c["name"] == "My Acme")
    assert leaf["vendor"] == "Acme" and leaf["source"] == "user"


def test_leaf_machine_matches_profiles_listing_the_system_name(tmp_path):
    # The crux: process lists "Acme One 0.4 nozzle" but the printer is mapped to the
    # leaf "My Acme". Both resolve to (Acme One, 0.4) -> they match.
    prof = _index(tmp_path).compatible_profiles("My Acme")
    assert prof["print_profiles"] == ["Fine @Acme"]
    assert prof["filament_profiles"] == ["PLA @Acme"]


def test_system_machine_name_also_resolves(tmp_path):
    prof = _index(tmp_path).compatible_profiles("Acme One 0.4 nozzle")
    assert prof["print_profiles"] == ["Fine @Acme"]


def test_different_nozzle_is_not_compatible(tmp_path):
    # The 0.6 variant has no compatible process/filament in this fixture.
    prof = _index(tmp_path).compatible_profiles("Acme One 0.6 nozzle")
    assert prof == {"print_profiles": [], "filament_profiles": []}


def test_unknown_machine_returns_empty(tmp_path):
    prof = _index(tmp_path).compatible_profiles("Nonexistent")
    assert prof == {"print_profiles": [], "filament_profiles": []}


def test_rebuilds_when_user_presets_change(tmp_path):
    root = _make_tree(tmp_path)
    idx = ProfileIndex(PresetResolver(str(root)))
    assert idx.compatible_profiles("My Acme")["filament_profiles"] == ["PLA @Acme"]
    # add a new compatible user filament -> signature changes -> rebuild picks it up
    _write(root / "user/42/filament/petg.json",
           {"name": "PETG @Acme", "type": "filament", "compatible_printers": ["Acme One 0.4 nozzle"]})
    assert idx.compatible_profiles("My Acme")["filament_profiles"] == ["PETG @Acme", "PLA @Acme"]
