"""The Bambu plugin decodes its own AMS RFID data into the slot tuple's manufacturer (BIZ-320)."""
import pytest

from app.plugins.bambu import rfid
from app.plugins.bambu.client import BambuMQTTClient

TAG = "A1B2C3D4" * 4


@pytest.mark.parametrize("tray,expected", [
    ({"tray_uuid": TAG, "tray_info_idx": "GFA00"}, "Bambu Lab"),             # tagged, Bambu catalogue code
    ({"tag_uid": "1234ABCD00000000", "tray_info_idx": "gfg00"}, "Bambu Lab"),   # tag_uid works; code case ignored
    ({"tray_uuid": TAG, "tray_info_idx": "GFL99"}, None),                    # generic preset code is not a tagged catalogue spool
    ({"tray_uuid": TAG, "tray_info_idx": "GFG99"}, None),
    ({"tray_uuid": TAG, "tray_info_idx": "P1a2b3c"}, None),                  # a tag from a maker the plugin does not know
    ({"tray_uuid": TAG}, None),                                              # tag but no material code
    ({"tray_uuid": "0" * 32, "tray_info_idx": "GFA00"}, None),               # no tag (zeros), code alone never attributes a maker
    ({"tray_info_idx": "GFA00"}, None),
])
def test_manufacturer_is_decoded_from_the_tag_and_material_code(tray, expected):
    assert rfid.manufacturer(tray) == expected


def test_another_tag_vendor_is_one_table_row(monkeypatch):
    monkeypatch.setattr(rfid, "TAG_VENDORS", rfid.TAG_VENDORS + (rfid.TagVendor("Acme", lambda c: c.startswith("AC")),))
    assert rfid.manufacturer({"tray_uuid": TAG, "tray_info_idx": "AC001"}) == "Acme"
    assert rfid.manufacturer({"tray_uuid": TAG, "tray_info_idx": "GFA00"}) == "Bambu Lab"


def test_published_slots_carry_the_decoded_manufacturer():
    report = {"ams": {"ams": [{"id": "0", "tray": [
        {"id": "0", "tray_type": "PLA", "tray_color": "FF0000FF", "tray_uuid": TAG, "tray_info_idx": "GFA00"},
        {"id": "1", "tray_type": "PLA", "tray_color": "FF0000FF", "tray_uuid": TAG, "tray_info_idx": "P9z"},
        {"id": "2", "tray_type": "PLA", "tray_color": "FF0000FF", "tray_info_idx": "GFA00"},
    ]}]}}
    by_id = {t["ams_tray_id"]: t["manufacturer"] for t in BambuMQTTClient._parse_ams(report)}
    assert by_id == {0: "Bambu Lab", 1: None, 2: None}
