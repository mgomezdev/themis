from types import SimpleNamespace
from app.services.queue_engine import _filament_mismatch, _mapped_tools_loaded


LOADED = [{"slot": i, "type": "PLA", "color": "#fff", "filament_profile": f"P{i}"} for i in range(3)]


def _cfg(**kw):
    base = dict(filament_type=None, filament_color=None, tool_index=None, filament_map=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_mapped_tools_loaded():
    assert _mapped_tools_loaded([{"model_filament": 1, "tool_index": 2}], LOADED) is True
    assert _mapped_tools_loaded([{"model_filament": 1, "tool_index": 9}], LOADED) is False
    assert _mapped_tools_loaded([], LOADED) is True


def test_filament_map_gates_on_mapped_tools():
    assert _filament_mismatch(_cfg(filament_map=[{"model_filament": 1, "tool_index": 2}]), LOADED) is None
    assert _filament_mismatch(_cfg(filament_map=[{"model_filament": 1, "tool_index": 9}]), LOADED) is not None


def test_filament_map_none_keeps_existing_behaviour():
    # no map, no ask, no tool_index -> not blocked (existing defer/first-slot behaviour)
    assert _filament_mismatch(_cfg(), LOADED) is None
