"""Printer clients' callbacks publish typed events on the process bus; the printer manager acts on them."""
import asyncio

from app.services.events import event_bus
from app.services.printer_events import AmsChanged, PrinterStateChanged, PrintCompleted
from app.services.printer_manager import printer_manager


class _FakeClient:
    printer_type = "fake"
    _on_ams_change = None  # vendors that report AMS declare this attribute

    def __init__(self) -> None:
        self.connect_loop = None

    def connect(self, loop=None) -> None:
        self.connect_loop = loop


async def _next(received: list, n: int, timeout: float = 2.0):
    async def wait():
        while len(received) < n:
            await asyncio.sleep(0)
    await asyncio.wait_for(wait(), timeout)


async def test_client_callbacks_publish_state_complete_and_ams_events_for_their_printer():
    seen: list = []
    event_bus.subscribe(PrinterStateChanged, lambda e: _record(seen, e))
    event_bus.subscribe(PrintCompleted, lambda e: _record(seen, e))
    event_bus.subscribe(AmsChanged, lambda e: _record(seen, e))
    client = _FakeClient()
    type(printer_manager).connect_printer(printer_manager, 42, client)  # the real method; conftest stubs the instance attribute

    await client._on_state_change({"s": 1})
    await client._on_print_complete({"s": 2})
    await client._on_ams_change([{"slot": 0}])
    await _next(seen, 3)

    assert any(isinstance(e, PrinterStateChanged) and e.printer_id == 42 and e.state == {"s": 1} for e in seen)
    assert any(isinstance(e, PrintCompleted) and e.printer_id == 42 and e.state == {"s": 2} for e in seen)
    assert any(isinstance(e, AmsChanged) and e.printer_id == 42 and e.trays == [{"slot": 0}] for e in seen)


async def _record(seen: list, event) -> None:
    seen.append(event)
