"""The event hub (BIZ-249; design: docs/events.md).

Two delivery paths, chosen per event class (`EventDef.durability`):

* **best_effort** — `publish()`: each subscriber has its own bounded FIFO lane and worker task. The publisher never waits and never
  fails because of a subscriber; a full lane drops the new event (counted, logged). Lost on a crash.
* **durable** — `enqueue_durable(session, envelope)`: the envelope and one delivery row per subscriber are added to the *caller's*
  transaction, so they commit (or roll back) together with the state change. After commit the caller calls `wake()`; a dispatcher
  delivers each row at least once, retrying with backoff and marking it `dead` after `MAX_ATTEMPTS`. Handlers must be idempotent
  (`envelope.id` / `dedup_key` are stable across redelivery).

Plugin subscribers are resolved from the plugin host at delivery time, so disabling or removing a plugin stops its deliveries
without unregistering anything, and re-enabling resumes them. Every handler runs contained (timeout, exceptions caught, errors
redacted); one failing subscriber never affects another or the publisher."""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import EventDelivery, EventOutbox
from .definitions import EVENT_NAME_RE
from .envelope import EventEnvelope, EventError
from .redaction import redact_error
from .registry import validate_publication

logger = logging.getLogger("app")

MAX_ATTEMPTS = 8                    # durable: then the delivery is `dead` until an operator retries it
BACKOFF_BASE_S = 5.0                # durable retry delay: base * 2**(attempt-1), capped
BACKOFF_CAP_S = 900.0
DORMANT_RECHECK_S = 60.0            # durable: a delivery whose subscriber is not running is looked at again after this long
DORMANT_DEAD_AFTER = timedelta(days=7)
RETENTION = timedelta(days=7)       # outbox rows whose deliveries all succeeded are purged after this
DEAD_RETENTION = timedelta(days=30)  # rows with a dead delivery are kept this long so an operator can retry them
PER_SUBSCRIBER_BATCH = 200          # durable: due deliveries picked per subscriber per cycle
PUBLISHED_MEMORY = 10_000           # best_effort: logical events remembered for publish-level deduplication
BACKLOG_WARN = 10_000               # durable: pending deliveries per subscriber that earn a warning
IDLE_WAKE_S = 30.0

Handler = Callable[[EventEnvelope], Awaitable[None]]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Subscriber:
    key: str                                  # "core:<name>" | "plugin:<plugin id>:<handler>"
    event: str
    timeout: float
    queue_size: int
    handler: Handler | None = None            # core subscribers
    plugin_id: str | None = None              # plugin subscribers
    handler_attr: str | None = None

    @property
    def kind(self) -> str:
        return "core" if self.plugin_id is None else "plugin"


@dataclass
class Stats:
    event: str = ""
    kind: str = "core"
    plugin_id: str | None = None
    delivered: int = 0
    failed: int = 0
    timed_out: int = 0
    dropped: int = 0                          # best_effort: lane full
    skipped_inactive: int = 0                 # subscriber (plugin) not running at delivery time
    last_error: str | None = None             # redacted
    last_error_at: str | None = None
    last_ok_at: str | None = None
    last_event_id: str | None = None


@dataclass
class _Outcome:
    ok: bool
    reason: str = "ok"                        # ok | timeout | error | inactive
    error: str | None = None


@dataclass
class _Lane:
    queue: asyncio.Queue
    task: asyncio.Task
    loop: asyncio.AbstractEventLoop


@dataclass
class _State:
    factory: async_sessionmaker[AsyncSession] | None = None
    wake: asyncio.Event | None = None
    loop_task: asyncio.Task | None = None
    inflight: dict[str, asyncio.Task] = field(default_factory=dict)
    stopping: bool = False
    last_purge: datetime | None = None


