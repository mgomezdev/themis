import zipfile
from app.services.snapmaker_client import SnapmakerExtendedClient
from app.services.elegoo_centauri_client import ElegooCentauriClient


def _prepared(tmp_path):
    p = tmp_path / "prepared.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        z.writestr("Metadata/project_settings.config", "{}")
        z.writestr("Metadata/model_settings.config",
                   '<?xml version="1.0"?>\n<config><object id="1">'
                   '<metadata key="extruder" value="1"/></object></config>')
    return p


def test_snapmaker_hook_remaps(tmp_path):
    c = SnapmakerExtendedClient(ip_address="1.2.3.4")
    p = _prepared(tmp_path)
    c.remap_sliceable_3mf(p, tool_index=2)
    with zipfile.ZipFile(p) as z:
        assert 'value="3"' in z.read("Metadata/model_settings.config").decode("utf-8")


def test_non_snapmaker_hook_is_noop(tmp_path):
    c = ElegooCentauriClient(ip_address="1.2.3.4")
    p = _prepared(tmp_path)
    before = p.read_bytes()
    c.remap_sliceable_3mf(p, tool_index=2)
    assert p.read_bytes() == before
