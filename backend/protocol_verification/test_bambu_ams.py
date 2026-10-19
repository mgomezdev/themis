"""Bambu AMS RFID assumptions behind `app/plugins/bambu/rfid.py`: which tray fields carry the tag and the maker's material code."""
import pytest

from app.plugins.bambu import rfid
from app.plugins.bambu.client import BambuMQTTClient


@pytest.fixture
def live_trays(bambu_cfg):
    """Raw AMS trays from a real printer's `pushall` report (read-only)."""
    import json, ssl, threading
    import paho.mqtt.client as mqtt
    serial = pytest.importorskip("os").environ.get("THEMIS_VERIFY_BAMBU_SERIAL")
    if not serial:
        pytest.skip("set THEMIS_VERIFY_BAMBU_SERIAL (the printer's serial number)")
    got, done = {}, threading.Event()
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1) if hasattr(mqtt, "CallbackAPIVersion") else mqtt.Client()
    c.username_pw_set("bblp", bambu_cfg["access_code"])
    c.tls_set(cert_reqs=ssl.CERT_NONE)
    c.tls_insecure_set(True)

    def on_connect(cl, *_):
        cl.subscribe(f"device/{serial}/report")
        cl.publish(f"device/{serial}/request", json.dumps({"pushing": {"command": "pushall", "version": 1, "push_target": 1}}))

    def on_message(cl, _u, msg):
        p = json.loads(msg.payload).get("print") or {}
        if "ams" in p:
            got["print"] = p
            done.set()

    c.on_connect, c.on_message = on_connect, on_message
    c.connect(bambu_cfg["host"], 8883, 30)
    c.loop_start()
    try:
        assert done.wait(30), "no AMS report within 30 s"
    finally:
        c.loop_stop()
        c.disconnect()
    return got["print"]


def test_tagged_spools_report_a_tag_id_and_a_material_code(live_trays):
    trays = [t for u in (live_trays.get("ams") or {}).get("ams", []) for t in u.get("tray", []) if t.get("tray_type")]
    print("AMS trays:", *[{k: t.get(k) for k in ("id", "tray_type", "tray_sub_brands", "tray_uuid", "tag_uid", "tray_info_idx")}
                         for t in trays], sep="\n  ")
    tagged = [t for t in trays if rfid.has_tag(t)]
    if not tagged:
        pytest.skip("no RFID-tagged spool loaded")
    assert all(t.get("tray_info_idx") for t in tagged), "a tagged spool should carry its material code (tray_info_idx)"


def test_decoded_manufacturers_for_the_loaded_spools(live_trays):
    for slot in BambuMQTTClient._parse_ams(live_trays):
        print(slot["ams_tray_id"], slot["filament_id"], "->", slot["manufacturer"])
