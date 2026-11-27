"""Vendor-neutral printer setup (BIZ-369): what a printer's connection config is missing, whether an edit changed it, and the
connection test that makes a printer `configured`. Reads only the plugin's `ConnectionField` metadata — never a vendor name."""
from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass
from typing import Callable

from .abstract_printer_client import ConnectionField
from .printer_client_factory import client_class, create_client_from_config
from .printer_identity import IdentityError, declared_model

logger = logging.getLogger(__name__)

TEST_CONNECT_POLL_S = 15.0  # MQTT/TLS handshake + first report can take well over 5s


def fields_for(printer) -> list[ConnectionField]:
    """The connection form of `printer`'s plugin, tailored to its model when the client does that (`connection_fields_for`);
    [] when no registered plugin serves it."""
    cls = client_class(printer.plugin_id or printer.printer_type)
    if cls is None:
        return []
    per_model = getattr(cls, "connection_fields_for", None)
    if callable(per_model) and printer.plugin_id and printer.manufacturer_id and printer.model_id:
        try:
            _, model = declared_model(printer.plugin_id, printer.manufacturer_id, printer.model_id)
        except IdentityError:
            model = None
        if model is not None:
            return per_model(model)
    return cls.connection_fields()


def strip_blank(cfg: dict | None) -> dict:
    """`cfg` without None / blank-string values: a field left empty is absent, never stored or sent as ""."""
    return {k: v for k, v in (cfg or {}).items() if v is not None and not (isinstance(v, str) and not v.strip())}


def missing_required(fields: list[ConnectionField], cfg: dict) -> list[str]:
    """Labels of the required fields `cfg` doesn't fill."""
    present = strip_blank(cfg)
    return [f.label for f in fields if f.required and f.name not in present]


def missing_reason(labels: list[str]) -> str:
    return "Missing " + ", ".join(labels)


def _normalised(cfg: dict | None) -> dict:
    return {k: str(v).strip() for k, v in strip_blank(cfg).items()}


def connection_changed(old: dict | None, new: dict | None, fields: list[ConnectionField] | None = None) -> bool:
    """True only for a real difference in how the printer connects: blanks and str/int spellings of the same value (`1` vs
    `"1"`) don't count, and with `fields` only the ones marked `connects` are compared (a per-print option is not a connection
    change), so re-saving a form or toggling an option never unconfigures a printer."""
    a, b = _normalised(old), _normalised(new)
    if fields is not None:
        keys = {f.name for f in fields if f.connects}
        a, b = {k: v for k, v in a.items() if k in keys}, {k: v for k, v in b.items() if k in keys}
    return a != b


@dataclass(frozen=True)
class TestOutcome:
    __test__ = False                  # not a pytest test class
    ok: bool
    error: str | None
    reachable: bool | None            # control port answered; None = nothing to probe / the attempt raised


