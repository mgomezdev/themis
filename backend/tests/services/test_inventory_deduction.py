"""The interim deduction (read the spool, set `remaining - grams`); BIZ-218 replaces it with the snapshot + outbox model."""
import pytest

from app.plugins.kinds.filament_inventory import InvSpool
from app.services.inventory import deduction
from tests.fake_providers import FakeInventoryProvider
from tests.inventory_helpers import spool, use_provider


async def test_sets_the_weight_to_remaining_minus_grams_and_clamps_at_zero(session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0), spool("2", 5.0)])
    await use_provider(fake)

    await deduction.deduct("1", 17.5)
    await deduction.deduct("2", 20.0)

    assert fake.writes == [("1", pytest.approx(82.5)), ("2", 0.0)]


@pytest.mark.parametrize("target", ["missing", "unweighed"])
async def test_a_spool_that_cannot_be_read_or_has_no_weight_is_skipped_without_writing(session_factory, target, caplog):
    fake = FakeInventoryProvider(spools=[InvSpool(ref="1", remaining_g=None, label="x")])
    await use_provider(fake)

    await deduction.deduct("999" if target == "missing" else "1", 10.0)      # never raises

    assert fake.writes == []
    assert "skipped" in caplog.text


async def test_a_failing_provider_never_raises_into_the_caller(session_factory):
    fake = FakeInventoryProvider(spools=[spool("1", 100.0)])
    fake.fail_with = RuntimeError("down")
    await use_provider(fake)
    await deduction.deduct("1", 10.0)
    assert fake.writes == []


def test_can_deduct_needs_both_weight_capabilities():
    assert deduction.can_deduct() is False                                    # no provider
