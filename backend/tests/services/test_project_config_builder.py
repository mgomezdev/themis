from app.services.project_config_builder import build_project_config


def _machine():
    return {
        "name": "TestMachine", "type": "machine", "from": "User",
        "printer_model": "TestPrinter",
        "nozzle_diameter": "0.6",          # scalar -> reference wants array
        "zzz_not_a_real_key": ["dropme"],  # not in schema -> dropped
    }


def _process():
    return {
        "name": "TestProcess", "type": "process",
        "layer_height": ["0.3"],           # list -> reference wants scalar
        "sparse_infill_density": "50%",
        "enable_support": "1",
    }


def _filament(material="PETG", name="MyFil"):
    return {"name": name, "type": "filament", "filament_type": [material]}


def test_coerces_types_to_reference_schema():
    cfg = build_project_config(_machine(), _process(), [_filament()], ["#00FF00"])
    assert cfg["nozzle_diameter"] == ["0.6"]   # scalar coerced to array
    assert cfg["layer_height"] == "0.3"        # list coerced to scalar
    assert cfg["sparse_infill_density"] == "50%"
    assert cfg["enable_support"] == "1"


def test_drops_keys_not_in_reference_schema():
    cfg = build_project_config(_machine(), _process(), [_filament()])
    assert "zzz_not_a_real_key" not in cfg     # would segfault the CLI otherwise


def test_identity_and_compatibility():
    cfg = build_project_config(_machine(), _process(), [_filament()], ["#00FF00"])
    assert cfg["printer_model"] == "TestPrinter"
    assert cfg["printer_settings_id"] == "TestMachine"   # preset name
    assert cfg["print_settings_id"] == "TestProcess"
    assert cfg["compatible_printers"] == []              # embedded path sets active printer
    assert cfg["filament_colour"] == ["#00FF00"]


def test_filament_per_slot_arrays():
    cfg = build_project_config(
        _machine(), _process(),
        [_filament("PLA", "A"), _filament("PETG", "B"), _filament("ABS", "C")],
        ["#111111", "#222222", "#333333"],
    )
    assert cfg["filament_type"] == ["PLA", "PETG", "ABS"]
    assert cfg["filament_settings_id"] == ["A", "B", "C"]
    assert cfg["filament_colour"] == ["#111111", "#222222", "#333333"]


def test_requires_at_least_one_filament():
    import pytest
    with pytest.raises(ValueError):
        build_project_config(_machine(), _process(), [])


def test_multi_extruder_expansion():
    machine = {
        "name": "MultiExtruderMachine",
        "type": "machine",
        "printer_model": "MultiExtruder",
        "nozzle_diameter": ["0.4", "0.4", "0.4"],  # 3 extruders
        "nozzle_volume": "120",  # scalar coerced to ["120"] of length 1, then expanded to 3
    }
    process = {
        "name": "TestProcess",
        "type": "process",
        "layer_height": "0.2",
    }
    # 1 filament supplied, but printer has 3 extruders
    filaments = [{"name": "MyFil", "type": "filament", "filament_type": ["PLA"]}]

    cfg = build_project_config(machine, process, filaments)

    # Verify arrays are expanded to length 3
    assert cfg["nozzle_diameter"] == ["0.4", "0.4", "0.4"]
    assert cfg["nozzle_volume"] == ["120", "120", "120"]
    assert cfg["nozzle_volume_type"] == ["Standard", "Standard", "Standard"]  # defaulted & expanded
    assert cfg["filament_type"] == ["PLA", "PLA", "PLA"]  # filament type expanded
