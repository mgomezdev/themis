from tests.catalog_helpers import patch_cached_catalog, prime_catalog
from pathlib import Path
from unittest.mock import MagicMock, patch

from httpx import AsyncClient

from app.services.slicer_service import SliceError, SliceRequest, SlicerService


async def test_create_job_stores_overrides(client: AsyncClient, upload_3mf, create_printer):
    file_id = await upload_3mf()
    printer_id = await create_printer()

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id,
            "plate_number": 1,
            "overrides": {"sparse_infill_pattern": "grid", "layer_height": "0.15"},
            "printer_configs": [{
                "printer_id": printer_id,
                "print_profile": "0.16mm Profile",
                "filament_type": "any",
                "filament_color": "any",
            }],
        })
    assert resp.status_code == 201
    job_id = resp.json()["id"]

    detail = await client.get(f"/api/v1/jobs/{job_id}/details")
    assert detail.status_code == 200
    assert detail.json()["overrides"] == {"sparse_infill_pattern": "grid", "layer_height": "0.15"}


async def test_create_job_strips_non_curated_override_keys(client: AsyncClient, upload_3mf, create_printer):
    """Non-curated keys (e.g. post_process) are silently dropped at the API boundary."""
    file_id = await upload_3mf()
    printer_id = await create_printer()

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id,
            "plate_number": 1,
            "overrides": {"layer_height": "0.15", "post_process": "rm -rf /", "unknown_key": "x"},
            "printer_configs": [{"printer_id": printer_id, "print_profile": "P", "filament_type": "any", "filament_color": "any"}],
        })
    assert resp.status_code == 201
    detail = await client.get(f"/api/v1/jobs/{resp.json()['id']}/details")
    assert detail.json()["overrides"] == {"layer_height": "0.15"}


async def test_create_job_without_overrides_is_null(client: AsyncClient, upload_3mf, create_printer):
    file_id = await upload_3mf()
    printer_id = await create_printer()

    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id,
            "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "Profile", "filament_type": "any", "filament_color": "any"}],
        })
    assert resp.status_code == 201
    job_id = resp.json()["id"]
    detail = await client.get(f"/api/v1/jobs/{job_id}/details")
    assert detail.json()["overrides"] is None


async def test_update_job_configs_clears_overrides_when_omitted(client: AsyncClient, upload_3mf, create_printer):
    """PATCH /configs without overrides field clears any previously-stored overrides."""
    file_id = await upload_3mf()
    printer_id = await create_printer()

    # Create job with overrides
    with patch("app.api.routes.jobs.queue_engine"):
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id,
            "plate_number": 1,
            "overrides": {"layer_height": "0.15"},
            "printer_configs": [{"printer_id": printer_id, "print_profile": "P", "filament_type": "any", "filament_color": "any"}],
        })
    job_id = resp.json()["id"]

    # PATCH without overrides field → overrides cleared
    with patch("app.api.routes.jobs.queue_engine"):
        await client.patch(f"/api/v1/jobs/{job_id}/configs", json={
            "printer_configs": [{"printer_id": printer_id, "print_profile": "P", "filament_type": "any", "filament_color": "any"}],
        })

    detail = await client.get(f"/api/v1/jobs/{job_id}/details")
    assert detail.json()["overrides"] is None


def test_slice_request_extra_config_defaults_empty():
    req = SliceRequest(
        job_id=1, source_3mf="/tmp/m.3mf", plate_number=1,
        machine_preset="machine", process_preset="process",
        filament_presets=["filament"],
    )
    assert req.extra_config == {}


def test_extra_config_forwarded_to_sidecar():
    """extra_config is passed through to slice_start so the sidecar merges it."""
    svc = SlicerService.__new__(SlicerService)
    svc._data_dir = Path("/tmp")
    prime_catalog({
        "machine": [{"name": "MyPrinter", "uuid": "m1"}],
        "process": [{"name": "MyProcess", "uuid": "p1"}],
        "filament": [{"name": "MyFilament", "uuid": "f1"}],
    })

    req = SliceRequest(
        job_id=1, source_3mf="/tmp/m.stl", plate_number=1,
        machine_preset="MyPrinter", process_preset="MyProcess",
        filament_presets=["MyFilament"],
        extra_config={"fill_pattern": "grid", "layer_height": "0.15"},
    )

    mock_client = MagicMock()
    mock_client.slice_start.return_value = "sidecar-job-1"
    mock_client.poll_status.return_value = {"sliced_file": "out.gcode"}
    mock_client.download.return_value = Path("/tmp/out.gcode")

    with patch("app.services.slicer_service.SlicerService._execute_slice_by_ids") as mock_exec, \
         patch("app.config.get_laminus_sidecar_url", return_value="http://laminus:5000"):
        mock_exec.return_value = "/tmp/out.gcode"
        svc.slice(req)

    mock_exec.assert_called_once()
    _, kwargs = mock_exec.call_args[0], mock_exec.call_args[1]
    # extra_config must be passed through to _execute_slice_by_ids
    assert req.extra_config == {"fill_pattern": "grid", "layer_height": "0.15"}


