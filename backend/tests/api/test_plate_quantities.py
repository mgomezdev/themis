"""Pure helpers behind project generation: reading a pack's layout into per-plate item counts, and naming its objects."""
import pytest

from app.api.routes.projects import _label_index, _pack_label, _plate_quantities

A, B = 11, 22   # item ids


def test_exact_mapping_with_two_items_on_one_plate():
    layout = {1: ["a__m0", "a__m0", "b__m1"], 2: ["a__m0", "b__m1"]}
    plates, per_plate, exact, unplaced = _plate_quantities(layout, [1, 2], [(A, 3), (B, 2)])
    assert (plates, exact, unplaced) == ([1, 2], True, {})
    assert per_plate == [{str(A): 2, str(B): 1}, {str(A): 1, str(B): 1}]


def test_an_empty_plate_is_dropped_from_the_returned_plates():
    layout = {1: ["a__m0"], 2: [], 3: ["a__m0"]}
    plates, per_plate, exact, _ = _plate_quantities(layout, [1, 2, 3], [(A, 2)])
    assert (plates, per_plate, exact) == ([1, 3], [{str(A): 1}, {str(A): 1}], True)


def test_plates_come_back_sorted_by_plate_number():
    plates, per_plate, _, _ = _plate_quantities({3: ["a__m0"], 1: ["a__m0"]}, [1, 3], [(A, 2)])
    assert plates == [1, 3] and per_plate == [{str(A): 1}, {str(A): 1}]


def test_unplaced_copies_are_reported_per_item_id():
    layout = {1: ["a__m0", "a__m0"]}
    plates, per_plate, exact, unplaced = _plate_quantities(layout, [1], [(A, 5), (B, 2)])
    assert exact is True and plates == [1] and per_plate == [{str(A): 2}]
    assert unplaced == {A: 3, B: 2}


def test_a_fully_placed_item_is_not_reported_unplaced():
    _, _, exact, unplaced = _plate_quantities({1: ["a__m0", "b__m1"]}, [1], [(A, 1), (B, 3)])
    assert exact is True and unplaced == {B: 2}


@pytest.mark.parametrize("layout", [
    {1: ["no-label"]},                    # a name that does not carry a label
    {1: [""]},                            # empty name
    {1: ["a__m0", "ghost__m5"]},          # index beyond the group
    {1: ["a__m0", "a__m0", "a__m0"]},     # more copies than wanted (2)
], ids=["no-label", "empty-name", "index-out-of-range", "too-many-copies"])
def test_an_unreadable_or_inconsistent_layout_falls_back_to_the_even_split(layout):
    plates, per_plate, exact, unplaced = _plate_quantities(layout, [1, 2], [(A, 2), (B, 1)])
    assert exact is False and unplaced == {}
    assert plates == [1, 2]
    assert per_plate == [{str(A): 1, str(B): 1}, {str(A): 1, str(B): 0}]


@pytest.mark.parametrize("qty,plate_nums", [(7, [1, 2, 3]), (1, [1, 2, 3]), (2, [1, 2, 3]), (6, [1, 2, 3]), (5, [4]), (9, [1, 2])])
def test_the_estimated_split_sums_to_each_items_quantity(qty, plate_nums):
    plates, per_plate, exact, unplaced = _plate_quantities(None, plate_nums, [(A, qty), (B, qty + 1)])
    assert (plates, exact, unplaced) == (plate_nums, False, {})
    assert len(per_plate) == len(plate_nums)
    assert sum(p[str(A)] for p in per_plate) == qty
    assert sum(p[str(B)] for p in per_plate) == qty + 1
    counts = [p[str(A)] for p in per_plate]
    assert max(counts) - min(counts) <= 1    # even


def test_the_split_gives_the_extra_copies_to_the_first_plates():
    _, per_plate, _, _ = _plate_quantities(None, [1, 2, 3], [(A, 7)])
    assert [p[str(A)] for p in per_plate] == [3, 2, 2]


def test_without_plate_numbers_everything_goes_on_a_single_plate_1():
    plates, per_plate, exact, _ = _plate_quantities(None, [], [(A, 4)])
    assert (plates, per_plate, exact) == ([1], [{str(A): 4}], False)


# ---- _pack_label ----

def _index_of(label: str) -> int:
    return _label_index(label)


@pytest.mark.parametrize("filename,expected_stem", [
    ("part.stl", "part"),
    ("my part (v2).stl", "my_part_v2"),
    ("Überbau Ø5mm.stl", "berbau_5mm"),
    ("dir/sub/gear.STL", "gear"),
])
def test_labels_are_sanitised_and_carry_the_index(filename, expected_stem):
    label = _pack_label(filename, 3)
    assert label == f"{expected_stem}__m3"
    assert _index_of(label) == 3


def test_a_long_stem_is_truncated_but_keeps_the_index():
    label = _pack_label("x" * 200 + ".stl", 12)
    assert label == "x" * 40 + "__m12"


@pytest.mark.parametrize("filename", ["***.stl", "日本語.stl", "  .stl"])
def test_an_empty_stem_becomes_model(filename):
    assert _pack_label(filename, 0) == "model__m0"


@pytest.mark.parametrize("filename,index", [
    ("bracket__m3.stl", 0),
    ("bracket__m3.stl", 1),
    ("a__m9__m2.stl", 4),
    ("part__m10.stl", 2),
])
def test_a_label_round_trips_to_its_index_even_when_the_stem_looks_like_a_label(filename, index):
    """The objects of a pack are read back by their `__m<index>` suffix; a file whose own name contains `__m<digits>` must not
    make a copy count towards another model."""
    assert _index_of(_pack_label(filename, index)) == index
