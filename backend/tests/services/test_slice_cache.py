"""services/slice_cache (BIZ-191): cache key, staleness, decision logging."""
import logging
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from app.services import slice_cache as sc
from app.services.slicer_service import SliceRequest
from tests.fake_providers import FakeSlicingProvider


def _req(**kw) -> SliceRequest:
    base = dict(
        job_id=1, source_3mf="/lib/m.3mf", plate_number=2, machine_preset="Bambu Lab P1S 0.4",
        process_preset="0.20mm Standard @BBL P1S", filament_presets=["Bambu PETG Basic @BBL P1S"],
        filament_colours=["#FF0000"], export_args=["--export-3mf", "m_p2_j1.gcode.3mf"],
        extra_config={"curr_bed_type": "Textured PEI Plate", "sparse_infill_density": "15%"},
    )
    base.update(kw)
    return SliceRequest(**base)


def _key(req=None, source="abc", tool_index=None, filament_map=None) -> str:
    return sc.cache_key(sc.key_inputs(req or _req(), source, tool_index, filament_map))


def test_same_inputs_give_the_same_key_regardless_of_dict_order():
    a = _req(extra_config={"curr_bed_type": "Textured PEI Plate", "sparse_infill_density": "15%"})
    b = _req(extra_config={"sparse_infill_density": "15%", "curr_bed_type": "Textured PEI Plate"})
    assert _key(a) == _key(b)
    assert len(_key(a)) == 64


@pytest.mark.parametrize("change", [
    {"filament_presets": ["Bambu PLA Basic @BBL P1S"]},          # PETG vs PLA
    {"process_preset": "0.12mm Fine @BBL P1S"},
    {"machine_preset": "Bambu Lab X1 Carbon 0.4"},
    {"plate_number": 1},
    {"extra_config": {"curr_bed_type": "Textured PEI Plate", "sparse_infill_density": "20%"}},   # override
    {"extra_config": {"curr_bed_type": "Cool Plate", "sparse_infill_density": "15%"}},           # bed type
    {"export_args": []},                                                                        # raw gcode vs archive
    {"filament_presets": ["Bambu PETG Basic @BBL P1S", "Bambu PLA Basic @BBL P1S"]},            # extra filament
])
def test_each_output_changing_input_changes_the_key(change):
    assert _key(_req(**change)) != _key()


def test_source_hash_tool_index_and_filament_map_change_the_key():
    assert _key(source="other") != _key()
    assert _key(tool_index=1) != _key()
    assert _key(filament_map=[{"model_filament": 1, "tool_index": 2}]) != _key()


def test_filament_colour_and_job_id_do_not_change_the_key():
    assert _key(_req(filament_colours=["#00FF00"])) == _key()
    assert _key(_req(job_id=99, source_3mf="/elsewhere.3mf")) == _key()


def test_artifact_kind_follows_the_export_args():
    assert sc.key_inputs(_req(), "h", None, None).artifact_kind == "gcode_3mf"
    assert sc.key_inputs(_req(export_args=[]), "h", None, None).artifact_kind == "gcode"


def test_key_fields_hash_the_bulky_inputs():
    inputs = sc.key_inputs(_req(), "abc", None, [{"model_filament": 1, "tool_index": 0}])
    fields = sc.key_fields(inputs)
    assert fields["source_content_hash"] == "abc" and fields["plate"] == 2
    assert fields["extra_config_hash"] == sc.sha256_of(inputs.extra_config)
    assert fields["filament_map_hash"] == sc.sha256_of(inputs.filament_map)
    assert sc.key_fields(replace(inputs, filament_map=None))["filament_map_hash"] is None


FP = sc.SlicerFingerprint


@pytest.mark.parametrize("stored_hash,stored_ver,current,expected", [
    ("h1", "2.3.0", FP("h1", "2.3.0"), (False, [])),
    ("h1", "2.3.0", FP("h2", "2.3.0"), (True, ["presets_changed"])),
    ("h1", "2.3.0", FP("h1", "2.4.0"), (True, ["slicer_version_changed"])),
    ("h1", "2.3.0", FP("h2", "2.4.0"), (True, ["presets_changed", "slicer_version_changed"])),
    ("h1", "2.3.0", FP(None, None), (None, [])),                       # sidecar unreachable
    ("h1", None, FP("h1", "2.4.0"), (False, [])),                      # version never recorded: that axis unknown
    (None, "2.3.0", FP("h9", "2.4.0"), (True, ["slicer_version_changed"])),
    (None, None, FP("h1", "2.3.0"), (None, [])),
    (None, "2.3.0", FP("h9", "2.3.0"), (False, [])),                    # only the version compared: same ⇒ fresh
])
def test_staleness(stored_hash, stored_ver, current, expected):
    assert sc.staleness(stored_hash, stored_ver, current) == expected


