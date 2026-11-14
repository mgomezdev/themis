"""three_mf_parser.plate_object_names: which objects an arranged 3MF holds on each plate."""
import io
import zipfile

import pytest

from app.services.three_mf_parser import plate_object_names
from tests.pack_fixtures import packed_3mf, zip_with

SETTINGS = "Metadata/model_settings.config"


def _names(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return plate_object_names(zf)


def test_multi_plate_layout_is_returned_per_plate_with_duplicates_preserved():
    data = packed_3mf([["part__m0", "part__m0", "b__m1"], ["part__m0"], ["b__m1", "b__m1"]])
    assert _names(data) == {1: ["part__m0", "part__m0", "b__m1"], 2: ["part__m0"], 3: ["b__m1", "b__m1"]}


def test_plater_id_is_used_instead_of_the_plate_position():
    data = packed_3mf([["a__m0"], ["b__m1"]], plater_ids=[4, 2])
    assert _names(data) == {4: ["a__m0"], 2: ["b__m1"]}


def test_a_plate_without_instances_is_reported_empty():
    assert _names(packed_3mf([["a__m0"], []])) == {1: ["a__m0"], 2: []}


def test_a_missing_model_settings_part_gives_none():
    assert _names(zip_with({"Metadata/plate_1.png": b"x"})) is None


def test_a_config_without_plates_gives_none():
    assert _names(zip_with({SETTINGS: '<config><object id="2"><metadata key="name" value="a"/></object></config>'})) is None


def test_an_instance_of_an_unknown_object_gives_none_rather_than_a_partial_layout():
    xml = ('<config><object id="2"><metadata key="name" value="a__m0"/></object>'
           '<plate><metadata key="plater_id" value="1"/>'
           '<model_instance><metadata key="object_id" value="2"/></model_instance>'
           '<model_instance><metadata key="object_id" value="99"/></model_instance></plate></config>')
    assert _names(zip_with({SETTINGS: xml})) is None


@pytest.mark.parametrize("payload", [
    '<!DOCTYPE config [<!ENTITY x "y">]><config><plate><metadata key="plater_id" value="1"/></plate></config>',
    '<!ENTITY a "b"><config><plate/></config>',
])
def test_doctype_or_entity_payloads_are_refused(payload):
    assert _names(zip_with({SETTINGS: payload})) is None


def test_malformed_xml_gives_none():
    assert _names(zip_with({SETTINGS: "<config><plate>"})) is None


def test_a_non_numeric_plater_id_gives_none():
    xml = '<config><plate><metadata key="plater_id" value="x"/></plate></config>'
    assert _names(zip_with({SETTINGS: xml})) is None
