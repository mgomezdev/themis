"""BIZ-366 v2: the usage-report contract and per-attempt allocation (`filament_usage`)."""
import copy

import pytest
from pydantic import ValidationError

from app.services.filament_usage import MAX_REPORT_SLOTS, UsageReport, allocate

LOADED = [{"slot": 0, "type": "PLA"}, {"slot": 4, "type": "PETG"}, {"slot": 9, "type": "PLA", "empty": True}]
CONTEXT = {"attempt_id": "a1", "loaded_filaments": LOADED,
           "slot_plan": [{"model_filament": 1, "slot": 1}, {"model_filament": 2, "slot": 0}]}


def rep(slots, **over):
    return {"version": 2, "attempt_id": "a1", "revision": 0, "final": True, "source": "reported",
            "slots": [{"slot": s, "grams": g} for s, g in slots], **over}


def test_a_valid_report_sums_its_slots():
    assert UsageReport.model_validate(rep([(0, 1.5), (4, 2.0)])).grams == 3.5


@pytest.mark.parametrize("bad", [
    rep([(0, 1.0), (0, 2.0)]),                              # a slot twice
    rep([(0, -1.0)]),                                       # negative
    rep([(-1, 1.0)]),                                       # not a slot id
    rep([(0, 1.0)], version=1),                             # the removed v1
    rep([(0, 1.0)], source="estimated"),                    # a plugin reports or measures; Themis estimates
    rep([(0, 1.0)], rollover=True),                         # unknown field: rollover is just more slots
    rep([(i, 1.0) for i in range(MAX_REPORT_SLOTS + 1)]),   # bounded
    {**rep([(0, 1.0)]), "slots": [{"slot": 0, "grams": float("inf")}]},
])
def test_a_malformed_report_is_refused(bad):
    with pytest.raises(ValidationError):
        UsageReport.model_validate(bad)


def test_a_report_maps_slot_ids_not_list_positions_and_skips_zero():
    out = allocate(CONTEXT, ended="complete", report=rep([(4, 3.0), (0, 0.0)]), end_loaded=None, estimate_grams=99, estimate_breakdown=None)
    assert [(s["slot"], g) for s, g in out.slots] == [(4, 3.0)] and out.source == "reported" and out.reason is None


def test_an_empty_slot_at_start_is_found_loaded_at_the_end():
    end = [{"slot": 9, "type": "PLA", "inventory": {"spool_ref": "7"}}]
    out = allocate(CONTEXT, ended="failed", report=rep([(9, 2.0)]), end_loaded=end, estimate_grams=None, estimate_breakdown=None)
    assert [(s["slot"], g) for s, g in out.slots] == [(9, 2.0)] and out.slots[0][0]["inventory"]["spool_ref"] == "7"


def test_an_unknown_slot_is_noted_and_the_rest_kept():
    out = allocate(CONTEXT, ended="complete", report=rep([(0, 1.0), (21, 2.0)]), end_loaded=[], estimate_grams=None, estimate_breakdown=None)
    assert [(s["slot"], g) for s, g in out.slots] == [(0, 1.0)] and "slot 21" in out.reason


def test_an_invalid_report_deducts_nothing():
    out = allocate(CONTEXT, ended="complete", report=rep([(0, 1.0)], version=1), end_loaded=None, estimate_grams=20, estimate_breakdown=None)
    assert out.slots == [] and out.source == "none" and "invalid" in out.reason


@pytest.mark.parametrize("ended", ["failed", "cancelled", "requeued"])
def test_without_a_report_only_a_completed_print_uses_the_estimate(ended):
    out = allocate(CONTEXT, ended=ended, report=None, end_loaded=None, estimate_grams=20,
                   estimate_breakdown=[{"extruder_index": 0, "grams": 20.0}])
    assert out.slots == [] and ended in out.reason


def test_another_attempts_report_is_ignored():
    out = allocate(CONTEXT, ended="complete", report=rep([(0, 50.0)], attempt_id="old"), end_loaded=None, estimate_grams=20,
                   estimate_breakdown=[{"extruder_index": 0, "grams": 12.0}, {"extruder_index": 1, "grams": 8.0}])
    assert out.source == "estimated" and sorted((s["slot"], g) for s, g in out.slots) == [(0, 8.0), (4, 12.0)]


def test_the_estimate_routes_model_filaments_through_the_plan_and_merges_shared_slots():
    shared = {**CONTEXT, "slot_plan": [{"model_filament": 1, "slot": 0}, {"model_filament": 2, "slot": 0}]}
    out = allocate(shared, ended="complete", report=None, end_loaded=None, estimate_grams=20,
                   estimate_breakdown=[{"extruder_index": 0, "grams": 12.0}, {"extruder_index": 1, "grams": 8.0}])
    assert [(s["slot"], g) for s, g in out.slots] == [(0, 20.0)]


def test_a_single_planned_slot_takes_the_whole_estimate_without_a_breakdown():
    one = {**CONTEXT, "slot_plan": [{"model_filament": None, "slot": 1}]}
    out = allocate(one, ended="complete", report=None, end_loaded=None, estimate_grams=7.5, estimate_breakdown=None)
    assert [(s["slot"], g) for s, g in out.slots] == [(4, 7.5)]


@pytest.mark.parametrize("grams", [None, "x", True, -1, float("nan")])
def test_a_malformed_estimate_deducts_nothing(grams):
    out = allocate(CONTEXT, ended="complete", report=None, end_loaded=None, estimate_grams=grams, estimate_breakdown=None)
    assert out.slots == [] and out.reason


def test_an_estimate_for_an_unplanned_filament_deducts_nothing():
    out = allocate(CONTEXT, ended="complete", report=None, end_loaded=None, estimate_grams=5,
                   estimate_breakdown=[{"extruder_index": 3, "grams": 5.0}])
    assert out.slots == [] and "filament 4" in out.reason


def test_the_context_is_never_mutated():
    before = copy.deepcopy(CONTEXT)
    allocate(CONTEXT, ended="complete", report=rep([(0, 1.0)]), end_loaded=None, estimate_grams=1, estimate_breakdown=None)
    allocate(CONTEXT, ended="complete", report=None, end_loaded=None, estimate_grams=20,
             estimate_breakdown=[{"extruder_index": 0, "grams": 20.0}])
    assert CONTEXT == before