def _client(merged=None, merged_exc=None, health=None, health_exc=None):
    """A fake slicing provider with a scripted merged config / health response (or failure)."""
    p = FakeSlicingProvider()
    p.merged = merged
    p.health_body = health
    if merged_exc is not None:
        p.fail_on["merged_config"] = merged_exc
    if health_exc is not None:
        p.fail_on["health"] = health_exc
    return p


def test_current_fingerprint_hashes_the_merged_config_and_reads_the_orca_version():
    client = _client(merged={"layer_height": "0.2", "a": [1, 2]}, health={"orca_version": "2.3.1",
                                                                          "orcaslicer_version": "OrcaSlicer 2.3.1"})
    with patch("app.services.slicer_service.resolve_preset_uuids", return_value=("m", "p", ["f"])) as resolve:
        fp = sc.current_fingerprint("M", "P", ["F"], client)
    resolve.assert_called_once_with("M", "P", ["F"], client)
    assert [c for c in client.calls if c[0] == "merged_config"] == [("merged_config", "m", "p", ["f"])]
    assert fp == FP(sc.sha256_of({"a": [1, 2], "layer_height": "0.2"}), "2.3.1")


def test_current_fingerprint_falls_back_to_the_binary_version_string():
    client = _client(merged={}, health={"orca_version": None, "orcaslicer_version": "2.2.0"})
    with patch("app.services.slicer_service.resolve_preset_uuids", return_value=("m", "p", ["f"])):
        assert sc.current_fingerprint("M", "P", ["F"], client).slicer_version == "2.2.0"


def test_current_fingerprint_never_raises():
    client = _client(merged_exc=RuntimeError("down"), health_exc=RuntimeError("down"))
    with patch("app.services.slicer_service.resolve_preset_uuids", return_value=("m", "p", ["f"])):
        assert sc.current_fingerprint("M", "P", ["F"], client) == FP(None, None)
    assert sc.current_fingerprint("M", "P", ["F"], None) == FP(None, None)


def test_log_event_is_one_greppable_line_with_every_field(caplog):
    caplog.set_level(logging.DEBUG, logger="app.services.slice_cache")
    sc.log_event("hit_slice_skipped", job_id=7, cache_key="k" * 64, machine_preset="Bambu Lab P1S 0.4",
                 filament_presets=["A", "B"], stale=False, sliced_version_id=None)
    (rec,) = caplog.records
    assert rec.levelno == logging.INFO
    assert rec.getMessage() == (
        f'slice_cache event=hit_slice_skipped job_id=7 cache_key={"k" * 64} machine_preset="Bambu Lab P1S 0.4" '
        'filament_presets=["A","B"] stale=false sliced_version_id=-')


@pytest.mark.parametrize("event,fields,level", [
    ("lookup", {}, logging.DEBUG),
    ("miss", {"reason": "no_version"}, logging.INFO),
    ("miss", {"reason": "cache_disabled"}, logging.DEBUG),
    ("hit_slice_skipped", {"stale": True}, logging.WARNING),
    ("hit_slice_skipped", {"stale": None}, logging.INFO),
    ("saved", {}, logging.INFO),
    ("save_duplicate_skipped", {}, logging.INFO),
    ("save_failed", {}, logging.WARNING),
    ("pack_reused", {}, logging.INFO),
    ("pack_new", {}, logging.INFO),
])
def test_log_event_levels(caplog, event, fields, level):
    caplog.set_level(logging.DEBUG, logger="app.services.slice_cache")
    sc.log_event(event, **fields)
    assert [r.levelno for r in caplog.records] == [level]
    assert caplog.records[0].getMessage().startswith(f"slice_cache event={event}")


