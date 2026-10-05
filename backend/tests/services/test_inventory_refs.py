"""The provider-namespaced ref normalizer (BIZ-217): slot dual-write resolution, AMS key preservation, material asks."""
import pytest

from app.services.inventory import refs
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import enable_spoolman, use_provider

INV = {"provider": "spoolman", "spool_ref": "7"}


# --- normalize_slot ----------------------------------------------------------------------------------------------------------

def test_an_old_client_that_only_sends_the_legacy_key_gets_the_namespaced_binding():
    assert refs.normalize_slot({"slot": 0, "spoolman_spool_id": 7}) == {"slot": 0, "spoolman_spool_id": 7, "inventory": INV}
    assert refs.normalize_slot({"slot": 0, "spoolman_spool_id": "7"})["inventory"] == INV


def test_a_new_client_that_only_sends_inventory_gets_the_legacy_key_mirrored_for_the_legacy_provider():
    assert refs.normalize_slot({"slot": 0, "inventory": INV}) == {"slot": 0, "inventory": INV, "spoolman_spool_id": "7"}


def test_a_binding_to_another_provider_clears_the_legacy_key():
    out = refs.normalize_slot({"slot": 0, "spoolman_spool_id": 7, "inventory": {"provider": "local", "spool_ref": "3"}},
                              previous={"slot": 0, "spoolman_spool_id": 7, "inventory": INV})
    assert out["inventory"] == {"provider": "local", "spool_ref": "3"} and out["spoolman_spool_id"] is None


def test_both_keys_consistent_is_a_no_op_and_keeps_the_legacy_value_type():
    slot = {"slot": 1, "spoolman_spool_id": 7, "inventory": INV}
    assert refs.normalize_slot(slot, previous=dict(slot)) == slot


def test_an_old_client_echoing_the_stale_inventory_but_editing_the_legacy_key_wins():
    """The frontend reads a slot (with `inventory`), changes `spoolman_spool_id`, and sends the whole object back."""
    prev = {"slot": 0, "spoolman_spool_id": "7", "inventory": INV}
    out = refs.normalize_slot({"slot": 0, "spoolman_spool_id": "9", "inventory": INV}, previous=prev)
    assert out["inventory"] == {"provider": "spoolman", "spool_ref": "9"} and out["spoolman_spool_id"] == "9"


def test_an_old_client_unlinking_through_the_legacy_key_unlinks_the_namespaced_binding_too():
    prev = {"slot": 0, "spoolman_spool_id": "7", "inventory": INV}
    out = refs.normalize_slot({"slot": 0, "spoolman_spool_id": None, "inventory": INV}, previous=prev)
    assert "inventory" not in out and out["spoolman_spool_id"] is None


def test_a_new_client_unlinking_with_null_inventory_unlinks_both():
    prev = {"slot": 0, "spoolman_spool_id": "7", "inventory": INV}
    out = refs.normalize_slot({"slot": 0, "inventory": None, "spoolman_spool_id": "7"}, previous=prev)
    assert "inventory" not in out and out["spoolman_spool_id"] is None


def test_when_both_keys_changed_the_inventory_wins():
    prev = {"slot": 0, "spoolman_spool_id": "7", "inventory": INV}
    out = refs.normalize_slot({"slot": 0, "spoolman_spool_id": "9", "inventory": {"provider": "spoolman", "spool_ref": "11"}}, previous=prev)
    assert out["inventory"]["spool_ref"] == "11" and out["spoolman_spool_id"] == "11"


def test_an_old_client_cannot_wipe_a_binding_it_cannot_see():
    prev = {"slot": 0, "inventory": {"provider": "local", "spool_ref": "3"}, "spoolman_spool_id": None}
    out = refs.normalize_slot({"slot": 0, "spoolman_spool_id": None}, previous=prev)          # no `inventory` key sent
    assert out["inventory"] == {"provider": "local", "spool_ref": "3"}


def test_slots_without_a_binding_stay_free_of_inventory_keys():
    assert refs.normalize_slot({"slot": 2, "type": "PLA"}) == {"slot": 2, "type": "PLA"}
    assert "inventory" not in refs.normalize_slot({"slot": 2, "spoolman_spool_id": "", "inventory": {"provider": "", "spool_ref": ""}})


