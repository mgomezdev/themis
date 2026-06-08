from types import SimpleNamespace
from app.services.queue_engine import _slot_for_config, _filament_mismatch


def _cfg(**kw):
    base = dict(filament_type=None, filament_color=None, tool_index=None)
    base.update(kw)
    return SimpleNamespace(**base)


LOADED = [
    {"slot": 0, "type": "PLA", "color": "#fff", "filament_profile": "PLA @U1"},
    {"slot": 1, "type": "PETG", "color": "#000", "filament_profile": "PETG @U1"},
    {"slot": 2, "type": "TPU", "color": "#0f0", "filament_profile": "TPU @U1"},
]


def test_slot_for_config_uses_tool_index_directly():
    slot = _slot_for_config(_cfg(tool_index=2), LOADED)
    assert slot["filament_profile"] == "TPU @U1"


def test_slot_for_config_tool_index_out_of_range_returns_none():
    assert _slot_for_config(_cfg(tool_index=9), LOADED) is None


def test_slot_for_config_falls_back_to_ask_match_when_no_tool_index():
    slot = _slot_for_config(_cfg(filament_type="PETG", filament_color="#000"), LOADED)
    assert slot["filament_profile"] == "PETG @U1"


def test_mismatch_blocks_when_tool_index_slot_missing():
    assert _filament_mismatch(_cfg(tool_index=9), LOADED) is not None
    assert _filament_mismatch(_cfg(tool_index=1), LOADED) is None
