import pytest
from pydantic import ValidationError
from app.plugins.printer_topology import PrinterTopology


def topology(**over):
    return {'tools': [{'id': 'T0', 'nozzle_mm': .4}], 'positions': [{'id': 'S0'}], 'swapping': False, **over}


def test_capacity_and_duplicates_are_not_loaded_or_unique_material_counts():
    filament = {'type': 'PLA', 'subtype': 'Silk'}
    t = PrinterTopology.model_validate(topology(positions=[{'id': 'a', 'filament': filament}, {'id': 'b', 'filament': filament}, {'id': 'empty'}], swapping=True))
    assert len(t.positions) == 3 and t.positions[2].filament is None
    assert t.positions[0].filament == t.positions[1].filament


def test_each_tool_has_its_own_size():
    t = PrinterTopology.model_validate(topology(tools=[{'id': 'a', 'nozzle_mm': .4}, {'id': 'b', 'nozzle_mm': .6}], positions=[{'id': 'a'}, {'id': 'b'}], swapping=True))
    assert [x.nozzle_mm for x in t.tools] == [.4, .6]


@pytest.mark.parametrize('over', [
    {'tools': []}, {'positions': []},
    {'tools': [{'id': 'a', 'nozzle_mm': .4}, {'id': 'b', 'nozzle_mm': .6}]},
    {'tools': [{'id': 'a'}]}, {'tools': [{'id': 'a', 'nozzle_mm': 0}]},
    {'tools': [{'id': 'a', 'nozzle_mm': float('nan')}]},
    {'positions': [{'id': 'a'}, {'id': 'a'}], 'swapping': True},
    {'positions': [{'id': 'a'}, {'id': 'b'}]},
])
def test_invalid_contract_rejected(over):
    with pytest.raises(ValidationError):
        PrinterTopology.model_validate(topology(**over))


def test_registration_requires_live_topology_implementation():
    from app.plugins import register_plugin
    from app.plugins.manifest import PluginError
    from app.services.abstract_printer_client import AbstractPrinterClient
    from tests.services.fake_printer_plugin import FakeClient
    from tests.plugins.dummy_plugin import make_manifest
    class MissingTopology(FakeClient):
        refresh_topology = AbstractPrinterClient.refresh_topology
    with pytest.raises(PluginError, match='topology'):
        register_plugin(make_manifest('no_topology', factory=MissingTopology))


def test_core_contract_does_not_assume_gcode_or_artifact_support():
    from app.services.abstract_printer_client import AbstractPrinterClient
    assert AbstractPrinterClient.raw_gcode_supported is False
    assert AbstractPrinterClient.print_artifact_kind is None


def test_printer_registration_requires_declared_artifact_support():
    from app.plugins import register_plugin
    from app.plugins.manifest import PluginError
    from tests.services.fake_printer_plugin import FakeClient
    from tests.plugins.dummy_plugin import make_manifest
    class NoArtifact(FakeClient):
        print_artifact_kind = None
    with pytest.raises(PluginError, match='artifact'):
        register_plugin(make_manifest('no_artifact', factory=NoArtifact))


def test_two_plugins_cannot_own_the_same_legacy_printer_alias():
    from app.plugins import register_plugin, load_bundled
    from app.plugins.manifest import PluginError
    from tests.services.fake_printer_plugin import FakeClient
    from tests.plugins.dummy_plugin import make_manifest
    load_bundled()
    with pytest.raises(PluginError, match='alias is already owned'):
        register_plugin(make_manifest('alias_collision', factory=FakeClient,
                                      legacy_printer_types={'bambu': ('fake', 'printer')}))
