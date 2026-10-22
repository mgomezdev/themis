"""BIZ-291: the manual slices (verify-slice, complete-manually) use the printer's slicer, like the queue does."""
from types import SimpleNamespace
from unittest.mock import patch

from app.api.routes.jobs import _build_slice_request
from app.models import Job, JobPrinterConfig, Printer, UploadedFile


def _rows(preferred):
    printer = Printer(id=1, name="P", printer_type="bambu", connection_config={}, machine_profile_override="M",
                      loaded_filaments=[{"slot": 0, "type": "PLA", "color": "#000000", "filament_profile": "PLA"}],
                      preferred_slicer_plugin_id=preferred)
    job = Job(id=7, plate_number=1, overrides={})
    config = JobPrinterConfig(job_id=7, printer_id=1, print_profile="0.20mm", filament_type="PLA", filament_color="#000000")
    return job, config, printer, UploadedFile(original_filename="m.3mf", relative_path="Prints/m.3mf")


def _build(preferred, default="a"):
    seen = []

    def fake_for(pid):
        seen.append(pid)
        return None
    with patch("app.services.providers.slicing.default_provider_id", return_value=default), \
            patch("app.api.routes.jobs.slicing_provider_for", side_effect=fake_for):
        return _build_slice_request(*_rows(preferred), "Machine 0.4"), seen


def test_a_printer_with_a_preferred_slicer_slices_manually_on_it():
    req, seen = _build("b")
    assert req.provider_id == "b" and seen == ["b"]


def test_without_a_preference_the_default_slicer_is_used():
    req, seen = _build(None, default="a")
    assert req.provider_id == "a" and seen == ["a"]
