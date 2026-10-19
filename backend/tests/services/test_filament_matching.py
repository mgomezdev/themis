"""The filament matcher (spec 2026-10-19-filament-matching-design.md): single ask, per-colour asks, pinned tools, conflicts, reasons."""
from types import SimpleNamespace

import pytest

from app.models import JobPrinterConfig
from app.plugins.capabilities.filament_inventory import InvMaterial, InvSpool
from app.services import filament_matching as fm
from app.services.queue_engine import _slot_for_config


def slot(i, type_="PLA", subtype="standard", color="#FF0000", manufacturer="Acme", **extra):
    return {"slot": i, "type": type_, "subtype": subtype, "color": color, "manufacturer": manufacturer, **extra}


EMPTY = {"slot": 9, "type": "", "color": "", "empty": True}
LOADED = [slot(0), slot(1, subtype="silk"), slot(2, "PETG", color="#00FF00", manufacturer=None)]


def cfg(manufacturer=None, type_=None, subtype=None, color=None, tool_index=None, filament_map=None, **kw):
    return SimpleNamespace(ask_manufacturer=manufacturer, ask_type=type_, ask_subtype=subtype, ask_color=color,
                           tool_index=tool_index, filament_map=filament_map, slot_plan=None, **kw)


def slots_of(verdict):
    return [(u.model_filament, u.slot) for u in verdict.plan]


# --- the spec's example table, through the engine entry point --------------------------------------------------------------------------

@pytest.mark.parametrize("ask,expected", [
    (cfg(), 0),
    (cfg(type_="PLA", subtype="any", color="#ff0000"), 0),
    (cfg(type_="PLA", subtype="silk", color="#FF0000"), 1),
    (cfg(type_="PLA", subtype="standard", color="#FF0000"), 0),
    (cfg("acme", "pla", "standard", "FF0000"), 0),
    (cfg("Bambu Lab", "PLA", "any", "#FF0000"), None),
    (cfg("Acme", "PETG", "standard", "#00FF00"), None),            # the slot's manufacturer is unknown
    (cfg(type_="PETG", subtype="standard", color="#00FF00"), 2),
    (cfg(type_="PLA", subtype="any", color="#FF0001"), None),
])
def test_single_ask_picks_the_lowest_satisfying_slot(ask, expected):
    verdict = fm.evaluate(ask, LOADED)
    assert verdict.eligible is (expected is not None)
    assert slots_of(verdict) == ([(None, expected)] if expected is not None else [])


def test_an_unmatched_ask_names_every_slot_and_what_was_wanted():
    verdict = fm.evaluate(cfg(type_="PLA", subtype="silk", color="#00FF00"), LOADED)
    assert not verdict.eligible
    assert verdict.reasons == ["T0: Acme PLA standard #FF0000; wanted PLA silk #00FF00",
                               "T1: Acme PLA silk #FF0000; wanted PLA silk #00FF00",
                               "T2: PETG standard #00FF00; wanted PLA silk #00FF00"]


def test_a_printer_with_no_slots_is_ineligible_for_a_defined_ask_but_not_for_defer():
    v = fm.evaluate(cfg(color="#FF0000"), [])
    assert (v.eligible, v.reasons) == (False, ["no filament configured"])
    assert fm.evaluate(cfg(), []).eligible is True
    assert fm.evaluate(cfg(), None).eligible is True


def test_an_empty_tool_placeholder_is_not_loaded_and_is_not_any_slot():
    loaded = [dict(EMPTY, slot=0), slot(1)]
    assert slots_of(fm.evaluate(cfg(), loaded)) == [(None, 1)]                      # defer skips the empty tool
    assert fm.evaluate(cfg(), [dict(EMPTY)]).eligible is False                       # only an empty tool: nothing is loaded
    v = fm.evaluate(cfg(type_="PLA", subtype="any"), [dict(EMPTY, slot=0)])
    assert (v.eligible, v.reasons) == (False, ["T0 is empty"])


