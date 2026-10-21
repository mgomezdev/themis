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


@pytest.fixture
def listen():
    """`seen = listen("job.failed")`: a core subscriber collecting that event's envelopes, removed (with its delivery lane) afterwards so
    it cannot leak into a later test (a lingering lane makes `settle_background_tasks` wait forever)."""
    from app.eventing.hub import hub
    keys: list[str] = []

    def _listen(event: str) -> list:
        seen: list = []

        async def handler(envelope):
            seen.append(envelope)

        name = f"test.listen.{event}.{len(keys)}"
        hub.subscribe(event, handler, name=name)
        keys.append(f"core:{name}")
        return seen

    yield _listen
    for key in keys:
        hub._core.pop(key, None)
        lane = hub._lanes.pop(key, None)
        if lane is not None:
            lane.task.cancel()
