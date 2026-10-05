"""Spoolman absolute-weight assumption behind the deduction model (BIZ-202 D6, spec §3.2): Themis sets a spool's weight
with `PATCH /api/v1/spool/{id}` `{"remaining_weight": N}` — never a delta — so that re-sending is harmless.

Read-only check always; the write checks need THEMIS_VERIFY_ALLOW_WRITE=1 and overwrite the TEST spool's weight
(they restore the original `used_weight` afterwards, even on failure)."""
import pytest

TARGET_G = 123.5          # an arbitrary value that no real spool is likely to hold exactly


def _get(spoolman) -> dict:
    r = spoolman.http.get(f"/api/v1/spool/{spoolman.spool_id}")
    r.raise_for_status()
    return r.json()


def _patch(spoolman, body: dict):
    return spoolman.http.patch(f"/api/v1/spool/{spoolman.spool_id}", json=body)


def _initial(spool: dict) -> float:
    return spool.get("initial_weight") or spool["filament"]["weight"]


def test_spool_read_carries_the_weight_fields_the_provider_maps(spoolman):
    s = _get(spoolman)
    print({k: s.get(k) for k in ("remaining_weight", "used_weight", "initial_weight")}, "filament.weight:", s["filament"].get("weight"))
    assert isinstance(s["remaining_weight"], (int, float)) and isinstance(s["used_weight"], (int, float))
    assert _initial(s) is not None, "the test spool's filament needs a `weight` (or the spool an initial_weight)"
    assert s["remaining_weight"] == pytest.approx(_initial(s) - s["used_weight"], abs=0.01)   # one quantity, two views


def test_patching_remaining_weight_sets_it_exactly_and_is_not_recomputed(spoolman, require_write):
    before = _get(spoolman)
    try:
        r = _patch(spoolman, {"remaining_weight": TARGET_G})
        print("PATCH ->", r.status_code, r.text[:300])
        assert r.status_code == 200
        after = _get(spoolman)
        print("after:", after["remaining_weight"], after["used_weight"])
        assert after["remaining_weight"] == pytest.approx(TARGET_G, abs=0.01)                  # exactly what was sent
        assert after["used_weight"] == pytest.approx(_initial(after) - TARGET_G, abs=0.01)     # the coupling goes this way
        assert _get(spoolman)["remaining_weight"] == pytest.approx(TARGET_G, abs=0.01)         # stable on a second read
    finally:
        _patch(spoolman, {"used_weight": before["used_weight"]})


def test_sending_the_same_remaining_weight_twice_is_harmless(spoolman, require_write):
    before = _get(spoolman)
    try:
        assert _patch(spoolman, {"remaining_weight": TARGET_G}).status_code == 200
        once = _get(spoolman)
        assert _patch(spoolman, {"remaining_weight": TARGET_G}).status_code == 200
        twice = _get(spoolman)
        assert (twice["remaining_weight"], twice["used_weight"]) == (once["remaining_weight"], once["used_weight"])
    finally:
        _patch(spoolman, {"used_weight": before["used_weight"]})


def test_sending_both_weights_at_once_is_rejected(spoolman, require_write):
    """So the provider must send exactly one of them (it only ever sends remaining_weight)."""
    before = _get(spoolman)
    try:
        r = _patch(spoolman, {"remaining_weight": TARGET_G, "used_weight": 1.0})
        print("PATCH both ->", r.status_code, r.text[:300])
        assert r.status_code == 400
        assert _get(spoolman)["used_weight"] == pytest.approx(before["used_weight"], abs=0.01)  # nothing changed
    finally:
        _patch(spoolman, {"used_weight": before["used_weight"]})