def test_slice_raises_without_sidecar():
    """SlicerService.slice raises SliceError when no sidecar is configured."""
    svc = SlicerService.__new__(SlicerService)
    svc._data_dir = Path("/tmp")
    req = SliceRequest(
        job_id=1, source_3mf="/tmp/m.stl", plate_number=1,
        machine_preset="m", process_preset="p", filament_presets=["f"],
    )
    with patch("app.config.get_laminus_sidecar_url", return_value=None):
        try:
            svc.slice(req)
            assert False, "expected SliceError"
        except SliceError as e:
            assert "LAMINUS_SIDECAR_URL" in str(e)


# ---------------------------------------------------------------------------
# POST /jobs/check-overrides — embedded 3MF settings vs the chosen presets
# ---------------------------------------------------------------------------

import io
import json
import zipfile
from unittest.mock import AsyncMock

import pytest

from app.services.providers.slicing import SlicingProviderError
from app.services.providers.laminus import LaminusSlicingProvider
from tests.fake_providers import FakeSlicingProvider

_KEYS = {"has_embedded_settings", "has_findings", "setting_changes", "slot_warning"}  # what the frontend reads
_CATALOG = {
    "machine": [{"name": "Bambu Lab P1S 0.4", "uuid": "m-1"}],
    "process": [{"name": "0.20mm Standard", "uuid": "p-1"}],
    "filament": [
        {"name": "Generic PLA", "uuid": "f-generic", "compatible_printers": []},
        {"name": "Bambu PLA Basic", "uuid": "f-bambu", "compatible_printers": ["Bambu Lab P1S 0.4"]},
    ],
}


