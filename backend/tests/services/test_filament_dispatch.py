"""Resolved asks reach slice routing, physical-tool presets and cache keys together."""
from unittest.mock import MagicMock

import pytest

from app.models import Job, JobPrinterConfig, Printer, UploadedFile
from app.plugins.snapmaker.client import SnapmakerExtendedClient
from app.services import slice_cache
from app.services.queue_engine import QueueEngine, _slice_params


@pytest.mark.parametrize("mapped", [False, True])
def test_single_ask_tracks_current_tool_and_cache_key(mapped, monkeypatch):
    config = JobPrinterConfig(print_profile="Process", ask_type="PLA", ask_subtype="any", ask_color="#519F61")
    if mapped:
        config.filament_map = [{"model_filament": 1, "tool_index": None, "filament_type": "PLA", "filament_color": "#519F61"}]
    engine = QueueEngine.__new__(QueueEngine)
    engine._mgr = MagicMock()
    engine._mgr.get_client.return_value = SnapmakerExtendedClient(ip_address="1.2.3.4")
    from app.api.routes.jobs import _build_slice_request, printer_manager
    monkeypatch.setitem(printer_manager._clients, 1, engine._mgr.get_client.return_value)
    keys = []
    for tool in (2, 3):
        loaded = [{"slot": i, "empty": True} for i in range(4)]
        loaded[tool] = {"slot": tool, "type": "PLA", "color": "#519F61", "filament_profile": "Green PLA"}
        printer = Printer(id=1, loaded_filaments=loaded)
        params = _slice_params(Job(), config, printer, UploadedFile(original_filename="part.3mf", relative_path="part.3mf"), "Machine")
        request, resolved = engine._slice_request(1, 1, 1, params, "laminus")
        if mapped:
            assert request.tool_index is None
            assert request.filament_map == [{"model_filament": 1, "tool_index": tool}]
            assert request.filament_presets[tool] == "Green PLA"
            assert len(request.filament_presets) == 4
        else:
            assert request.tool_index == tool
            assert request.filament_presets == ["Green PLA"]
        debug = _build_slice_request(Job(id=1, plate_number=1), config, printer,
                                     UploadedFile(original_filename="part.3mf", relative_path="part.3mf"), "Machine")
        assert (debug.tool_index, debug.filament_map, debug.filament_presets) == (request.tool_index, request.filament_map, request.filament_presets)
        keys.append(slice_cache.cache_key(slice_cache.key_inputs(request, "same-source", params["tool_index"], resolved)))
    assert keys[0] != keys[1]
    assert config.tool_index is None
    if mapped:
        assert config.filament_map[0]["tool_index"] is None


def test_shared_tools_and_each_matched_profile_reach_the_slicer():
    config = JobPrinterConfig(print_profile="Process", tool_index=0, filament_map=[
        {"model_filament": 1, "filament_type": "PLA"},
        {"model_filament": 2, "filament_type": "PLA"},
        {"model_filament": 3, "filament_type": "PETG"},
    ])
    loaded = [{"slot": 0, "empty": True}, {"slot": 1, "empty": True},
              {"slot": 2, "type": "PLA", "filament_profile": "PLA profile"},
              {"slot": 3, "type": "PETG", "filament_profile": "PETG profile"}]
    engine = QueueEngine.__new__(QueueEngine)
    engine._mgr = MagicMock()
    engine._mgr.get_client.return_value = SnapmakerExtendedClient(ip_address="1.2.3.4")
    params = _slice_params(Job(), config, Printer(loaded_filaments=loaded), UploadedFile(original_filename="part.3mf"), "Machine")
    request, resolved = engine._slice_request(1, 1, 1, params, "laminus")
    assert request.tool_index is None  # a legacy job-level pin cannot override a per-filament map
    assert request.filament_map == resolved == [
        {"model_filament": 1, "tool_index": 2}, {"model_filament": 2, "tool_index": 2}, {"model_filament": 3, "tool_index": 3}]
    assert request.filament_presets == ["PLA profile", "PLA profile", "PLA profile", "PETG profile"]


