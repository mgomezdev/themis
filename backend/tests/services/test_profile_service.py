import json
import pytest
from pathlib import Path
from app.services.profile_service import ProfileService


def _write_preset(directory: Path, filename: str, name: str, compatible: list[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(json.dumps({
        "name": name,
        "compatible_printers": compatible,
    }))


def test_get_printer_presets_from_system(tmp_path):
    machine_dir = tmp_path / "system" / "Bambu Lab" / "machine"
    _write_preset(machine_dir, "P1S_0.4.json", "Bambu Lab P1S 0.4 nozzle", [])
    _write_preset(machine_dir, "P1S_0.2.json", "Bambu Lab P1S 0.2 nozzle", [])
    svc = ProfileService(orca_config_dir=str(tmp_path))
    names = svc.get_printer_preset_names()
    assert "Bambu Lab P1S 0.4 nozzle" in names
    assert "Bambu Lab P1S 0.2 nozzle" in names


def test_get_printer_presets_from_user_dir(tmp_path):
    user_dir = tmp_path / "user" / "default" / "machine"
    _write_preset(user_dir, "custom.json", "My Custom Printer", [])
    svc = ProfileService(orca_config_dir=str(tmp_path))
    assert "My Custom Printer" in svc.get_printer_preset_names()


def test_get_print_profiles_filters_by_printer(tmp_path):
    proc_dir = tmp_path / "user" / "default" / "process"
    _write_preset(proc_dir, "fast.json", "0.20mm Standard", ["Bambu Lab P1S 0.4 nozzle"])
    _write_preset(proc_dir, "fine.json", "0.10mm Fine", ["Bambu Lab P1S 0.2 nozzle"])
    svc = ProfileService(orca_config_dir=str(tmp_path))
    profiles = svc.get_compatible_profiles("Bambu Lab P1S 0.4 nozzle")
    assert "0.20mm Standard" in profiles["print_profiles"]
    assert "0.10mm Fine" not in profiles["print_profiles"]


def test_get_filament_profiles_filters_by_printer(tmp_path):
    fil_dir = tmp_path / "user" / "default" / "filament"
    _write_preset(fil_dir, "pla.json", "Bambu PLA Basic", ["Bambu Lab P1S 0.4 nozzle"])
    _write_preset(fil_dir, "abs.json", "Generic ABS", ["Other Printer"])
    svc = ProfileService(orca_config_dir=str(tmp_path))
    profiles = svc.get_compatible_profiles("Bambu Lab P1S 0.4 nozzle")
    assert "Bambu PLA Basic" in profiles["filament_profiles"]
    assert "Generic ABS" not in profiles["filament_profiles"]


def test_empty_config_dir_returns_empty(tmp_path):
    svc = ProfileService(orca_config_dir=str(tmp_path))
    assert svc.get_printer_preset_names() == []
    result = svc.get_compatible_profiles("anything")
    assert result == {"print_profiles": [], "filament_profiles": []}


def test_malformed_json_skipped(tmp_path):
    proc_dir = tmp_path / "user" / "default" / "process"
    proc_dir.mkdir(parents=True, exist_ok=True)
    (proc_dir / "bad.json").write_text("{not valid json")
    _write_preset(proc_dir, "good.json", "Good Profile", ["My Printer"])
    svc = ProfileService(orca_config_dir=str(tmp_path))
    profiles = svc.get_compatible_profiles("My Printer")
    assert "Good Profile" in profiles["print_profiles"]
