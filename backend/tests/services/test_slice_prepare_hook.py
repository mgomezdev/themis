"""Slice-side tool mapping (BIZ-295): `produce()` remaps a job-scoped copy through the slicing provider, never the shared source."""
from pathlib import Path

from app.services.slicer_service import SliceRequest, SlicerService
from tests.catalog_helpers import prime_catalog
from tests.fake_providers import FakeSlicingProvider
from tests.slicing_env import slicing_provider


def _req(**kw):
    base = dict(job_id=1, source_3mf="x.stl", plate_number=1, machine_preset="M",
                process_preset="P", filament_presets=["F"])
    base.update(kw)
    return SliceRequest(**base)


def _catalog():
    return {
        "machine": [{"name": "M", "uuid": "m1"}],
        "process": [{"name": "P", "uuid": "p1"}],
        "filament": [{"name": "F", "uuid": "f1"}],
    }


class RemappingProvider(FakeSlicingProvider):
    TOOL_MAPPING = True

    def apply_tool_mapping(self, source_3mf, *, tool_index=None, filament_map=None):
        self.calls.append(("apply_tool_mapping", Path(source_3mf), tool_index, filament_map))
        Path(source_3mf).write_bytes(b"remapped-bytes")


def _svc(tmp_path) -> SlicerService:
    svc = SlicerService.__new__(SlicerService)
    svc._data_dir = tmp_path
    return svc


def test_tool_mapping_runs_on_a_copy_and_the_copy_is_what_gets_sliced(tmp_path):
    source = tmp_path / "x.3mf"
    source.write_bytes(b"original-bytes")
    prime_catalog(_catalog())
    fake = RemappingProvider()
    with slicing_provider(fake):
        _svc(tmp_path).slice(_req(source_3mf=str(source), tool_index=2))

    (applied,) = [c for c in fake.calls if c[0] == "apply_tool_mapping"]
    assert applied[1] != source and applied[2:] == (2, None)
    assert source.read_bytes() == b"original-bytes", "shared library source must not be mutated"
    passed = next(c[1] for c in fake.calls if c[0] == "slice").source_file
    assert passed == applied[1] and passed.read_bytes() == b"remapped-bytes"


def test_a_filament_map_reaches_the_provider_in_its_dict_shape(tmp_path):
    source = tmp_path / "x.3mf"
    source.write_bytes(b"original-bytes")
    prime_catalog(_catalog())
    fake = RemappingProvider()
    fmap = [{"model_filament": 1, "tool_index": 2}, {"model_filament": 2, "tool_index": 0}]
    with slicing_provider(fake):
        _svc(tmp_path).slice(_req(source_3mf=str(source), filament_map=fmap))
    (applied,) = [c for c in fake.calls if c[0] == "apply_tool_mapping"]
    assert applied[2:] == (None, fmap)


def test_no_mapping_slices_the_source_itself(tmp_path):
    source = tmp_path / "x.3mf"
    source.write_bytes(b"original-bytes")
    prime_catalog(_catalog())
    fake = RemappingProvider()
    with slicing_provider(fake):
        _svc(tmp_path).slice(_req(source_3mf=str(source)))
    assert not [c for c in fake.calls if c[0] == "apply_tool_mapping"]
    assert next(c[1] for c in fake.calls if c[0] == "slice").source_file == source
