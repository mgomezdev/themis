"""The filament tuple rules (spec 2026-10-19): the example table, the suffix split, validation and the conflict test."""
import pytest

from app.services.filament_tuple import (
    ANY, STANDARD, FilamentTuple as T, ask_from_legacy, conflicts, describe, mismatches, satisfies, slot_tuple, split_type,
    validate_ask,
)

SLOTS = [T("Acme", "PLA", "standard", "#FF0000"), T("Acme", "PLA", "silk", "#FF0000"), T(None, "PETG", "standard", "#00FF00")]


def first_match(ask):
    return next((i for i, s in enumerate(SLOTS) if satisfies(ask, s)), None)


@pytest.mark.parametrize("ask,expected", [
    (T(), 0),                                              # defer: lowest loaded slot
    (T(None, "PLA", ANY, "#ff0000"), 0),                   # subtype any, lowest index
    (T(None, "PLA", "silk", "#FF0000"), 1),                # subtype must be equal
    (T(None, "PLA", STANDARD, "#FF0000"), 0),
    (T("acme", "pla", "standard", "FF0000"), 0),           # names case-insensitive, # ignored
    (T("Bambu Lab", "PLA", ANY, "#FF0000"), None),         # manufacturer differs
    (T("Acme", "PETG", STANDARD, "#00FF00"), None),        # unknown slot manufacturer fails a manufacturer ask
    (T(None, "PETG", STANDARD, "#00FF00"), 2),             # manufacturer undefined
    (T(None, "PLA", ANY, "#FF0001"), None),                # colour is exact
])
def test_spec_example_table(ask, expected):
    assert first_match(ask) == expected


def test_mismatches_name_the_failing_parts():
    assert mismatches(T("Acme", "PLA", "silk", "#00FF00"), SLOTS[0]) == ["subtype", "color"]
    assert mismatches(T("Bambu", "ABS", ANY, None), SLOTS[0]) == ["manufacturer", "type"]


def test_an_undefined_ask_part_is_satisfied_by_an_unknown_slot_value():
    assert satisfies(T(None, "PETG", ANY, None), T(None, "PETG", STANDARD, None))
    assert satisfies(T(), T())


@pytest.mark.parametrize("raw,expected", [
    ("PLA", ("PLA", STANDARD)), ("PLA+", ("PLA", "plus")), ("PLA-CF", ("PLA", "cf")), ("PLA Silk", ("PLA", "silk")),
    ("petg gf", ("petg", "gf")), ("PLA Matte", ("PLA", "matte")), ("PA12-CF", ("PA12", "cf")), ("PLA Plus", ("PLA", "plus")),
    ("", (None, None)), (None, (None, None)), ("  abs  ", ("abs", STANDARD)),
])
def test_split_type_suffix_rules(raw, expected):
    assert split_type(raw) == expected


def test_subtype_is_required_when_type_is_set():
    assert validate_ask(T(None, "PLA", None, None)) is not None
    assert validate_ask(T(None, "PLA", ANY, None)) is None
    assert validate_ask(T(None, None, None, "#fff")) is None


@pytest.mark.parametrize("ftype,fcolor,expected", [
    ("any", "any", T()), ("", "", T()), (None, None, T()),
    ("PLA", "any", T(None, "PLA", ANY, None)),                 # legacy type -> base type + subtype any
    ("PLA+", "#FF0000", T(None, "PLA", ANY, "#FF0000")),        # at least as broad as before
    ("any", "#00ff00", T(None, None, None, "#00ff00")),
])
def test_legacy_ask_migration_rules(ftype, fcolor, expected):
    assert ask_from_legacy(ftype, fcolor) == expected


def test_slot_tuple_reads_a_legacy_type_string_through_the_suffix_rules():
    assert slot_tuple({"type": "PLA+", "color": "#fff"}) == T(None, "PLA", "plus", "#fff")
    assert slot_tuple({"type": "PLA", "subtype": "silk", "manufacturer": "Acme"}) == T("Acme", "PLA", "silk", None)
    assert slot_tuple({}) == T()


def test_conflict_needs_a_difference_in_a_part_known_on_both_sides():
    slot = T(None, "PLA", STANDARD, "#FF0000")
    assert conflicts(slot, T("Acme", "PLA", "silk", "#FF0000"))                    # subtype differs
    assert not conflicts(slot, T("Acme", "PLA", STANDARD, "#ff0000"))              # manufacturer unknown on one side, colour equal
    assert not conflicts(T(None, "PLA", STANDARD, None), T(None, None, None, "#00F"))
    assert conflicts(slot, T(None, "PLA", STANDARD, "#00FF00"))


def test_describe():
    assert describe(T("Acme", "PLA", "silk", "#ff0000")) == "Acme PLA silk #FF0000"
    assert describe(T()) == "any filament"