def test_normalize_slots_matches_stored_slots_by_slot_number_not_position():
    prev = [{"slot": 0, "spoolman_spool_id": "1", "inventory": {"provider": "spoolman", "spool_ref": "1"}},
            {"slot": 1, "spoolman_spool_id": "2", "inventory": {"provider": "spoolman", "spool_ref": "2"}}]
    new = [{"slot": 1, "spoolman_spool_id": "2", "inventory": {"provider": "spoolman", "spool_ref": "2"}},      # reordered, unchanged
           {"slot": 0, "spoolman_spool_id": "5", "inventory": {"provider": "spoolman", "spool_ref": "1"}}]      # old client re-linked slot 0
    out = refs.normalize_slots(new, prev)
    assert [s["inventory"]["spool_ref"] for s in out] == ["2", "5"]
    assert refs.normalize_slots(None) == [] and refs.normalize_slots([]) == []


# --- AMS merge ------------------------------------------------------------------------------------------------------------------

def test_preserve_slot_keys_keeps_every_themis_owned_key_and_takes_the_rest_from_the_report():
    stored = {"slot": 0, "type": "OLD", "filament_profile": "PLA @X", "spoolman_spool_id": "7", "inventory": INV, "extra": "x"}
    fresh = {"slot": 0, "type": "PETG", "color": "#fff", "filament_id": "GFA00"}
    out = refs.preserve_slot_keys(stored, fresh)
    assert out == {"slot": 0, "type": "PETG", "color": "#fff", "filament_id": "GFA00",          # hardware facts from the report
                   "filament_profile": "PLA @X", "spoolman_spool_id": "7", "inventory": INV}   # Themis' links kept
    assert refs.preserve_slot_keys({"slot": 0}, {"slot": 0})["spoolman_spool_id"] is None         # legacy keys always present
    assert "inventory" not in refs.preserve_slot_keys({"slot": 0}, {"slot": 0})


# --- slot_spool_ref ---------------------------------------------------------------------------------------------------------------

async def test_slot_spool_ref_follows_the_active_provider(session_factory):
    slot = {"spoolman_spool_id": "7", "inventory": INV}
    assert refs.slot_spool_ref(slot) is None                                                  # no provider
    await use_provider(FakeInventoryProvider(), plugin_id="spoolman")
    assert refs.slot_spool_ref(slot) == "7"
    assert refs.slot_spool_ref({"spoolman_spool_id": "7"}) == "7"                              # pre-migration slot
    await use_provider(FakeInventoryProvider(), plugin_id="other_inventory")
    assert refs.slot_spool_ref(slot) is None                                                  # never applied to another provider
    assert refs.slot_spool_ref({"inventory": {"provider": "other_inventory", "spool_ref": "3"}}) == "3"
    assert refs.slot_spool_ref(None) is None and refs.slot_spool_ref({}) is None


# --- material asks --------------------------------------------------------------------------------------------------------------------

def test_material_accepts_the_legacy_id_and_fills_the_namespaced_pair():
    assert refs.material(12) == (12, "spoolman", "12")
    assert refs.material(None) == (None, None, None) and refs.material(None, "spoolman", None) == (None, None, None)


def test_material_ref_wins_and_only_the_legacy_provider_gets_an_integer_mirror():
    assert refs.material(99, "spoolman", "12") == (12, "spoolman", "12")
    assert refs.material(99, "local", "m-5") == (None, "local", "m-5")
    assert refs.material(None, "local", "7") == (None, "local", "7")


async def test_material_ref_without_a_provider_uses_the_active_one_or_is_an_error(session_factory):
    with pytest.raises(refs.MaterialRefError, match="material_provider"):
        refs.material(None, None, "7")
    await use_provider(FakeInventoryProvider(), plugin_id="local")
    assert refs.material(None, None, "7") == (None, "local", "7")


def test_a_legacy_provider_ref_must_be_a_number():
    with pytest.raises(refs.MaterialRefError):
        refs.material(None, "spoolman", "abc")