@pytest.mark.parametrize("override", [None, "Explicit profile"])
def test_missing_matched_profile_never_silently_uses_another_tools_profile(override):
    config = JobPrinterConfig(print_profile="Process", filament_profile=override, filament_map=[
        {"model_filament": 1, "tool_index": 0}, {"model_filament": 2, "tool_index": 1}])
    loaded = [{"slot": 0, "type": "PLA", "filament_profile": "PLA profile"}, {"slot": 1, "type": "PETG"}]
    engine = QueueEngine.__new__(QueueEngine)
    engine._mgr = MagicMock()
    engine._mgr.get_client.return_value = SnapmakerExtendedClient(ip_address="1.2.3.4")
    params = _slice_params(Job(), config, Printer(loaded_filaments=loaded), UploadedFile(original_filename="part.3mf"), "Machine")
    if override:
        request, _ = engine._slice_request(1, 1, 1, params, "laminus")
        assert request.filament_presets == [override, override]
    else:
        with pytest.raises(ValueError, match="Filament 2 matched T1.*no filament profile"):
            engine._slice_request(1, 1, 1, params, "laminus")


def test_debug_slice_rejects_an_unmatched_map_instead_of_slicing_on_the_default_tool():
    from fastapi import HTTPException
    from app.api.routes.jobs import _build_slice_request
    config = JobPrinterConfig(filament_map=[{"model_filament": 1, "filament_type": "PETG"}])
    with pytest.raises(HTTPException) as error:
        _build_slice_request(Job(), config, Printer(name="Shuna", loaded_filaments=[{"type": "PLA"}]), UploadedFile(), "Machine")
    assert error.value.status_code == 422
    assert "Shuna" in error.value.detail and "PETG" in error.value.detail


def test_ams_profiles_and_dispatch_tray_mapping_follow_model_order_not_physical_slot_order():
    from app.services.filament_matching import SlotUse
    from app.services.queue_engine import _ams_mapping
    config = JobPrinterConfig(print_profile="Process", filament_map=[
        {"model_filament": 2, "tool_index": 0}, {"model_filament": 1, "tool_index": 2}])
    loaded = [{"slot": 0, "type": "PETG", "filament_profile": "PETG profile", "ams_tray_id": 0},
              {"slot": 1, "type": "PLA", "filament_profile": "Unused", "ams_tray_id": 1},
              {"slot": 2, "type": "PLA", "filament_profile": "PLA profile", "ams_tray_id": 18}]
    engine = QueueEngine.__new__(QueueEngine)
    engine._mgr = MagicMock()
    engine._mgr.get_client.return_value.slice_tool_mapping = False
    params = _slice_params(Job(), config, Printer(loaded_filaments=loaded), UploadedFile(original_filename="part.3mf"), "Machine")
    request, resolved = engine._slice_request(1, 1, 1, params, "laminus")
    assert request.filament_presets == ["PLA profile", "PETG profile"]
    assert request.filament_map is None
    assert resolved == [{"model_filament": 2, "tool_index": 0}, {"model_filament": 1, "tool_index": 2}]
    assert _ams_mapping([SlotUse(2, 0), SlotUse(1, 2)], loaded) == [18, 0]
    assert _ams_mapping([SlotUse(1, 2), SlotUse(2, 2)], loaded) == [18, 18]


@pytest.mark.parametrize("plan,loaded", [
    ([(1, 0), (2, 1)], [{"ams_tray_id": 0}, {}]),
    ([(1, 0), (3, 1)], [{"ams_tray_id": 0}, {"ams_tray_id": 4}]),
])
def test_incomplete_ams_mapping_cannot_silently_print_a_partial_tray_list(plan, loaded):
    from app.services.filament_matching import SlotUse
    from app.services.queue_engine import _ams_mapping
    with pytest.raises(ValueError):
        _ams_mapping([SlotUse(m, s) for m, s in plan], loaded)


@pytest.mark.parametrize("slice_mapping", [False, True])
@pytest.mark.parametrize("indices", [[None, 2], [1, 1], [0, 2]])
def test_invalid_model_indices_produce_a_recoverable_error_before_slice_routing(slice_mapping, indices):
    config = JobPrinterConfig(print_profile="Process", filament_map=[
        {"model_filament": indices[0], "tool_index": 0}, {"model_filament": indices[1], "tool_index": 1}])
    loaded = [{"type": "PLA", "filament_profile": "PLA"}, {"type": "PETG", "filament_profile": "PETG"}]
    engine = QueueEngine.__new__(QueueEngine)
    engine._mgr = MagicMock()
    engine._mgr.get_client.return_value.slice_tool_mapping = slice_mapping
    params = _slice_params(Job(), config, Printer(loaded_filaments=loaded), UploadedFile(original_filename="part.3mf"), "Machine")
    with pytest.raises(ValueError, match="unique positive model filament number"):
        engine._slice_request(1, 1, 1, params, "laminus")
