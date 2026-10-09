"""EventBus.subscribe(..., timeout=) per-subscription limit, and which PrinterManager handlers get which limit."""
import asyncio

from app.services import printer_manager as pm_module
from app.services.events import EventBus
from app.services.printer_events import AmsChanged, PrinterStateChanged, PrintCompleted
from app.services.printer_manager import PrinterManager


def _slow(record: list, label: str, delay: float = 0.3):
    async def handler(event) -> None:
        await asyncio.sleep(delay)
        record.append(label)
    return handler


async def _run(bus: EventBus, event) -> None:
    await asyncio.wait_for(bus.publish(event), timeout=1)
    await asyncio.wait_for(bus.drain(), timeout=3)


async def test_timeout_none_handler_outlives_the_bus_limit_while_a_default_handler_is_abandoned():
    bus = EventBus(handler_timeout=0.05)
    done: list[str] = []
    bus.subscribe(PrintCompleted, _slow(done, "unlimited"), timeout=None)
    bus.subscribe(PrintCompleted, _slow(done, "default"))

    await _run(bus, PrintCompleted(printer_id=1, state=None))

    assert done == ["unlimited"]


async def test_explicit_float_timeout_shorter_than_the_bus_default_abandons_a_slow_handler():
    bus = EventBus(handler_timeout=5.0)
    done: list[str] = []
    bus.subscribe(PrintCompleted, _slow(done, "short", delay=0.5), timeout=0.05)
    bus.subscribe(PrintCompleted, _slow(done, "default", delay=0.1))

    await _run(bus, PrintCompleted(printer_id=1, state=None))

    assert done == ["default"]


async def test_printer_manager_subscribes_completion_without_a_limit_and_state_and_ams_with_the_default(monkeypatch):
    bus = EventBus(handler_timeout=0.05)
    monkeypatch.setattr(pm_module, "event_bus", bus)
    done: list[str] = []
    mgr = PrinterManager()
    mgr.on_print_complete = _slow_args(done, "complete")
    mgr.on_state_change = _slow_args(done, "state")
    mgr.on_ams_change = _slow_args(done, "ams")
    mgr.subscribe_events()

    await bus.publish(PrintCompleted(printer_id=1, state=None))
    await bus.publish(PrinterStateChanged(printer_id=1, state="RUNNING"))
    await bus.publish(AmsChanged(printer_id=1, trays=[]))
    await asyncio.wait_for(bus.drain(), timeout=3)

    assert done == ["complete"]


def _slow_args(record: list, label: str):
    async def handler(*args) -> None:
        await asyncio.sleep(0.3)
        record.append(label)
    return handler