def test_a_hand_entered_slot_without_a_type_is_loaded_for_defer_but_fails_a_type_ask():
    manual = [{"slot": 0, "type": "", "name": "", "color": "", "filament_profile": "Generic PLA"}]
    assert fm.evaluate(cfg(), manual).eligible is True
    assert fm.evaluate(cfg(type_="PLA", subtype="any"), manual).eligible is False


def test_a_legacy_slot_type_string_is_matched_through_the_suffix_rules():
    old = [{"slot": 0, "type": "PLA+", "color": "#FF0000"}]                          # not migrated yet: no subtype key
    assert fm.evaluate(cfg(type_="PLA", subtype="plus"), old).eligible is True
    assert fm.evaluate(cfg(type_="PLA", subtype="standard"), old).eligible is False


# --- pinned tool slots ------------------------------------------------------------------------------------------------------------------------

def test_a_pinned_tool_must_simply_be_loaded_its_content_is_not_matched():
    loaded = [dict(EMPTY, slot=0), slot(1, "PETG", color="#000000")]
    assert slots_of(fm.evaluate(cfg(tool_index=1, type_="PLA", subtype="silk"), loaded)) == [(None, 1)]
    assert fm.evaluate(cfg(tool_index=0), loaded).reasons == ["T0 is empty"]
    assert fm.evaluate(cfg(tool_index=5), loaded).reasons == ["T5 does not exist on this printer"]
    assert fm.evaluate(cfg(tool_index=0), []).eligible is False


# --- multi-colour ---------------------------------------------------------------------------------------------------------------------------------

def test_each_model_colour_is_resolved_independently_and_may_share_a_slot():
    fmap = [{"model_filament": 1, "filament_type": "PLA", "filament_subtype": "standard", "filament_color": "#FF0000"},
            {"model_filament": 2, "filament_type": "PLA", "filament_subtype": "any", "filament_color": "#FF0000"},
            {"model_filament": 3, "filament_type": "PETG", "filament_subtype": "any"}]
    v = fm.evaluate(cfg(filament_map=fmap), LOADED)
    assert v.eligible and slots_of(v) == [(1, 0), (2, 0), (3, 2)]                    # two colours on T0: no error
    assert fm.resolved_filament_map(cfg(filament_map=fmap), v.plan) == [
        {"model_filament": 1, "tool_index": 0}, {"model_filament": 2, "tool_index": 0}, {"model_filament": 3, "tool_index": 2}]


def test_one_unsatisfied_colour_makes_the_printer_ineligible_and_the_reason_names_the_colour():
    fmap = [{"model_filament": 1, "filament_type": "PLA", "filament_subtype": "any"},
            {"model_filament": 2, "filament_type": "ABS", "filament_subtype": "any"}]
    v = fm.evaluate(cfg(filament_map=fmap), LOADED)
    assert not v.eligible and v.plan == []
    assert all(r.startswith("colour 2: ") for r in v.reasons) and "wanted ABS any" in v.reasons[0]


def test_pinned_and_asked_colours_mix():
    fmap = [{"model_filament": 1, "tool_index": 2}, {"model_filament": 2, "filament_type": "PLA", "filament_subtype": "silk"}]
    assert slots_of(fm.evaluate(cfg(filament_map=fmap), LOADED)) == [(1, 2), (2, 1)]
    v = fm.evaluate(cfg(filament_map=[{"model_filament": 1, "tool_index": 9}]), LOADED)
    assert v.reasons == ["colour 1: T9 does not exist on this printer"]


def test_a_map_on_a_printer_with_no_slots_says_no_filament_configured():
    v = fm.evaluate(cfg(filament_map=[{"model_filament": 1, "filament_type": "PLA", "filament_subtype": "any"}]), [])
    assert v.reasons == ["colour 1: no filament configured"]


