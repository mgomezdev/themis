import zipfile

from app.plugins.elegoo_centauri.client import ElegooCentauriClient
from app.services.providers.laminus.adapter import LaminusSlicingProvider
from app.services.slicer_service import tool_mapping_hook
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


def test_snapmaker_hook_remaps_through_the_slicing_provider(tmp_path):
    provider = LaminusSlicingProvider("http://laminus.test")
    hook = tool_mapping_hook(SnapmakerExtendedClient(ip_address="1.2.3.4"), 2, None, provider)
    p = _prepared(tmp_path)

    hook(p)

    with zipfile.ZipFile(p) as z:
        assert 'value="3"' in z.read("Metadata/model_settings.config").decode("utf-8")


def test_a_printer_that_does_not_need_slice_time_mapping_gets_no_hook():
    provider = LaminusSlicingProvider("http://laminus.test")
    assert tool_mapping_hook(ElegooCentauriClient(ip_address="1.2.3.4"), 2, None, provider) is None
