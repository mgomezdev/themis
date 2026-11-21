"""Core can consume contracts, never transport/codec implementations."""
from pathlib import Path
import ast


def test_core_cannot_import_printer_implementations():
    root = Path(__file__).parents[1] / 'app'
    violations = []
    for p in root.rglob('*.py'):
        if 'plugins' in p.relative_to(root).parts or 'providers/laminus/' in p.relative_to(root).as_posix():
            continue
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.ImportFrom) and any(part in (node.module or '').split('.') for part in ['moonraker', 'snapmaker', '_protocols']):
                violations.append(f'{p.relative_to(root)}:{node.lineno}')
    assert violations == []


def test_queue_never_encodes_vendor_tray_mapping():
    root = Path(__file__).parents[1] / 'app'
    for name in ['services/queue_engine.py', 'services/queue/printing.py', 'services/queue/slicing.py']:
        tree = ast.parse((root / name).read_text())
        assert not any(isinstance(n, ast.Constant) and n.value == 'ams_tray_id' for n in ast.walk(tree)), name


def test_core_contract_contains_no_gcode_command_recipes():
    root = Path(__file__).parents[1] / 'app'
    tree = ast.parse((root / 'services/abstract_printer_client.py').read_text())
    commands = {'G28', 'G90', 'G91', 'M211 S0', 'M211 S1'}
    assert not any(isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in commands for n in ast.walk(tree))


def test_core_identity_has_no_manufacturer_mapping_table():
    root = Path(__file__).parents[1] / 'app'
    tree = ast.parse((root / 'services/printer_identity.py').read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            assert not any(isinstance(key, ast.Constant) and key.value in {'bambu', 'elegoo_centauri', 'snapmaker_extended'} for key in node.keys)


def test_core_filament_events_and_capabilities_use_neutral_contracts():
    from dataclasses import fields
    from app.services.abstract_printer_client import PrinterCapabilities
    assert 'ams' not in {field.name for field in fields(PrinterCapabilities)}
    root = Path(__file__).parents[1] / 'app'
    for name in ['services/printer_events.py', 'services/printer_manager.py']:
        tree = ast.parse((root / name).read_text())
        assert not any(isinstance(n, ast.Name) and n.id == 'AmsChanged' for n in ast.walk(tree)), name