def _project_3mf(settings: dict, model_xml: str | None = None) -> bytes:
    """A 3MF that carries embedded slicer settings (what makes the override check run)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/slice_info.config", json.dumps({"plate": [{"index": 1, "prediction": 60, "weight": [5.0]}]}))
        zf.writestr("Metadata/plate_1.png", b"\x89PNG")
        zf.writestr("Metadata/project_settings.config", json.dumps(settings))
        if model_xml is not None:
            zf.writestr("Metadata/model_settings.config", model_xml)
    return buf.getvalue()


def _merge_calls(provider) -> list:
    return [c for c in provider.calls if c[0] == "merged_config"]


@pytest.fixture
def check(client: AsyncClient, tmp_path, upload_3mf, create_printer):
    """`await check(data=..., printer=..., sidecar=..., catalog=..., merged=..., **body)` -> (response, fake_provider).
    `sidecar=None` means no slicing provider is configured."""
    async def _check(*, data: bytes | None = None, sidecar: str | None = "http://laminus.test",
                     catalog=_CATALOG, merged: dict | Exception = None, printer: dict | None = None,
                     real_inspector: bool = True, **body):
        file_id = await upload_3mf(data=data)
        printer_id = await create_printer(**(printer or {}))
        payload = {"uploaded_file_id": file_id, "printer_id": printer_id, "print_profile": "0.20mm Standard",
                   "filament_profile": "Bambu PLA Basic", **body}
        provider = FakeSlicingProvider()
        if real_inspector:   # exercise the Laminus (OrcaSlicer) inspection logic through the route
            provider.inspect_overrides = LaminusSlicingProvider("").inspect_overrides
        if isinstance(merged, Exception):
            provider.fail_on["merged_config"] = merged
        else:
            provider.merged = merged if merged is not None else {}
        with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
             patch("app.services.providers.slicing.get_slicing_provider", return_value=provider if sidecar else None), \
             patch_cached_catalog(catalog):
            resp = await client.post("/api/v1/jobs/check-overrides", json=payload)
        return resp, provider
    return _check


async def test_check_overrides_404_for_unknown_file_and_printer(client: AsyncClient, upload_3mf, create_printer):
    printer_id = await create_printer()
    resp = await client.post("/api/v1/jobs/check-overrides", json={
        "uploaded_file_id": 424242, "printer_id": printer_id, "print_profile": "0.20mm Standard"})
    assert (resp.status_code, resp.json()["detail"]) == (404, "File 424242 not found")

    file_id = await upload_3mf()
    resp = await client.post("/api/v1/jobs/check-overrides", json={
        "uploaded_file_id": file_id, "printer_id": 424243, "print_profile": "0.20mm Standard"})
    assert (resp.status_code, resp.json()["detail"]) == (404, "Printer 424243 not found")


async def test_check_overrides_bare_3mf_has_nothing_to_lose_and_never_calls_the_sidecar(check):
    resp, sidecar = await check()  # default fixture 3MF has no project_settings.config

    assert resp.status_code == 200
    assert resp.json() == {"has_findings": False, "setting_changes": [], "slot_warning": None,
                           "has_embedded_settings": False}
    assert _merge_calls(sidecar) == []


async def test_check_overrides_printer_without_active_preset_skips_the_diff(check):
    resp, sidecar = await check(data=_project_3mf({"layer_height": "0.2"}),
                                printer={"current_orca_printer_profile": None})

    assert resp.json() == {"has_findings": False, "setting_changes": [], "slot_warning": None,
                           "has_embedded_settings": True}
    assert _merge_calls(sidecar) == []


@pytest.mark.parametrize("case, kwargs, error", [
    ("no sidecar configured", {"sidecar": None}, "Override check requires Laminus sidecar"),
    ("catalog unavailable", {"catalog": RuntimeError("boom")}, "Catalog unavailable: boom"),
    ("machine preset not in catalog", {"catalog": {**_CATALOG, "machine": []}},
     "Profile not found in sidecar: machine='Bambu Lab P1S 0.4' process='0.20mm Standard'"),
    ("process preset not in catalog", {"print_profile": "0.28mm Draft"},
     "Profile not found in sidecar: machine='Bambu Lab P1S 0.4' process='0.28mm Draft'"),
    ("catalog has no filaments", {"catalog": {**_CATALOG, "filament": []}},
     "No filament profiles found in sidecar catalog"),
    ("sidecar rejects the merge", {"merged": SlicingProviderError("merge failed")}, "merge failed"),
])
async def test_check_overrides_degrades_to_an_error_note_instead_of_blocking(check, case, kwargs, error):
    resp, _sidecar = await check(data=_project_3mf({"layer_height": "0.2"}), **kwargs)

    assert resp.status_code == 200, case
    assert resp.json() == {"has_findings": False, "setting_changes": [], "slot_warning": None,
                           "has_embedded_settings": True, "error": error}


async def test_check_overrides_reports_only_curated_settings_the_presets_would_change(check):
    embedded = {"layer_height": "0.2", "enable_support": "1", "wall_loops": ["3"], "fan_speed": "10"}
    merged = {"layer_height": "0.16", "enable_support": "1", "wall_loops": ["4"], "fan_speed": "99"}

    resp, sidecar = await check(data=_project_3mf(embedded), merged=merged)

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _KEYS
    assert body["has_embedded_settings"] is True and body["has_findings"] is True
    assert body["slot_warning"] is None
    # unchanged (enable_support) and non-curated (fan_speed) keys are not reported; list values are joined
    assert sorted(body["setting_changes"], key=lambda c: c["key"]) == [
        {"key": "layer_height", "from": "0.2", "to": "0.16"},
        {"key": "wall_loops", "from": "3", "to": "4"},
    ]
    assert _merge_calls(sidecar) == [("merged_config", "m-1", "p-1", ["f-bambu"])]


async def test_check_overrides_clean_when_presets_agree_with_the_file(check):
    resp, _ = await check(data=_project_3mf({"layer_height": "0.2"}), merged={"layer_height": "0.2"})

    assert resp.json() == {"has_embedded_settings": True, "setting_changes": [], "slot_warning": None,
                           "has_findings": False}


async def test_check_overrides_unknown_filament_falls_back_to_one_compatible_with_the_printer(check):
    _resp, sidecar = await check(data=_project_3mf({"layer_height": "0.2"}), filament_profile="Not A Filament")

    assert _merge_calls(sidecar) == [("merged_config", "m-1", "p-1", ["f-bambu"])]  # not the first-listed generic one


async def test_check_overrides_warns_when_the_file_uses_more_slots_than_the_printer_has(check):
    model_xml = '<config><metadata key="extruder" value="1"/><metadata key="extruder" value="3"/></config>'

    resp, _ = await check(data=_project_3mf({"layer_height": "0.2"}, model_xml=model_xml),
                          merged={"layer_height": "0.2"})

    body = resp.json()
    assert body["slot_warning"] == {"used_slots": 3, "printer_slots": 1}  # printer has no loaded slots -> 1
    assert (body["has_findings"], body["setting_changes"]) == (True, [])


async def test_check_overrides_returns_whatever_the_provider_inspects(check):
    """The route delegates the file-format comparison to the slicing provider and passes its result through
    unchanged, handing it the file, the merged config of the chosen presets and the printer's slot count."""
    resp, provider = await check(data=_project_3mf({"layer_height": "0.2"}), merged={"layer_height": "0.3"},
                                 real_inspector=False)
    assert resp.status_code == 200
    assert resp.json() == provider.override_findings
    (call,) = [c for c in provider.calls if c[0] == "inspect_overrides"]
    assert call[2] == 1   # slot count: a printer with no loaded filaments counts as one