def _probe(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except Exception:
        return False


async def failure_hint(client) -> tuple[str, bool | None]:
    """Why a connection failed, from a probe of the control port: unreachable (off/asleep/wrong IP) vs reached but the login
    didn't complete. Returns (hint, reachable)."""
    try:
        endpoint = client.control_endpoint()
    except Exception:
        endpoint = None
    if not endpoint:
        return "Could not connect.", None
    host, port = endpoint
    reachable = await asyncio.get_running_loop().run_in_executor(None, _probe, host, port)
    if reachable:
        return (f"Reached {host}:{port} but the login didn't complete — check the access code / "
                f"serial number, or the printer's single LAN connection is busy."), True
    return (f"Couldn't reach {host}:{port}. The printer may be off or asleep, the IP may be wrong, "
            f"or another app is holding the printer's LAN connection."), False


async def wait_connected(client, timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline and not client.connected:
        await asyncio.sleep(0.5)
    return bool(client.connected)


def default_name(model_display_name: str, ip: str) -> str:
    """A new printer's nickname when none is given: "<model> <last IP octet>" (e.g. "P1S 30")."""
    return f"{model_display_name} {str(ip).strip().rsplit('.', 1)[-1]}"


# --- the saved printer's test: what makes it `configured` ----------------------------------------------------------------

AUTO_TEST_CONCURRENCY = 4
_locks: dict[int, asyncio.Lock] = {}
_semaphore: asyncio.Semaphore | None = None
_tasks: set[asyncio.Task] = set()
_pending: dict[int, int] = {}        # printer id -> tests scheduled or running for it


def _reset() -> None:
    """Forget per-loop primitives (tests run each case on a fresh event loop)."""
    global _semaphore
    _locks.clear()
    _semaphore = None
    _tasks.clear()
    _pending.clear()


def is_pending(printer_id: int) -> bool:
    """A connection test is scheduled or running for this printer (its live client must not vouch for the setup meanwhile)."""
    return _pending.get(printer_id, 0) > 0


def _begin(printer_id: int) -> None:
    _pending[printer_id] = _pending.get(printer_id, 0) + 1


def _end(printer_id: int) -> None:
    left = _pending.get(printer_id, 0) - 1
    if left > 0:
        _pending[printer_id] = left
    else:
        _pending.pop(printer_id, None)


async def _persist(session_factory, printer_id: int, configured: bool, error: str | None) -> None:
    from ..models import Printer
    async with session_factory() as session:
        printer = await session.get(Printer, printer_id)
        if printer is not None:
            printer.configured, printer.config_error = configured, error
            await session.commit()


async def write_if_current(session_factory, printer_id: int, still_current, configured: bool, error: str | None) -> bool:
    """Record a test result only if `still_current(printer)` holds for the committed row. A no-op UPDATE takes SQLite's write lock
    first, so no connection edit can commit between the check and the write."""
    from sqlalchemy import update
    from ..models import Printer
    async with session_factory() as session:
        await session.execute(update(Printer).where(Printer.id == printer_id).values(id=Printer.id)
                              .execution_options(synchronize_session=False))
        printer = await session.get(Printer, printer_id, populate_existing=True)
        if printer is None or not still_current(printer):
            await session.rollback()
            return False
        printer.configured, printer.config_error = configured, error
        from .printer_manager import printer_manager
        was = printer_manager.is_configured(printer_id)
        printer_manager.set_configured(printer_id, configured)   # before the lock is released: an edit's own reset always lands last
        try:
            await session.commit()
        except BaseException:
            printer_manager.set_configured(printer_id, was)
            raise
        return True


def _tested_with(tested: dict, fields) -> Callable:
    """The row still connects the way the test did: a result for since-edited credentials must not mark the printer."""
    return lambda printer: not connection_changed(tested, printer.connection_config, fields)


async def _report(printer_id: int, outcome: TestOutcome) -> None:
    from .printer_manager import printer_manager
    if printer_manager._on_state_broadcast:
        await printer_manager._on_state_broadcast(
            "printer_configured", {"printer_id": printer_id, "configured": outcome.ok, "config_error": outcome.error})


async def evaluate(printer_id: int, session_factory) -> TestOutcome:
    """Test a saved printer with its stored config and record the result. Missing a required field: unconfigured with the reason,
    no client built, nothing contacted. Otherwise the printer's own managed client connects (one LAN connection, which some printers
    require) and success marks it configured. On failure: unreachable keeps the client registered so its reconnect can still produce
    the first live connection; reached-but-login-failed tears it down (no retry storm against the printer) until the fields change or
    the user tests again. Never raises; one test per printer at a time."""
    _begin(printer_id)
    try:
        return await _evaluate_locked(printer_id, session_factory)
    finally:
        _end(printer_id)


async def _evaluate_locked(printer_id: int, session_factory) -> TestOutcome:
    lock = _locks.setdefault(printer_id, asyncio.Lock())
    async with lock:
        tested: dict = {}
        try:
            outcome = await _evaluate(printer_id, session_factory, tested)
        except Exception as exc:
            outcome = TestOutcome(False, str(exc) or type(exc).__name__, None)
            try:
                if "current" in tested:            # GH #250: only for the settings it tested, never an edit made meanwhile
                    if not await write_if_current(session_factory, printer_id, tested["current"], False, outcome.error):
                        outcome = _superseded()
                else:                              # it failed before reading the settings: nothing was tested yet
                    await _persist(session_factory, printer_id, False, outcome.error)
            except Exception:
                logger.exception("Recording the connection test of printer %s failed", printer_id)
        try:
            await _report(printer_id, outcome)
        except Exception:
            logger.exception("Broadcasting the connection test of printer %s failed", printer_id)
        return outcome


async def _evaluate(printer_id: int, session_factory, tested: dict | None = None) -> TestOutcome:
    from ..models import Printer
    from .printer_client_factory import create_client
    from .printer_manager import printer_manager
    async with session_factory() as session:
        printer = await session.get(Printer, printer_id)
        if printer is None:
            return TestOutcome(False, "Printer not found", None)
        from . import printer_ownership
        owner = await printer_ownership.conflict(session, printer.connection_config, printer.id)
        if owner is not None:
            reason = printer_ownership.reason(owner)
            printer.configured, printer.config_error = False, reason
            await session.commit()
            printer_manager.set_configured(printer_id, False)
            printer_manager.disconnect_printer(printer_id)
            return TestOutcome(False, reason, None)
        missing = missing_required(fields_for(printer), printer.connection_config or {})
        if missing:
            reason = missing_reason(missing)
            printer.configured, printer.config_error = False, reason
            await session.commit()
            printer_manager.set_configured(printer_id, False)
            printer_manager.disconnect_printer(printer_id)
            return TestOutcome(False, reason, None)
        if not printer.enabled:                       # a disabled printer stays unconnected: test it with a throwaway client
            current = _tested_with(dict(printer.connection_config or {}), fields_for(printer))
            if tested is not None:
                tested["current"] = current
            plugin, cfg = printer.plugin_id or printer.printer_type, dict(printer.connection_config or {})
            await session.rollback()                  # nothing pending; no transaction held across the test
            outcome = await test_unsaved(plugin, cfg)
            # GH #260: recorded only for the settings it tested (an edit during the test retests on its own)
            if not await write_if_current(session_factory, printer_id, current, outcome.ok, outcome.error):
                return _superseded()
            return outcome
        printer_manager.disconnect_printer(printer_id)
        printer_manager.set_printer_plugin(printer_id, printer.plugin_id)
        client = create_client(printer)
        current = _tested_with(dict(printer.connection_config or {}), fields_for(printer))
        if tested is not None:
            tested["current"] = current
    printer_manager.connect_printer(printer_id, client)
    connected = await wait_connected(client, TEST_CONNECT_POLL_S)
    if not connected:
        hint, reachable = await failure_hint(client)
        connected = client.connected                        # it came up while the port was probed
    if connected:
        if await printer_manager.mark_configured(printer_id, session_factory, broadcast=False, still_current=current):
            return TestOutcome(True, None, True)
        return _superseded()
    if not await write_if_current(session_factory, printer_id, current, False, hint):
        return _superseded()                                # the edit's own retest owns the client and the state now
    if reachable is True and printer_manager._clients.get(printer_id) is client:
        printer_manager.disconnect_printer(printer_id)      # login failed: don't let its reconnect loop hammer the printer
    return TestOutcome(False, hint, reachable)


def _superseded() -> TestOutcome:
    return TestOutcome(False, "The connection settings changed during the test; the new settings are tested separately", None)


def schedule(printer_id: int, session_factory) -> None:
    """Run `evaluate` in the background (at most AUTO_TEST_CONCURRENCY at once), off the request path."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(AUTO_TEST_CONCURRENCY)
    sem = _semaphore
    _begin(printer_id)                       # pending from now, not only once a slot frees up

    async def run() -> None:
        try:
            async with sem:
                await evaluate(printer_id, session_factory)
        finally:
            _end(printer_id)

    task = asyncio.get_running_loop().create_task(run(), name=f"printer-test-{printer_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def drain() -> None:
    """Wait for every scheduled test to finish (shutdown, tests)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def test_unsaved(printer_type: str, cfg: dict) -> TestOutcome:
    """Connect a throwaway client with `cfg` (blanks stripped) and always disconnect it."""
    client = None
    try:
        client = create_client_from_config(printer_type, strip_blank(cfg))
        client.connect()
        if await wait_connected(client, TEST_CONNECT_POLL_S):
            return TestOutcome(True, None, True)
        hint, reachable = await failure_hint(client)
        return TestOutcome(False, hint, reachable)
    except Exception as exc:
        return TestOutcome(False, str(exc), None)
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass
