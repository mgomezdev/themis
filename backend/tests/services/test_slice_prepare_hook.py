from tests.fake_providers import FakeSlicingProvider
from tests.catalog_helpers import cached_raw, prime_catalog
from pathlib import Path
from unittest.mock import patch, MagicMock
from app.services.slicer_service import SlicerService, SliceRequest


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


def test_prepare_hook_runs_on_copy_leaves_source_untouched(tmp_path):
    """prepare_hook must rewrite a job-scoped copy, never the shared library source file."""
    svc = SlicerService.__new__(SlicerService)
    svc._data_dir = tmp_path

    source = tmp_path / "x.3mf"
    source.write_bytes(b"original-bytes")

    hooked_paths = []

    def hook(p):
        hooked_paths.append(Path(p))
        Path(p).write_bytes(b"remapped-bytes")
    prime_catalog(_catalog())

    fake = FakeSlicingProvider()
    with patch("app.services.slicer_service.get_slicing_provider", return_value=fake):
        svc.slice(_req(source_3mf=str(source), prepare_hook=hook))

    assert len(hooked_paths) == 1
    assert hooked_paths[0] != source, "prepare_hook must run on a copy, not the shared source file"
    assert source.read_bytes() == b"original-bytes", "shared library source must not be mutated"

    passed_source = next(c[1] for c in fake.calls if c[0] == "slice").source_file
    assert passed_source == hooked_paths[0], "the remapped copy must be what gets sliced"


def test_no_hook_with_sidecar_succeeds(tmp_path):
    """A request without a prepare_hook routes to sidecar successfully."""
    svc = SlicerService.__new__(SlicerService)
    svc._data_dir = tmp_path
    prime_catalog(_catalog())

    with patch("app.config.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch.object(SlicerService, "_execute_slice_by_ids", return_value="out.gcode"):
        assert svc.slice(_req()) == "out.gcode"
