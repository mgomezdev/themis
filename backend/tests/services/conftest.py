"""Shared fixtures for services tests.

Patches the slicing-provider health preflight in the queue engine so tests can
reach the slicing/printing steps without a live sidecar.
"""
import pytest
from unittest.mock import patch

from tests.fake_providers import FakeSlicingProvider


@pytest.fixture(autouse=True)
def mock_laminus_health(request):
    """Bypass the slicing provider health check in QueueEngine._try_claim_for_printer."""
    with patch("app.services.queue_engine.get_slicing_provider", return_value=FakeSlicingProvider()):
        yield