class EventHub:
    def __init__(self, host=None) -> None:
        self._host = host                       # a PluginHost; None = the process-wide `plugin_host` (resolved lazily)
        self._core: dict[str, Subscriber] = {}
        self._stats: dict[str, Stats] = {}
        self._lanes: dict[str, _Lane] = {}
        self._published: OrderedDict[str, None] = OrderedDict()
        self._s = _State()

    @property
    def host(self):
        if self._host is None:
            from ..plugins.host import plugin_host
            self._host = plugin_host
        return self._host

    # --- subscribing ----------------------------------------------------------------------------------------------

    def subscribe(self, event: str, handler: Handler, *, name: str, timeout: float = 10.0, queue_size: int = 1000) -> Subscriber:
        """Register a core subscriber. `name` is its stable identity (durable delivery rows are keyed on it): never rename one."""
        if not EVENT_NAME_RE.match(event):
            raise EventError(f"{event!r} is not an event name")
        key = f"core:{name}"
        if key in self._core:
            raise EventError(f"core subscriber {name!r} is already registered")
        if timeout <= 0 or queue_size < 1:
            raise EventError("timeout and queue_size must be positive")
        sub = Subscriber(key, event, timeout, queue_size, handler=handler)
        self._core[key] = sub
        self._stats_for(sub)
        return sub

    def unsubscribe(self, name: str) -> None:
        self._core.pop(f"core:{name}", None)

    def has_subscriber(self, name: str) -> bool:
        return f"core:{name}" in self._core

    def configure(self, factory: async_sessionmaker[AsyncSession] | None) -> None:
        """Set the database handlers use without starting the dispatcher (tests, and `start` does it too)."""
        self._s.factory = factory

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """The database the outbox lives in: core handlers open their sessions here, so they work on the same DB as the event."""
        if self._s.factory is None:
            raise RuntimeError("the event hub has no session factory (not started)")
        return self._s.factory

    def subscribers_for(self, event: str) -> list[Subscriber]:
        """Everyone who would receive `event` right now: core subscribers, then running plugins' subscriptions."""
        out = [s for s in self._core.values() if s.event == event]
        out += [Subscriber(f"plugin:{pid}:{sub.handler}", event, sub.timeout, sub.queue_size, plugin_id=pid, handler_attr=sub.handler)
                for pid, sub in self.host.event_subscriptions(event)]
        return out

    def _resolve(self, key: str, event: str) -> Subscriber | None:
        if key.startswith("core:"):
            sub = self._core.get(key)
            return sub if sub is not None and sub.event == event else None
        return next((s for s in self.subscribers_for(event) if s.key == key), None)

    def _stats_for(self, sub: Subscriber) -> Stats:
        st = self._stats.get(sub.key)
        if st is None:
            st = self._stats[sub.key] = Stats(event=sub.event, kind=sub.kind, plugin_id=sub.plugin_id)
        return st

    def _validate(self, envelope: EventEnvelope, as_plugin: str | None):
        d = validate_publication(envelope, as_plugin=as_plugin)
        if as_plugin is not None and not self.host.is_enabled(as_plugin):         # a disabled plugin publishes nothing
            raise EventError(f"plugin {as_plugin!r} is not enabled")
        return d

    # --- best-effort path -----------------------------------------------------------------------------------------

    async def publish(self, envelope: EventEnvelope, *, as_plugin: str | None = None) -> bool:
        """Deliver a best-effort event to its subscribers without waiting for them. Raises `EventError` for a malformed or
        unauthorised envelope (a programming error in the publisher); never for anything a subscriber does. Returns False when
        the same logical event (`dedup_key`, else `id`) was already published."""
        d = self._validate(envelope, as_plugin)
        if d.durability == "durable":
            raise EventError(f"{envelope.name} is durable: publish it with enqueue_durable() inside the state-changing transaction")
        identity = envelope.dedup_key or envelope.id
        if identity in self._published:
            return False
        self._published[identity] = None
        while len(self._published) > PUBLISHED_MEMORY:
            self._published.popitem(last=False)
        for sub in self.subscribers_for(envelope.name):
            lane = self._lane(sub)
            self._stats_for(sub)
            try:
                lane.queue.put_nowait(envelope)
            except asyncio.QueueFull:
                st = self._stats_for(sub)
                st.dropped += 1
                if st.dropped == 1 or st.dropped % 100 == 0:
                    logger.warning("Event subscriber %s is %d events behind; dropped %s (%d dropped so far)",
                                   sub.key, sub.queue_size, envelope.name, st.dropped)
        return True

    def _lane(self, sub: Subscriber) -> _Lane:
        loop = asyncio.get_running_loop()
        lane = self._lanes.get(sub.key)
        if lane is None or lane.loop is not loop or lane.task.done():
            queue: asyncio.Queue = asyncio.Queue(maxsize=sub.queue_size)
            lane = _Lane(queue, loop.create_task(self._work(sub.key, queue), name=f"event-lane-{sub.key}"), loop)
            self._lanes[sub.key] = lane
        return lane

    async def _work(self, key: str, queue: asyncio.Queue) -> None:
        while True:
            envelope: EventEnvelope = await queue.get()
            try:
                sub = self._resolve(key, envelope.name)
                if sub is None:
                    st = self._stats.get(key)
                    if st is not None:
                        st.skipped_inactive += 1
                else:
                    outcome = await self._invoke(sub, envelope)
                    self._record(sub, envelope, outcome)
            except asyncio.CancelledError:
                raise
            except Exception:                                   # a bug here must not kill the lane
                logger.exception("Event lane %s failed on %s", key, envelope.name)
            finally:
                queue.task_done()

    # --- invocation -----------------------------------------------------------------------------------------------

    async def _invoke(self, sub: Subscriber, envelope: EventEnvelope) -> _Outcome:
        if sub.plugin_id is not None:
            plugin_host = self.host
            res = await plugin_host.deliver_event(sub.plugin_id, sub.handler_attr or "", envelope, timeout=sub.timeout)
            if res.ok:
                return _Outcome(True)
            return _Outcome(False, res.reason if res.reason in ("timeout", "inactive") else "error",
                            redact_error(plugin_host.redact(sub.plugin_id, res.error or "failed")))
        assert sub.handler is not None
        try:
            await asyncio.wait_for(sub.handler(envelope), timeout=sub.timeout)
            return _Outcome(True)
        except asyncio.TimeoutError:
            return _Outcome(False, "timeout", f"timed out after {sub.timeout:g}s")
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            return _Outcome(False, "error", "handler was cancelled")
        except Exception as e:
            return _Outcome(False, "error", redact_error(e))

    def _record(self, sub: Subscriber, envelope: EventEnvelope, outcome: _Outcome) -> None:
        st = self._stats_for(sub)
        st.last_event_id = envelope.id
        if outcome.ok:
            st.delivered += 1
            st.last_ok_at = _now()
            return
        if outcome.reason == "inactive":
            st.skipped_inactive += 1
            return
        st.failed += 1
        st.timed_out += outcome.reason == "timeout"
        st.last_error, st.last_error_at = outcome.error, _now()
        logger.warning("Event subscriber %s failed on %s: %s", sub.key, envelope.name, outcome.error)

    async def drain(self, timeout: float = 5.0) -> None:
        """Wait until every best-effort lane is empty and idle and no durable delivery is in flight (tests, shutdown)."""
        async def _wait() -> None:
            for lane in list(self._lanes.values()):
                await lane.queue.join()
            while True:
                for key in [k for k, t in self._s.inflight.items() if t.done()]:     # a finished task's callback may never have run
                    self._s.inflight.pop(key, None)
                if not self._s.inflight:
                    return
                await asyncio.gather(*list(self._s.inflight.values()), return_exceptions=True)
        await asyncio.wait_for(_wait(), timeout=timeout)

    # --- durable path ---------------------------------------------------------------------------------------------

    async def enqueue_durable(self, session: AsyncSession, envelope: EventEnvelope, *, as_plugin: str | None = None) -> bool:
        """Add a durable event and its delivery rows to the caller's transaction (no commit). Call `wake()` after the commit.
        Returns False, adding nothing, when an event with the same `dedup_key` is already stored (a repeated publication of
        one logical event). Subscribers are those running now; a plugin enabled later does not receive past events."""
        d = self._validate(envelope, as_plugin)
        if d.durability != "durable":
            raise EventError(f"{envelope.name} is best_effort: publish it with publish()")
        # INSERT .. ON CONFLICT DO NOTHING: a repeated dedup_key stores nothing and leaves the caller's transaction intact (a
        # SAVEPOINT would do the same, but pysqlite releases savepoints taken outside a BEGIN, which can commit the row early).
        inserted = (await session.execute(
            sqlite_insert(EventOutbox).values(
                event_id=envelope.id, dedup_key=envelope.dedup_key, name=envelope.name, schema_version=envelope.schema_version,
                source=envelope.source, occurred_at=envelope.occurred_at, envelope=envelope.model_dump(), created_at=_now())
            .on_conflict_do_nothing().returning(EventOutbox.id))).scalar_one_or_none()
        if inserted is None:
            return False
        for sub in self.subscribers_for(envelope.name):
            session.add(EventDelivery(outbox_id=inserted, subscriber=sub.key, next_attempt_at=_now()))
        return True

    def wake(self) -> None:
        """Tell the dispatcher there is new work (call after the publishing transaction commits)."""
        if self._s.wake is not None:
            self._s.wake.set()

    async def start(self, factory: async_sessionmaker[AsyncSession]) -> None:
        await self.stop()
        self._s = _State(factory=factory, wake=asyncio.Event())
        self._s.loop_task = asyncio.get_running_loop().create_task(self._dispatch_loop(), name="event-dispatcher")
        self.wake()                                               # deliveries left pending by a previous run

    async def stop(self) -> None:
        s = self._s
        s.stopping = True
        tasks = [t for t in [s.loop_task, *s.inflight.values(), *(l.task for l in self._lanes.values())] if t is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._lanes.clear()
        self._published.clear()                                   # the publish-level dedup memory is per run
        self._s = _State()

    async def _dispatch_loop(self) -> None:
        s = self._s
        assert s.wake is not None
        while not s.stopping:
            try:
                await self._dispatch_due()
                await self._maybe_purge()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Event dispatcher cycle failed")
            try:
                delay = await self._next_due_delay()
            except Exception:
                delay = IDLE_WAKE_S
            try:
                await asyncio.wait_for(s.wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            s.wake.clear()

    async def _next_due_delay(self) -> float:
        """Seconds until the earliest pending delivery is due (so a retry fires on time), at most `IDLE_WAKE_S`."""
        assert self._s.factory is not None
        async with self._s.factory() as session:
            q = select(func.min(EventDelivery.next_attempt_at)).where(EventDelivery.status == "pending")
            if self._s.inflight:                                  # a subscriber mid-delivery wakes the loop itself when it finishes
                q = q.where(EventDelivery.subscriber.not_in(list(self._s.inflight)))
            nxt = (await session.execute(q)).scalar()
        if nxt is None:
            return IDLE_WAKE_S
        return max(0.01, min(IDLE_WAKE_S, (_parse(nxt) - datetime.now(timezone.utc)).total_seconds()))

    async def deliver_pending(self, factory: async_sessionmaker[AsyncSession] | None = None, *, max_rounds: int = 100) -> None:
        """Deliver everything that is due right now and wait for it. For tests (it may swap in `factory` for the call); safe
        alongside the dispatcher loop, which owns any subscriber already mid-delivery."""
        previous = self._s.factory
        if factory is not None:
            self._s.factory = factory
        try:
            for _ in range(max_rounds):
                started = await self._dispatch_due()
                await self.drain()
                if not started:
                    return
        finally:
            if factory is not None:
                self._s.factory = previous

    async def _dispatch_due(self) -> int:
        """Start a delivery task for every idle subscriber with due work; returns how many were started."""
        s = self._s
        assert s.factory is not None
        groups: dict[str, list[int]] = {}
        async with s.factory() as session:
            due = EventDelivery.status == "pending", EventDelivery.next_attempt_at <= _now()
            idle = EventDelivery.subscriber.not_in(list(s.inflight)) if s.inflight else EventDelivery.id > 0
            subscribers = [r[0] for r in (await session.execute(select(EventDelivery.subscriber).where(*due, idle).distinct())).all()]
            for subscriber in subscribers:                        # per subscriber, so one with a huge backlog never crowds out another
                groups[subscriber] = [r[0] for r in (await session.execute(
                    select(EventDelivery.id).where(*due, EventDelivery.subscriber == subscriber)
                    .order_by(EventDelivery.outbox_id, EventDelivery.id).limit(PER_SUBSCRIBER_BATCH))).all()]
        for subscriber, ids in groups.items():                    # one task per subscriber: a slow one never delays another
            if subscriber in s.inflight:
                continue
            task = asyncio.get_running_loop().create_task(self._deliver_group(subscriber, ids), name=f"event-deliver-{subscriber}")
            s.inflight[subscriber] = task
            task.add_done_callback(lambda _t, k=subscriber: (s.inflight.pop(k, None), self.wake()))
        return sum(1 for subscriber in groups if subscriber in s.inflight)

    async def _deliver_group(self, subscriber: str, delivery_ids: list[int]) -> None:
        for delivery_id in delivery_ids:
            if self._s.stopping:
                return
            try:
                await self._deliver_one(delivery_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("Durable delivery %s failed unexpectedly", delivery_id)
                await self._park(delivery_id, e)

    async def _park(self, delivery_id: int, exc: Exception) -> None:
        """A delivery failed outside its handler (unreadable envelope, database error): back it off so it cannot hot-loop."""
        try:
            assert self._s.factory is not None
            async with self._s.factory() as session:
                d = await session.get(EventDelivery, delivery_id)
                if d is not None and d.status == "pending":
                    d.last_error = redact_error(exc)
                    d.next_attempt_at = _iso(datetime.now(timezone.utc) + timedelta(seconds=DORMANT_RECHECK_S))
                    await session.commit()
        except Exception:
            logger.exception("Could not park delivery %s", delivery_id)

    async def _deliver_one(self, delivery_id: int) -> None:
        factory = self._s.factory
        assert factory is not None
        async with factory() as session:
            delivery = await session.get(EventDelivery, delivery_id)
            if delivery is None or delivery.status != "pending":
                return
            outbox = await session.get(EventOutbox, delivery.outbox_id)
            if outbox is None:
                return
            now = datetime.now(timezone.utc)
            if delivery.attempts >= MAX_ATTEMPTS:               # e.g. a handler that kills the process every time
                delivery.status, delivery.last_error = "dead", delivery.last_error or "gave up: attempts exhausted"
                await session.commit()
                return
            try:
                envelope = EventEnvelope.model_validate(outbox.envelope)
            except ValueError as e:                              # stored under a schema this build no longer reads: never deliverable
                delivery.status, delivery.last_error = "dead", redact_error(f"unreadable envelope: {e}")
                await session.commit()
                return
            sub = self._resolve(delivery.subscriber, envelope.name)
            if sub is None:                                      # plugin disabled/removed: dormant, not failed
                age = now - _parse(outbox.created_at)
                if age > DORMANT_DEAD_AFTER:
                    delivery.status, delivery.last_error = "dead", "subscriber unavailable for 7 days"
                else:
                    delivery.next_attempt_at = _iso(now + timedelta(seconds=DORMANT_RECHECK_S))
                self._stats.setdefault(delivery.subscriber, Stats(event=envelope.name)).skipped_inactive += 1
                await session.commit()
                return
            delivery.attempts += 1                              # counted (and committed) before the handler runs: survives a crash
            delivery.last_attempt_at = _iso(now)
            await session.commit()
            attempts = delivery.attempts
        outcome = await self._invoke(sub, envelope)
        self._record(sub, envelope, outcome)
        async with factory() as session:
            delivery = await session.get(EventDelivery, delivery_id)
            if delivery is None:
                return
            if outcome.ok:
                delivery.status, delivery.delivered_at, delivery.last_error = "delivered", _now(), None
            else:
                delivery.last_error = outcome.error
                if outcome.reason == "inactive":                 # plugin vanished between resolve and call: not an attempt
                    delivery.attempts = max(0, attempts - 1)
                    delivery.next_attempt_at = _iso(datetime.now(timezone.utc) + timedelta(seconds=DORMANT_RECHECK_S))
                elif attempts >= MAX_ATTEMPTS:
                    delivery.status = "dead"
                else:
                    delay = min(BACKOFF_BASE_S * 2 ** (attempts - 1), BACKOFF_CAP_S)
                    delivery.next_attempt_at = _iso(datetime.now(timezone.utc) + timedelta(seconds=delay))
            await session.commit()

    async def retry(self, session: AsyncSession, delivery_id: int) -> bool:
        """Operator action: put a dead (or pending) delivery back in the queue now. Returns False for an unknown or delivered one."""
        d = await session.get(EventDelivery, delivery_id)
        if d is None or d.status == "delivered":
            return False
        d.status, d.attempts, d.next_attempt_at, d.last_error = "pending", 0, _now(), d.last_error
        await session.commit()
        self.wake()
        return True

    async def _maybe_purge(self) -> None:
        s = self._s
        now = datetime.now(timezone.utc)
        if s.factory is None or (s.last_purge is not None and now - s.last_purge < timedelta(hours=1)):
            return
        s.last_purge = now
        async with s.factory() as session:
            for subscriber, n in (await session.execute(select(EventDelivery.subscriber, func.count()).where(
                    EventDelivery.status == "pending").group_by(EventDelivery.subscriber))).all():
                if n > BACKLOG_WARN:
                    logger.warning("Event subscriber %s has %d undelivered durable events", subscriber, n)
            unfinished = select(EventDelivery.outbox_id).where(EventDelivery.status == "pending")
            troubled = select(EventDelivery.outbox_id).where(EventDelivery.status == "dead")
            ids = [r[0] for r in (await session.execute(select(EventOutbox.id).where(
                EventOutbox.id.not_in(unfinished),
                (EventOutbox.created_at < _iso(now - DEAD_RETENTION))
                | ((EventOutbox.created_at < _iso(now - RETENTION)) & EventOutbox.id.not_in(troubled))))).all()]
            if ids:
                await session.execute(delete(EventDelivery).where(EventDelivery.outbox_id.in_(ids)))
                await session.execute(delete(EventOutbox).where(EventOutbox.id.in_(ids)))
                await session.commit()

    # --- observability --------------------------------------------------------------------------------------------

    def snapshot(self) -> list[dict]:
        """In-memory per-subscriber counters (reset on restart) plus current best-effort lane depth."""
        from ..plugins import registered_plugins
        plugin_host = self.host
        for m in registered_plugins():                      # list running plugins' subscriptions before their first event
            for sub in m.subscribes:
                if any(pid == m.id for pid, _ in plugin_host.event_subscriptions(sub.event)):
                    self._stats_for(Subscriber(f"plugin:{m.id}:{sub.handler}", sub.event, sub.timeout, sub.queue_size,
                                               plugin_id=m.id, handler_attr=sub.handler))
        out = []
        for key, st in sorted(self._stats.items()):
            lane = self._lanes.get(key)
            out.append({"subscriber": key, "event": st.event, "kind": st.kind, "plugin_id": st.plugin_id, "delivered": st.delivered,
                        "failed": st.failed, "timed_out": st.timed_out, "dropped": st.dropped, "skipped_inactive": st.skipped_inactive,
                        "queue_depth": lane.queue.qsize() if lane is not None else 0, "last_error": st.last_error,
                        "last_error_at": st.last_error_at, "last_ok_at": st.last_ok_at, "last_event_id": st.last_event_id})
        for b in plugin_host.broken_subscriptions():
            out.append({"subscriber": f"plugin:{b['plugin_id']}:{b['handler']}", "event": b["event"], "kind": "plugin",
                        "plugin_id": b["plugin_id"], "delivered": 0, "failed": 0, "timed_out": 0, "dropped": 0,
                        "skipped_inactive": 0, "queue_depth": 0, "last_ok_at": None, "last_event_id": None, "last_error_at": None,
                        "last_error": f"handler {b['handler']!r} is missing or not async; nothing is delivered"})
        return out


hub = EventHub()
