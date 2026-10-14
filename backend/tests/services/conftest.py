"""Shared fixtures for services tests.

Installs a healthy fake slicing provider as the default `gcode.produce` provider so the queue's claim-time resolution (BIZ-291)
reaches the slicing/printing steps without a live sidecar.
"""
import pytest

from tests.fake_providers import FakeSlicingProvider
from tests.slicing_env import slicing_provider


@pytest.fixture(autouse=True)
def mock_laminus_health(request):
    """A healthy slicing provider for QueueEngine._try_claim_for_printer (override per test with `slicing_provider(...)`)."""
    with slicing_provider(FakeSlicingProvider()):
        yield
