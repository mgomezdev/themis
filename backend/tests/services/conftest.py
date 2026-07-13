"""Shared fixtures for services tests.

Patches the Laminus sidecar health preflight in the queue engine so tests can
reach the slicing/printing steps without a live sidecar.
"""
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def mock_laminus_health(request):
    """Bypass the Laminus health check in QueueEngine._try_claim_for_printer."""
    mock_resp = MagicMock()
    mock_resp.is_success = True
    with (
        patch("app.services.queue_engine.get_laminus_sidecar_url", return_value="http://fake-laminus"),
        patch("httpx.get", return_value=mock_resp),
    ):
        yield
