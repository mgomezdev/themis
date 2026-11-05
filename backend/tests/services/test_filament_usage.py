from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.services.filament_usage import UsageReport, allocate


def context(basis="slicer_filament"):
    return {"attempt_id": "print-1", "basis": basis,
            "slot_plan": [{"model_filament": 1, "slot": 2}, {"model_filament": 2, "slot": 0}],
            "loaded_filaments": [{}, {}, {}, {}]}


def report(**overrides):
    return {"version": 1, "attempt_id": "print-1", "report_id": "r1", "revision": 1,
            "basis": "physical_slot", "source": "reported", "final": True, "complete": True,
            "grams": 20.0, "breakdown": [{"index": 2, "grams": 12.0}, {"index": 0, "grams": 8.0}], **overrides}


def test_ams_estimates_translate_logical_filaments_to_the_saved_slots():
    result = allocate(context(), 20.0, [{"extruder_index": 0, "grams": 12.0}, {"extruder_index": 1, "grams": 8.0}])
    assert result.by_slot == {2: 12.0, 0: 8.0} and result.reason is None and result.source == "estimated"


def test_multitool_usage_is_already_in_physical_tool_order():
    result = allocate(context("physical_slot"), 20.0, [{"extruder_index": 2, "grams": 12.0}, {"extruder_index": 0, "grams": 8.0}])
    assert result.by_slot == {2: 12.0, 0: 8.0} and result.reason is None


def test_final_physical_consumption_overrides_sliced_estimates_and_can_include_a_rollover_slot():
    actual = report(rollover=True, breakdown=[{"index": 2, "grams": 5.0}, {"index": 3, "grams": 15.0}])
    result = allocate(context(), 100.0, None, actual)
    assert result.by_slot == {2: 5.0, 3: 15.0} and result.source == "reported" and result.reason is None


@pytest.mark.parametrize("changes", [{"final": False}, {"complete": False}, {"attempt_id": "old-print"},
    {"rollover": True, "source": "estimated"}, {"rollover": True, "basis": "slicer_filament"},
    {"grams": 999.0}, {"breakdown": [{"index": 99, "grams": 20.0}]}])
def test_unattributable_reports_never_fall_back_to_the_slice_or_produce_a_partial_allocation(changes):
    result = allocate(context(), 20.0, [{"extruder_index": 0, "grams": 12.0}, {"extruder_index": 1, "grams": 8.0}], report(**changes))
    assert result.by_slot == {} and result.reason


@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf")])
def test_invalid_quantities_are_rejected(value):
    with pytest.raises(ValidationError):
        UsageReport.model_validate(report(grams=value))


def test_shared_slots_are_aggregated_once():
    saved = context()
    saved["slot_plan"][1]["slot"] = 2
    result = allocate(saved, 20.0, [{"extruder_index": 0, "grams": 12.0}, {"extruder_index": 1, "grams": 8.0}])
    assert result.by_slot == {2: 20.0}


def test_missing_usage_entries_do_not_silently_deduct_zero_from_a_used_spool():
    result = allocate(context(), 20.0, [{"extruder_index": 0, "grams": 12.0}])
    assert result.by_slot == {} and "incomplete" in result.reason


def test_input_context_is_never_mutated():
    saved = context()
    before = deepcopy(saved)
    allocate(saved, 20.0, None, report())
    assert saved == before


def test_a_complete_zero_usage_report_needs_no_deduction_or_reconciliation():
    result = allocate(context(), 20.0, None, report(grams=0.0, breakdown=[]))
    assert result.by_slot == {} and result.reason is None


@pytest.mark.parametrize("invalid", ["not-a-weight", True, None])
def test_malformed_totals_require_reconciliation_instead_of_crashing_the_completion_observer(invalid):
    result = allocate(context(), invalid, None, report(grams=invalid))
    assert result.reason and result.by_slot == {}