def test_resolved_map_is_none_for_a_single_filament_config():
    assert fm.resolved_filament_map(cfg(type_="PLA", subtype="any"), [fm.SlotUse(None, 0)]) is None


# --- conflicts ------------------------------------------------------------------------------------------------------------------------------------------

def test_a_conflicting_slot_is_skipped_for_matching_but_the_printers_other_slots_still_match():
    ask = cfg(type_="PLA", subtype="any")
    assert slots_of(fm.evaluate(ask, LOADED, conflicts={0})) == [(None, 1)]
    v = fm.evaluate(cfg(type_="PLA", subtype="standard"), LOADED, conflicts={0})
    assert not v.eligible and "T0: bound spool conflicts with what the printer reports" in v.reasons


def test_a_pinned_conflicting_slot_makes_the_printer_ineligible():
    v = fm.evaluate(cfg(tool_index=1), LOADED, conflicts={1})
    assert (v.eligible, v.reasons) == (False, ["T1: bound spool conflicts with what the printer reports"])


def spool(ref, type_="PLA", subtype="standard", color="#FF0000", vendor="Acme"):
    m = InvMaterial(ref="m" + ref, name="x", material=type_, subtype=subtype, color_hex=color, vendor=vendor)
    return InvSpool(ref=ref, material_ref=m.ref, material=m)


def bound(i, ref, reported=True, **kw):
    return slot(i, reported=reported, inventory={"provider": "spoolman", "spool_ref": ref}, **kw)


async def test_conflicts_are_found_for_the_active_providers_spools(session_factory, monkeypatch):
    from tests.fake_providers import FakeInventoryProvider
    from tests.inventory_helpers import use_provider
    fake = FakeInventoryProvider(spools=[spool("1", subtype="silk"), spool("2", vendor="Other"), spool("3")])
    await use_provider(fake, plugin_id="spoolman")
    loaded = [bound(0, "1"),                                    # differs in subtype -> conflict
              bound(1, "2", manufacturer=None),                 # manufacturer unknown on the slot -> not a difference
              bound(2, "3"),                                    # identical
              bound(3, "1", reported=False),                    # hand-entered: never conflicts
              slot(4)]                                          # unbound
    assert await fm.conflicts_for(loaded) == {0}
    assert await fm.conflicts_for([slot(0)]) == set()                                # nothing bound: the provider is not even asked


# --- the stored plan ---------------------------------------------------------------------------------------------------------------------------------

def test_slot_for_config_prefers_the_claim_time_plan_over_a_fresh_match():
    config = cfg(type_="PLA", subtype="any")
    assert _slot_for_config(config, LOADED)["slot"] == 0
    config.slot_plan = [{"model_filament": None, "slot": 1}]
    assert _slot_for_config(config, LOADED)["slot"] == 1
    assert _slot_for_config(config, LOADED, plan=[{"model_filament": None, "slot": 2}])["slot"] == 2


def test_a_plan_whose_filament_was_removed_since_is_reported():
    plan = [fm.SlotUse(None, 1)]
    assert fm.still_loaded(plan, LOADED) is None
    assert fm.still_loaded(plan, [slot(0), dict(EMPTY, slot=1)]) == "the filament in T1 was removed"
    assert fm.still_loaded(plan, [slot(0)]) == "the filament in T1 was removed"


def test_plan_json_round_trip_and_garbage():
    plan = [fm.SlotUse(1, 0), fm.SlotUse(None, 3)]
    assert fm.plan_from_json(fm.plan_to_json(plan)) == plan
    assert fm.plan_from_json(None) is None and fm.plan_from_json("x") is None and fm.plan_from_json([{"slot": "a"}]) is None


def test_orm_config_rows_are_matched_by_their_ask_columns():
    row = JobPrinterConfig(job_id=1, printer_id=1, print_profile="p", ask_type="PETG", ask_subtype="any")
    assert slots_of(fm.evaluate(row, LOADED)) == [(None, 2)]
