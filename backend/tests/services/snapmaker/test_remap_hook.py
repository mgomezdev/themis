import zipfile

from app.plugins.elegoo_centauri.client import ElegooCentauriClient
from app.services.providers.laminus.adapter import LaminusSlicingProvider
from app.services.slicer_service import slice_side_mapping
from app.plugins.snapmaker.client import SnapmakerExtendedClient


def _prepared(tmp_path):
    p = tmp_path / "prepared.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        z.writestr("Metadata/project_settings.config", "{}")
        z.writestr("Metadata/model_settings.config",
                   '<?xml version="1.0"?>\n<config><object id="1">'
                   '<metadata key="extruder" value="1"/></object></config>')
    return p


def test_snapmaker_needs_slice_side_mapping_and_the_slicing_provider_remaps_it(tmp_path):
    assert slice_side_mapping(SnapmakerExtendedClient(ip_address="1.2.3.4"), 2, None) == (2, None)
    p = _prepared(tmp_path)

    LaminusSlicingProvider("http://laminus.test").apply_tool_mapping(p, tool_index=2)     # what produce() runs on its copy

    with zipfile.ZipFile(p) as z:
        assert 'value="3"' in z.read("Metadata/model_settings.config").decode("utf-8")


def test_a_printer_that_does_not_need_slice_time_mapping_gets_none():
    assert slice_side_mapping(ElegooCentauriClient(ip_address="1.2.3.4"), 2, [{"model_filament": 1, "tool_index": 0}]) == (None, None)
    assert slice_side_mapping(None, 2, None) == (None, None)
