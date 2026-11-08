"""Vendor-neutral printer setup (BIZ-369): what a printer's connection config is missing, whether an edit changed it, and the
connection test that makes a printer `configured`. Reads only the plugin's `ConnectionField` metadata — never a vendor name."""
from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass

from .abstract_printer_client import ConnectionField
from .printer_client_factory import client_class, create_client_from_config
from .printer_identity import IdentityError, declared_model

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


def connection_changed(old: dict | None, new: dict | None) -> bool:
    """True only for a real difference: blanks and str/int spellings of the same value (`1` vs `"1"`) don't count, so
    re-saving a form with untouched values never unconfigures a printer."""
    return _normalised(old) != _normalised(new)


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
            f"or another app is holding the printer's single LAN connection (Bambu allows only one)."), False


async def wait_connected(client, timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline and not client.connected:
        await asyncio.sleep(0.5)
    return bool(client.connected)


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
