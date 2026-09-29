from unittest.mock import patch

import pytest
from pydantic import ValidationError
from app.services.slicer_service import SliceRequest
from app.api.routes.jobs import PrinterConfigInput


def test_slice_request_has_prepare_hook_default_none():
    req = SliceRequest(job_id=1, source_3mf="x", plate_number=0, machine_preset="M",
                       process_preset="P", filament_presets=["F"])
    assert req.prepare_hook is None


def test_printer_config_input_accepts_filament_map():
    c = PrinterConfigInput(printer_id=1, print_profile="p", filament_type="any", filament_color="any",
                           filament_map=[{"model_filament": 1, "tool_index": 2}])
    assert c.filament_map[0]["tool_index"] == 2
    assert PrinterConfigInput(printer_id=1, print_profile="p", filament_type="any", filament_color="any").filament_map is None


def test_printer_config_input_rejects_non_dict_entry():
    with pytest.raises(ValidationError):
        PrinterConfigInput(printer_id=1, print_profile="p", filament_type="any", filament_color="any",
                           filament_map=["oops"])


def test_printer_config_input_coerces_numeric_string_tool_index():
    # Pydantic's lax int coercion turns "0" into 0 so downstream int comparisons
    # (e.g. `0 <= tool_index < len(loaded)`) never see a str.
    c = PrinterConfigInput(printer_id=1, print_profile="p", filament_type="any", filament_color="any",
                           filament_map=[{"tool_index": "0"}])
    assert c.filament_map[0]["tool_index"] == 0
    assert isinstance(c.filament_map[0]["tool_index"], int)


def test_printer_config_input_rejects_non_numeric_tool_index():
    with pytest.raises(ValidationError):
        PrinterConfigInput(printer_id=1, print_profile="p",
                           filament_map=[{"tool_index": "not-a-number"}])


def test_printer_config_input_requires_filament_type_and_color():
    with pytest.raises(ValidationError):
        PrinterConfigInput(printer_id=1, print_profile="p")


def test_printer_config_input_rejects_blank_filament_type():
    with pytest.raises(ValidationError):
        PrinterConfigInput(printer_id=1, print_profile="p", filament_type="  ", filament_color="any")


def test_printer_config_input_rejects_null_filament_color():
    with pytest.raises(ValidationError):
        PrinterConfigInput(printer_id=1, print_profile="p", filament_type="any", filament_color=None)


def test_printer_config_input_accepts_any_or_real_value():
    c = PrinterConfigInput(printer_id=1, print_profile="p", filament_type="any", filament_color="any")
    assert (c.filament_type, c.filament_color) == ("any", "any")
    c2 = PrinterConfigInput(printer_id=1, print_profile="p", filament_type="PLA", filament_color="#FFFFFF")
    assert (c2.filament_type, c2.filament_color) == ("PLA", "#FFFFFF")


def _routing(filament_map):
    """(model_filament, tool_index) pairs; the API normalises entries with extra null keys."""
    return [(e["model_filament"], e["tool_index"]) for e in filament_map]


async def test_tool_index_and_filament_map_round_trip_through_create_details_and_edit(client, upload_3mf, create_printer):
    """Multi-material routing must survive HTTP create -> details -> edit (both fields are stored on the
    per-printer config; a route that forgets to copy one silently drops it)."""
    printer_id = await create_printer()
    file_id = await upload_3mf()
    first_map = [{"model_filament": 1, "tool_index": 2}, {"model_filament": 2, "tool_index": 0}]
    config = {"printer_id": printer_id, "print_profile": "0.20mm", "filament_type": "any", "filament_color": "any"}

    with patch("app.api.routes.jobs.queue_engine"):
        created = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{**config, "tool_index": 3, "filament_map": first_map}],
        })
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    (stored,) = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()["printer_configs"]
    assert stored["tool_index"] == 3
    assert _routing(stored["filament_map"]) == [(1, 2), (2, 0)]

    second_map = [{"model_filament": 1, "tool_index": 1}]
    with patch("app.api.routes.jobs.queue_engine"):
        edited = await client.patch(f"/api/v1/jobs/{job_id}/configs", json={
            "printer_configs": [{**config, "tool_index": 0, "filament_map": second_map}],
        })
    assert edited.status_code == 200, edited.text

    (stored,) = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()["printer_configs"]
    assert stored["tool_index"] == 0
    assert _routing(stored["filament_map"]) == [(1, 1)]

    catalog_map = [{"model_filament": 1, "filament_type": "PETG", "filament_color": "#FFFFFF"}]  # resolved to a slot at slice time
    with patch("app.api.routes.jobs.queue_engine"):
        await client.patch(f"/api/v1/jobs/{job_id}/configs", json={
            "printer_configs": [{**config, "filament_map": catalog_map}],
        })
    (stored,) = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()["printer_configs"]
    assert stored["tool_index"] is None
    assert [(e["model_filament"], e["tool_index"], e["filament_type"], e["filament_color"]) for e in stored["filament_map"]] \
        == [(1, None, "PETG", "#FFFFFF")]

    with patch("app.api.routes.jobs.queue_engine"):
        await client.patch(f"/api/v1/jobs/{job_id}/configs", json={"printer_configs": [config]})
    (stored,) = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()["printer_configs"]
    assert stored["tool_index"] is None and stored["filament_map"] is None  # clearing works too