def test_decision_info_keeps_an_earlier_save_outcome():
    version = MagicMock(id=3, file_id=9, preset_content_hash="old", slicer_version="2.3.0")
    saved = sc.with_save_outcome(None, "saved", cache_key="k", sliced_version_id=3, file_id=9)
    info = sc.decision_info("hit", cache_key="k", inputs=sc.key_inputs(_req(), "src", None, None), version=version,
                            cached_file_hash="fh", current=FP("new", "2.4.0"), stale=True,
                            stale_reasons=["presets_changed"], policy="pin_cached", previous=saved)
    assert {k: info[k] for k in ("decision", "cache_key", "source_content_hash", "sliced_version_id", "cached_file_id",
                                 "cached_file_hash", "preset_content_hash_stored", "preset_content_hash_current",
                                 "slicer_version_stored", "slicer_version_current", "stale", "stale_reasons",
                                 "policy")} == {
        "decision": "hit", "cache_key": "k", "source_content_hash": "src", "sliced_version_id": 3, "cached_file_id": 9,
        "cached_file_hash": "fh", "preset_content_hash_stored": "old", "preset_content_hash_current": "new",
        "slicer_version_stored": "2.3.0", "slicer_version_current": "2.4.0", "stale": True,
        "stale_reasons": ["presets_changed"], "policy": "pin_cached"}
    assert info["save"]["outcome"] == "saved" and info["save"]["sliced_version_id"] == 3


def test_a_source_without_a_content_hash_is_uncacheable():
    """Two different models with no hash and the same settings must never share a key."""
    assert sc.key_inputs(_req(), "", None, None) is None
    assert sc.key_inputs(_req(), None, None, None) is None


@pytest.mark.parametrize("raw,expected", [
    ("2.3.1", "2.3.1"), ("OrcaSlicer 2.3.1", "2.3.1"), ("OrcaSlicer 2.3.1-beta+abc", "2.3.1"),
    (None, None), ("", None), ("nightly", "nightly"),
])
def test_normalize_version(raw, expected):
    assert sc.normalize_version(raw) == expected


def test_a_fallback_between_the_two_version_fields_is_not_a_slicer_change():
    client = _client(merged={}, health={"orca_version": None, "orcaslicer_version": "OrcaSlicer 2.3.1"})
    with patch("app.services.slicer_service.resolve_preset_uuids", return_value=("m", "p", ["f"])):
        fp = sc.current_fingerprint("M", "P", ["F"], client)
    assert sc.staleness(None, "2.3.1", fp) == (False, [])


def test_log_event_keeps_multi_line_values_on_one_line_and_accepts_any_field_name(caplog):
    caplog.set_level(logging.DEBUG, logger="app.services.slice_cache")
    sc.log_event("save_failed", error="boom\nTraceback\tline", level="x", event="y")
    (rec,) = caplog.records
    assert "\n" not in rec.getMessage()
    assert rec.getMessage() == 'slice_cache event=save_failed error="boom\\nTraceback\\tline" level=x event=y'
    assert rec.levelno == logging.WARNING


def test_an_unreachable_sidecar_is_remembered_briefly_not_for_the_full_minute():
    with patch("app.services.slice_cache.current_fingerprint", return_value=FP(None, None)) as fp, \
         patch("time.monotonic", side_effect=[100.0, 105.0, 111.0]):
        for _ in range(3):
            sc.cached_fingerprint("M", "P", ["F"], FakeSlicingProvider(identity="fake://s"))
    assert fp.call_count == 2   # cached at +5s, refetched at +11s


def test_a_good_fingerprint_is_remembered_for_a_minute():
    with patch("app.services.slice_cache.current_fingerprint", return_value=FP("h", "2.3.1")) as fp, \
         patch("time.monotonic", side_effect=[100.0, 150.0, 161.0]):
        for _ in range(3):
            sc.cached_fingerprint("M", "P", ["F"], FakeSlicingProvider(identity="fake://s"))
    assert fp.call_count == 2


def test_the_fingerprint_memo_is_keyed_per_provider_identity():
    with patch("app.services.slice_cache.current_fingerprint", return_value=FP("h", "2.3.1")) as fp:
        a1 = FakeSlicingProvider(identity="fake://a")
        a2 = FakeSlicingProvider(identity="fake://a")    # same identity, different instance: shares the memo
        b = FakeSlicingProvider(identity="fake://b")
        for provider in (a1, a2, b, None):
            sc.cached_fingerprint("M", "P", ["F"], provider)
    assert fp.call_count == 3
