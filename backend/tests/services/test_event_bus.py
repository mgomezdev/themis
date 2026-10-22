import asyncio
from collections import Counter

from app.services.events import Event, EventBus, event_bus
from app.services.printer_events import AmsChanged, PrinterStateChanged, PrintCompleted


async def test_publish_delivers_same_event_object_to_subscriber():
    bus = EventBus()
    received: list[Event] = []

    async def handler(event: PrintCompleted) -> None:
        received.append(event)

    bus.subscribe(PrintCompleted, handler)
    event = PrintCompleted(printer_id=1, state={"x": 1})
    await bus.publish(event)
    await asyncio.wait_for(bus.drain(), timeout=2)

    assert len(received) == 1
    assert received[0] is event


async def test_subscriber_receives_only_its_event_type():
    bus = EventBus()
    received: list[Event] = []

    async def handler(event: PrintCompleted) -> None:
        received.append(event)

    bus.subscribe(PrintCompleted, handler)
    await bus.publish(PrinterStateChanged(printer_id=1, state="RUNNING"))
    await asyncio.wait_for(bus.drain(), timeout=2)
    assert received == []

    done = PrintCompleted(printer_id=1, state="FINISH")
    await bus.publish(done)
    await asyncio.wait_for(bus.drain(), timeout=2)
    assert received == [done]


async def test_base_event_subscriber_receives_subclass_events():
    bus = EventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe(Event, handler)
    event = PrintCompleted(printer_id=7, state=None)
    await bus.publish(event)
    await asyncio.wait_for(bus.drain(), timeout=2)

    assert received == [event]
    assert isinstance(received[0], PrintCompleted)


async def test_publish_returns_before_slow_handler_finishes():
    bus = EventBus()
    gate = asyncio.Event()
    started = asyncio.Event()
    completed: list[int] = []

    async def slow(event: PrintCompleted) -> None:
        started.set()
        await gate.wait()
        completed.append(event.printer_id)

    bus.subscribe(PrintCompleted, slow)
    await asyncio.wait_for(bus.publish(PrintCompleted(printer_id=3, state=None)), timeout=1)

    # publish() has returned; the handler is in flight but has not completed.
    await asyncio.wait_for(started.wait(), timeout=2)
    assert completed == []

    gate.set()
    await asyncio.wait_for(bus.drain(), timeout=2)
    assert completed == [3]


async def test_handler_exception_does_not_block_other_handlers_or_publisher():
    bus = EventBus()
    received: list[PrintCompleted] = []

    async def failing(event: PrintCompleted) -> None:
        raise RuntimeError("boom")

    async def healthy(event: PrintCompleted) -> None:
        received.append(event)

    bus.subscribe(PrintCompleted, failing)
    bus.subscribe(PrintCompleted, healthy)
    event = PrintCompleted(printer_id=2, state=None)

    await asyncio.wait_for(bus.publish(event), timeout=1)  # must not raise
    await asyncio.wait_for(bus.drain(), timeout=2)

    assert received == [event]


async def test_hung_handler_is_abandoned_after_timeout_and_others_still_run():
    bus = EventBus(handler_timeout=0.05)
    never = asyncio.Event()
    hung_entered: list[int] = []
    received: list[PrintCompleted] = []

    async def hangs(event: PrintCompleted) -> None:
        hung_entered.append(event.printer_id)
        await never.wait()  # never set: exceeds handler_timeout

    async def healthy(event: PrintCompleted) -> None:
        received.append(event)

    bus.subscribe(PrintCompleted, hangs)
    bus.subscribe(PrintCompleted, healthy)
    event = PrintCompleted(printer_id=4, state=None)

    await asyncio.wait_for(bus.publish(event), timeout=1)
    await asyncio.wait_for(bus.drain(), timeout=2)  # bounded: the timed-out handler does not hold drain

    assert hung_entered == [4]
    assert received == [event]


async def test_publish_with_no_subscribers_is_a_noop():
    bus = EventBus()
    result = await asyncio.wait_for(bus.publish(PrintCompleted(printer_id=1, state=None)), timeout=1)
    await asyncio.wait_for(bus.drain(), timeout=2)

    assert result is None


async def test_each_event_delivered_exactly_once_to_each_subscriber():
    bus = EventBus()
    seen_a: Counter[int] = Counter()
    seen_b: Counter[int] = Counter()

    async def handler_a(event: PrintCompleted) -> None:
        seen_a[event.printer_id] += 1

    async def handler_b(event: PrintCompleted) -> None:
        seen_b[event.printer_id] += 1

    bus.subscribe(PrintCompleted, handler_a)
    bus.subscribe(PrintCompleted, handler_b)
    for i in range(5):
        await bus.publish(PrintCompleted(printer_id=i, state=None))
    await asyncio.wait_for(bus.drain(), timeout=2)

    assert seen_a == Counter({i: 1 for i in range(5)})
    assert seen_b == Counter({i: 1 for i in range(5)})


async def test_drain_returns_after_pending_handlers_finish():
    bus = EventBus()
    gate = asyncio.Event()
    recorded: list[int] = []

    async def handler(event: PrintCompleted) -> None:
        await gate.wait()
        recorded.append(event.printer_id)

    bus.subscribe(PrintCompleted, handler)
    for i in range(3):
        await bus.publish(PrintCompleted(printer_id=i, state=None))

    drain_task = asyncio.create_task(bus.drain(timeout=5.0))
    gate.set()
    await asyncio.wait_for(drain_task, timeout=2)

    assert sorted(recorded) == [0, 1, 2]


def test_printer_event_types_carry_their_fields():
    completed = PrintCompleted(printer_id=1, state={"x": 1})
    changed = PrinterStateChanged(printer_id=2, state="IDLE")
    ams = AmsChanged(printer_id=3, trays=[{"slot": 0}])

    assert completed.printer_id == 1
    assert completed.state == {"x": 1}
    assert changed.printer_id == 2
    assert changed.state == "IDLE"
    assert ams.printer_id == 3
    assert ams.trays == [{"slot": 0}]
    assert isinstance(completed, Event)


def test_module_singleton_is_an_event_bus():
    assert isinstance(event_bus, EventBus)


async def test_a_failing_handler_is_logged_with_its_event_type(caplog):
    import logging
    bus = EventBus()

    class Boom(Event):
        pass

    async def explode(_event):
        raise RuntimeError("handler boom")

    bus.subscribe(Boom, explode)
    with caplog.at_level(logging.ERROR, logger="app"):
        await bus.publish(Boom())
        await bus.drain()

    assert any("explode" in r.getMessage() and "Boom" in r.getMessage() for r in caplog.records)
