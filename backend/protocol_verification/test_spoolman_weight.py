"""Spoolman absolute-weight assumption behind the deduction model (BIZ-202 D6, spec §3.2): Themis sets a spool's weight
with `PATCH /api/v1/spool/{id}` `{"remaining_weight": N}` — never a delta — so that re-sending is harmless.

Read-only check always; the write checks need THEMIS_VERIFY_ALLOW_WRITE=1 and overwrite the TEST spool's weight.
Each write check restores the original `used_weight` afterwards and FAILS LOUDLY (printing the original value) if the
restore did not take, so a real spool is never left silently altered. Against the mock the idempotency/stability checks
cannot fail by construction; their value is against a real instance."""
import contextlib

import pytest


def _get(spoolman) -> dict:
    r = spoolman.http.get(f"/api/v1/spool/{spoolman.spool_id}")
    r.raise_for_status()
    return r.json()


def _patch(spoolman, body: dict):
    return spoolman.http.patch(f"/api/v1/spool/{spoolman.spool_id}", json=body)


def _initial(spool: dict) -> float | None:
    return spool.get("initial_weight") or (spool.get("filament") or {}).get("weight")


def _target(before: dict) -> float:
    """A remaining weight that is valid for this spool and distinct from what it holds now."""
    initial = _initial(before)
    if not initial or initial < 20:
        pytest.skip("the test spool needs an initial weight (filament `weight`) of at least 20 g")
    target = round(initial / 2, 1)
    return target if abs(target - before["remaining_weight"]) > 1 else round(initial / 4, 1)


@contextlib.contextmanager
def _restored(spoolman):
    """Yield the spool as it was; afterwards put `used_weight` back and verify it (never silently)."""
    before = _get(spoolman)
    print(f"spool {spoolman.spool_id} before: used_weight={before['used_weight']!r} remaining_weight={before['remaining_weight']!r}")
    try:
        yield before
    finally:
        r = _patch(spoolman, {"used_weight": before["used_weight"]})
        now = _get(spoolman) if r.status_code == 200 else {}
        restored = r.status_code == 200 and now.get("used_weight") == pytest.approx(before["used_weight"], abs=0.01)
        if not restored:
            pytest.fail(f"COULD NOT RESTORE spool {spoolman.spool_id}: set used_weight back to {before['used_weight']!r} by hand "
                        f"(restore PATCH -> {r.status_code} {r.text[:200]})", pytrace=False)


def test_spool_read_carries_the_weight_fields_the_provider_maps(spoolman):
    s = _get(spoolman)
    print({k: s.get(k) for k in ("remaining_weight", "used_weight", "initial_weight")}, "filament.weight:", (s.get("filament") or {}).get("weight"))
    assert isinstance(s["remaining_weight"], (int, float)) and isinstance(s["used_weight"], (int, float))
    initial = _initial(s)
    assert initial is not None, "the test spool's filament needs a `weight` (or the spool an initial_weight)"
    # one quantity, two views (Spoolman reports remaining clamped at 0 for an overdrawn spool)
    assert s["remaining_weight"] == pytest.approx(max(0.0, initial - s["used_weight"]), abs=0.01)


def test_patching_remaining_weight_sets_it_exactly_and_is_not_recomputed(spoolman, require_write):
    with _restored(spoolman) as before:
        target = _target(before)
        r = _patch(spoolman, {"remaining_weight": target})
        print("PATCH ->", r.status_code, r.text[:300])
        assert r.status_code == 200
        after = _get(spoolman)
        print("after:", after["remaining_weight"], after["used_weight"])
        assert after["remaining_weight"] == pytest.approx(target, abs=0.01)                    # exactly what was sent
        assert after["used_weight"] == pytest.approx(_initial(after) - target, abs=0.01)       # the coupling goes this way
        assert _get(spoolman)["remaining_weight"] == pytest.approx(target, abs=0.01)           # stable on a second read


def test_sending_the_same_remaining_weight_twice_is_harmless(spoolman, require_write):
    with _restored(spoolman) as before:
        target = _target(before)
        assert _patch(spoolman, {"remaining_weight": target}).status_code == 200
        once = _get(spoolman)
        assert _patch(spoolman, {"remaining_weight": target}).status_code == 200
        twice = _get(spoolman)
        assert (twice["remaining_weight"], twice["used_weight"]) == (once["remaining_weight"], once["used_weight"])


def test_sending_both_weights_at_once_is_rejected(spoolman, require_write):
    """So the provider must send exactly one of them (it only ever sends remaining_weight)."""
    with _restored(spoolman) as before:
        r = _patch(spoolman, {"remaining_weight": _target(before), "used_weight": 1.0})
        print("PATCH both ->", r.status_code, r.text[:300])
        assert r.status_code == 400
        assert _get(spoolman)["used_weight"] == pytest.approx(before["used_weight"], abs=0.01)  # nothing changed
