import pytest
from types import SimpleNamespace
from app.services.queue_engine import _filament_mismatch, _mapped_tools_loaded, _find_slot_for_filament


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


LOADED_MIXED = [
    {"slot": 0, "type": "PLA",  "color": "#5B9BD5", "filament_profile": "PLA @ECC"},
    {"slot": 1, "type": "PETG", "color": "#FFFFFF",  "filament_profile": "PETG @ECC"},
]


# ── _find_slot_for_filament ──────────────────────────────────────────────────

def test_find_slot_type_match():
    assert _find_slot_for_filament("PLA", None, LOADED_MIXED) == 0

def test_find_slot_type_and_color_match():
    assert _find_slot_for_filament("PETG", "#FFFFFF", LOADED_MIXED) == 1

def test_find_slot_color_mismatch_returns_none():
    assert _find_slot_for_filament("PLA", "#000000", LOADED_MIXED) is None

def test_find_slot_type_not_loaded_returns_none():
    assert _find_slot_for_filament("ABS", None, LOADED_MIXED) is None

def test_find_slot_empty_loaded_returns_none():
    assert _find_slot_for_filament("PLA", None, []) is None

def test_find_slot_color_stripped_hash():
    # filament_color stored with #, loaded slot color also with # — both normalised
    assert _find_slot_for_filament("PETG", "FFFFFF", LOADED_MIXED) == 1


# ── _mapped_tools_loaded with catalog entries (no tool_index) ────────────────

def test_mapped_tools_loaded_skips_catalog_entries():
    # Catalog entry has tool_index=None — should be skipped, not cause KeyError
    mixed_map = [
        {"model_filament": 1, "tool_index": 0},
        {"model_filament": 2, "tool_index": None, "filament_type": "PETG"},
    ]
    assert _mapped_tools_loaded(mixed_map, LOADED_MIXED) is True


# ── _filament_mismatch with catalog entries ──────────────────────────────────

def test_filament_mismatch_catalog_entry_matched():
    cfg = _cfg(filament_map=[
        {"model_filament": 1, "tool_index": None, "filament_type": "PLA", "filament_color": None},
    ])
    assert _filament_mismatch(cfg, LOADED_MIXED) is None

def test_filament_mismatch_catalog_entry_not_loaded():
    cfg = _cfg(filament_map=[
        {"model_filament": 1, "tool_index": None, "filament_type": "ABS", "filament_color": None},
    ])
    assert _filament_mismatch(cfg, LOADED_MIXED) is not None

def test_filament_mismatch_mixed_map_slot_bad():
    # One slot entry out of range, one catalog entry matched — whole map fails
    cfg = _cfg(filament_map=[
        {"model_filament": 1, "tool_index": 99},
        {"model_filament": 2, "tool_index": None, "filament_type": "PETG", "filament_color": None},
    ])
    assert _filament_mismatch(cfg, LOADED_MIXED) is not None

def test_filament_mismatch_mixed_map_catalog_bad():
    # Slot entry valid, catalog entry unmatched — whole map fails
    cfg = _cfg(filament_map=[
        {"model_filament": 1, "tool_index": 0},
        {"model_filament": 2, "tool_index": None, "filament_type": "ABS", "filament_color": None},
    ])
    assert _filament_mismatch(cfg, LOADED_MIXED) is not None

def test_filament_mismatch_all_slot_entries_unchanged():
    # Backward compat: old-style slot-only map still works
    assert _filament_mismatch(
        _cfg(filament_map=[{"model_filament": 1, "tool_index": 0}]), LOADED_MIXED
    ) is None

def test_filament_mismatch_slot_entry_with_filament_type_not_double_checked():
    # An entry with tool_index set should not be re-checked via type matching,
    # even if it carries filament_type. T0 is PLA which is loaded, so this should pass.
    cfg = _cfg(filament_map=[
        {"model_filament": 1, "tool_index": 0, "filament_type": "TPU", "filament_color": None},
    ])
    # TPU is not loaded in LOADED_MIXED, but slot 0 is valid — should NOT return mismatch
    assert _filament_mismatch(cfg, LOADED_MIXED) is None


from app.services.queue_engine import _resolve_filament_map


def test_resolve_slot_entries_unchanged():
    fm = [{"model_filament": 1, "tool_index": 0, "filament_id": None, "filament_type": None, "filament_color": None}]
    result = _resolve_filament_map(fm, LOADED_MIXED)
    assert result[0]["tool_index"] == 0

def test_resolve_catalog_entry_found():
    fm = [{"model_filament": 1, "tool_index": None, "filament_id": 7, "filament_type": "PLA", "filament_color": None}]
    result = _resolve_filament_map(fm, LOADED_MIXED)
    assert result[0]["tool_index"] == 0

def test_resolve_catalog_entry_with_color():
    fm = [{"model_filament": 1, "tool_index": None, "filament_id": 19, "filament_type": "PETG", "filament_color": "#FFFFFF"}]
    result = _resolve_filament_map(fm, LOADED_MIXED)
    assert result[0]["tool_index"] == 1

def test_resolve_catalog_entry_not_loaded_raises():
    fm = [{"model_filament": 1, "tool_index": None, "filament_id": 5, "filament_type": "ABS", "filament_color": None}]
    with pytest.raises(ValueError, match="ABS"):
        _resolve_filament_map(fm, LOADED_MIXED)

def test_resolve_mixed_map():
    fm = [
        {"model_filament": 1, "tool_index": 0, "filament_id": None, "filament_type": None, "filament_color": None},
        {"model_filament": 2, "tool_index": None, "filament_id": 19, "filament_type": "PETG", "filament_color": "#FFFFFF"},
    ]
    result = _resolve_filament_map(fm, LOADED_MIXED)
    assert result[0]["tool_index"] == 0
    assert result[1]["tool_index"] == 1

def test_resolve_duplicate_slot_raises():
    # Two catalog entries that both resolve to slot 0 (both PLA, no color) should raise
    fm = [
        {"model_filament": 1, "tool_index": None, "filament_type": "PLA", "filament_color": None},
        {"model_filament": 2, "tool_index": None, "filament_type": "PLA", "filament_color": None},
    ]
    with pytest.raises(ValueError, match="same printer slot"):
        _resolve_filament_map(fm, LOADED_MIXED)
