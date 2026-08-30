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

    import app.api.routes.laminus as _laminus
    _laminus._catalog_dict = _catalog()

    mock_client = MagicMock()
    mock_client.slice_start.return_value = "job1"
    mock_client.poll_status.return_value = {"sliced_file": "out.gcode"}
    mock_client.download.return_value = tmp_path / "gcode" / "1" / "out.gcode"

    with patch("app.config.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch("app.services.laminus_sidecar_client.LaminusSidecarClient", return_value=mock_client):
        svc.slice(_req(source_3mf=str(source), prepare_hook=hook))

    assert len(hooked_paths) == 1
    assert hooked_paths[0] != source, "prepare_hook must run on a copy, not the shared source file"
    assert source.read_bytes() == b"original-bytes", "shared library source must not be mutated"

    passed_source = Path(mock_client.slice_start.call_args[0][0])
    assert passed_source == hooked_paths[0], "the remapped copy must be what gets sliced"


def test_no_hook_with_sidecar_succeeds(tmp_path):
    """A request without a prepare_hook routes to sidecar successfully."""
    svc = SlicerService.__new__(SlicerService)
    svc._data_dir = tmp_path

    import app.api.routes.laminus as _laminus
    _laminus._catalog_dict = _catalog()

    with patch("app.config.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch.object(SlicerService, "_execute_slice_by_ids", return_value="out.gcode"):
        assert svc.slice(_req()) == "out.gcode"
