# backend/tests/services/test_spool_check.py
from app.services.spool_check import check_spool_sufficiency


def test_sufficient_remaining_weight_returns_none():
    spool = {"id": 7, "remaining_weight": 500.0, "filament": {"name": "Bambu PLA Basic Black", "material": "PLA"}}
    assert check_spool_sufficiency(200.0, spool) is None


def test_insufficient_remaining_weight_returns_warning():
    spool = {"id": 7, "remaining_weight": 220.0, "filament": {"name": "Bambu PLA Basic Black", "material": "PLA"}}
    result = check_spool_sufficiency(340.0, spool)
    assert result is not None
    assert result["spool_id"] == 7
    assert result["remaining_g"] == 220.0
    assert result["needed_g"] == 340.0
    assert "340" in result["message"]
    assert "220" in result["message"]


def test_needed_g_none_returns_none():
    spool = {"id": 7, "remaining_weight": 220.0, "filament": {"name": "Bambu PLA Basic Black", "material": "PLA"}}
    assert check_spool_sufficiency(None, spool) is None


def test_spool_with_no_remaining_weight_returns_none():
    spool = {"id": 7, "remaining_weight": None, "filament": {"name": "Bambu PLA Basic Black", "material": "PLA"}}
    assert check_spool_sufficiency(340.0, spool) is None


def test_message_mentions_spool_label_and_type_when_available():
    spool = {"id": 9, "remaining_weight": 100.0, "filament": {"name": "Bambu PLA Basic Black", "material": "PLA"}}
    result = check_spool_sufficiency(300.0, spool)
    assert result["spool_label"] == "Bambu PLA Basic Black"
    assert "PLA" in result["message"]


def test_falls_back_to_spool_id_label_when_filament_info_missing():
    spool = {"id": 11, "remaining_weight": 10.0}
    result = check_spool_sufficiency(300.0, spool)
    assert result is not None
    assert result["spool_label"] == "spool 11"
    assert "300" in result["message"]
    assert "10" in result["message"]
