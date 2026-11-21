from unittest.mock import Mock
import pytest
from app.plugins.bambu.client import BambuMQTTClient
from app.plugins.elegoo_centauri.client import ElegooCentauriClient
from app.plugins.snapmaker.client import SnapmakerExtendedClient
from app.plugins.moonraker.client import MoonrakerClient


def test_bambu_counts_empty_trays_and_live_ams_expansion_without_reconnect():
    c = BambuMQTTClient('x', 's', 'k')
    c.connect = Mock(); c.disconnect = Mock()
    report = {'print': {'ams': {'ams': [{'id': '0', 'tray': [{'id': '0', 'tray_type': 'PLA'}, {'id': '1'}, {'id': '2'}, {'id': '3'}]}]}, 'nozzle_diameter': '0.4'}}
    c.request_status_update = Mock(side_effect=lambda: c._handle_message(report))
    a = c.refresh_topology({})
    assert len(a.positions) == 4 and a.positions[1].filament is None
    report['print']['ams']['ams'].append({'id': '1', 'tray': [{'id': str(i)} for i in range(4)]})
    report['print']['nozzle_diameter'] = '0.6'
    b = c.refresh_topology({})
    assert len(b.positions) == 8 and b.tools[0].nozzle_mm == .6
    assert b.positions[0].id == a.positions[0].id
    c.connect.assert_not_called(); c.disconnect.assert_not_called()


def test_elegoo_explicit_live_nozzle_configuration_no_guess():
    c = ElegooCentauriClient('x')
    c.request_status_update = Mock()
    with pytest.raises(ValueError):
        c.refresh_topology({})
    t = c.refresh_topology({'nozzle_sizes_mm': [.6], 'positions': [{'id': 'spool', 'filament': {'type': 'PETG'}}]})
    assert t.tools[0].nozzle_mm == .6 and len(t.positions) == 1


@pytest.mark.parametrize('cls, count', [(MoonrakerClient, 2), (SnapmakerExtendedClient, 4)])
def test_moonraker_refresh_reads_current_config_and_each_nozzle(cls, count, mocker):
    c = cls('x')
    names = ['extruder' if i == 0 else f'extruder{i}' for i in range(count)]
    settings = {name: {'nozzle_diameter': .4 + i * .1} for i, name in enumerate(names)}
    settings.update({f'gcode_macro T{i}': {} for i in range(count)})
    status = {'configfile': {'settings': settings}, 'print_task_config': {}}
    mocker.patch('httpx.get', return_value=Mock(json=lambda: {'result': {'status': status}}, raise_for_status=Mock()))
    t = c.refresh_topology({})
    assert len(t.tools) == count and len(t.positions) == count
    assert list(c._extruders) == names
    assert t.tools[-1].nozzle_mm == .4 + (count - 1) * .1


def test_generic_multi_position_requires_real_tool_selection_support(mocker):
    c = MoonrakerClient('x')
    settings = {'extruder': {'nozzle_diameter': .4}, 'extruder1': {'nozzle_diameter': .6}}
    mocker.patch('httpx.get', return_value=Mock(json=lambda: {'result': {'status': {'configfile': {'settings': settings}}}}, raise_for_status=Mock()))
    with pytest.raises(ValueError, match='tool-selection'):
        c.refresh_topology({})


def test_reported_filaments_keep_hardware_authority_after_topology_refresh():
    c = BambuMQTTClient('x', 's', 'k')
    c.request_status_update = Mock(side_effect=lambda: c._handle_message({'print': {'nozzle_diameter': '.4', 'ams': {'ams': [{'id': 0, 'tray': [{'id': 0, 'tray_type': 'PLA'}]}]}}}))
    t = c.refresh_topology({})
    assert t.positions[0].reported is True


def test_virtual_printer_honors_live_manual_nozzle_sizes():
    from app.plugins.mock.client import MockPrinterClient
    c = MockPrinterClient()
    t = c.refresh_topology({'nozzle_sizes_mm': [.6]})
    assert t.tools[0].nozzle_mm == .6
