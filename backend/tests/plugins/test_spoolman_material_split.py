"""The Spoolman plugin owns how Spoolman's single free-text `material` becomes the contract's type + subtype."""
import pytest

from app.plugins.spoolman.provider import to_material


def raw(material):
    return {"id": 3, "name": "x", "material": material, "color_hex": "ff0000", "vendor": {"name": "Acme"}, "extra": {}}


@pytest.mark.parametrize("text,expected", [
    ("PLA", ("PLA", "standard")), ("PLA+", ("PLA", "plus")), ("PLA-CF", ("PLA", "cf")), ("PETG GF", ("PETG", "gf")),
    ("PLA Silk", ("PLA", "silk")), (None, (None, None)), ("", (None, None)),
])
def test_spoolman_material_string_is_split_into_type_and_subtype(text, expected):
    m = to_material(raw(text))
    assert (m.material, m.subtype) == expected
    assert (m.vendor, m.color_hex) == ("Acme", "#FF0000")
