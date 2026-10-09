from __future__ import annotations
import asyncio
import logging
from dataclasses import asdict
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .abstract_printer_client import AbstractPrinterClient
from .events import event_bus
from .inventory import refs as inventory_refs
from .printer_client_factory import create_client
from .printer_events import AmsChanged, PrinterStateChanged, PrintCompleted
from .printer_identity import dormant_reason

logger = logging.getLogger(__name__)


class PrinterManager:
    def __init__(self) -> None:
        self._clients: dict[int, AbstractPrinterClient] = {}
        self._awaiting_plate_clear: set[int] = set()
        self._on_state_broadcast: Callable | None = None
        self._on_job_complete: Callable | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session_factory: async_sessionmaker | None = None
        self._subscribed = False
        self._printer_plugin: dict[int, str | None] = {}

    def subscribe_events(self) -> None:
        """Route the bus's printer events to the handlers that act on them. Idempotent: the bus is process-wide and the app
        lifespan runs once per client in tests."""
        if self._subscribed:
            return
        self._subscribed = True
        event_bus.subscribe(PrinterStateChanged, lambda e: self.on_state_change(e.printer_id, e.state))
        event_bus.subscribe(PrintCompleted, lambda e: self.on_print_complete(e.printer_id, e.state))
        event_bus.subscribe(AmsChanged, lambda e: self.on_ams_change(e.printer_id, e.trays))

    def set_broadcast_callback(self, cb: Callable) -> None:
        self._on_state_broadcast = cb

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def set_session_factory(self, factory: async_sessionmaker) -> None:
        self._session_factory = factory

    def set_job_complete_callback(self, cb: Callable) -> None:
        self._on_job_complete = cb

    async def load_awaiting_plate_clear_from_db(self) -> None:
        if not self._session_factory:
            return
        async with self._session_factory() as session:
            from ..models import Printer
            result = await session.execute(
                select(Printer.id).where(Printer.awaiting_plate_clear == True)  # noqa: E712
            )
            ids = {row[0] for row in result.all()}
            self.load_awaiting_plate_clear(ids)

    async def connect_all_enabled_printers(self, session_factory) -> None:
        if not session_factory:
            return
        async with session_factory() as session:
            from ..models import Printer
            result = await session.execute(
                select(Printer).where(Printer.enabled == True)  # noqa: E712
            )
            printers = result.scalars().all()
        for printer in printers:
            self.set_printer_plugin(printer.id, printer.plugin_id)
            try:
                client = create_client(printer)
                self.connect_printer(printer.id, client)
            except Exception:
                logger.exception("Failed to connect printer %s (id=%s)", printer.name, printer.id)

    def register_client(self, printer_id: int, client: AbstractPrinterClient) -> None:
        self._clients[printer_id] = client

    def get_client(self, printer_id: int) -> AbstractPrinterClient:
        return self._clients[printer_id]

    def get_all_printer_ids(self) -> list[int]:
        return list(self._clients.keys())

    def is_awaiting_plate_clear(self, printer_id: int) -> bool:
        return printer_id in self._awaiting_plate_clear

    def set_printer_plugin(self, printer_id: int, plugin_id: str | None) -> None:
        """Remember which plugin serves a printer, so a disabled or removed plugin makes its printers dormant (not ready)."""
        self._printer_plugin[printer_id] = plugin_id

    def is_printer_ready(self, printer_id: int) -> bool:
        client = self._clients.get(printer_id)
        if client is None:
            return False
        if dormant_reason(self._printer_plugin.get(printer_id)) is not None:
            return False
        return client.connected and client.is_idle and printer_id not in self._awaiting_plate_clear

    def set_awaiting_plate_clear(self, printer_id: int, awaiting: bool) -> None:
        if awaiting:
            self._awaiting_plate_clear.add(printer_id)
        else:
            self._awaiting_plate_clear.discard(printer_id)

    def load_awaiting_plate_clear(self, printer_ids: set[int]) -> None:
        self._awaiting_plate_clear = printer_ids.copy()

    def get_normalized_state(self, printer_id: int) -> dict:
        client = self._clients[printer_id]
        state = client.serialize_state(printer_id)
        # Override connected with the client-level property (authoritative source)
        state["connected"] = client.connected
        state["capabilities"] = asdict(client.get_capabilities())
        state["awaiting_plate_clear"] = self.is_awaiting_plate_clear(printer_id)
        return state

    async def on_state_change(self, printer_id: int, vendor_state) -> None:
        await self._observe_alarms(printer_id)
        if self._on_state_broadcast:
            normalized = self.get_normalized_state(printer_id)
            await self._on_state_broadcast("printer_state", normalized)

    async def _observe_alarms(self, printer_id: int) -> None:
        """Feed the printer's current problems to the alarm history. Never lets an alarm failure break telemetry."""
        client = self._clients.get(printer_id)
        if client is None or self._session_factory is None:
            return
        try:
            from .alarms import tracker
            await tracker.observe(
                self._session_factory, printer_id, client.get_alarms(), self._on_state_broadcast,
                refresh=lambda: self._clients[printer_id].get_alarms() if printer_id in self._clients else [])
        except Exception:
            logger.exception("Alarm update failed for printer %s", printer_id)

    async def on_print_complete(self, printer_id: int, vendor_state) -> None:
        self.set_awaiting_plate_clear(printer_id, True)
        if self._session_factory:
            async with self._session_factory() as session:
                from ..models import Printer
                printer = await session.get(Printer, printer_id)
                if printer:
                    printer.awaiting_plate_clear = True
                    await session.commit()
        if self._on_job_complete:
            await self._on_job_complete(printer_id)
        if self._on_state_broadcast:
            normalized = self.get_normalized_state(printer_id)
            await self._on_state_broadcast("plate_clear_required", {"printer_id": printer_id})
            await self._on_state_broadcast("printer_state", normalized)

    async def on_ams_change(self, printer_id: int, trays: list) -> None:
        """AMS filament change → persist the printer's `loaded_filaments` from the
        auto-detected trays. User-set per-slot mappings (`filament_profile`,
        `inventory`, legacy binding key) are preserved by slot across AMS reports; slots no
        longer reported drop with their mappings."""
        if self._session_factory:
            async with self._session_factory() as session:
                from ..models import Printer
                printer = await session.get(Printer, printer_id)
                if printer is not None:
                    prev_by_slot = {
                        f.get("slot"): f for f in (printer.loaded_filaments or [])
                    }
                    merged = []
                    for tray in trays:
                        prev = prev_by_slot.get(tray.get("slot"))
                        if prev is not None:
                            # Keep EVERY Themis-owned key (profile, spool binding, inventory ref), not just two named ones:
                            # a vendor AMS report only carries hardware facts and must never wipe a link.
                            tray = inventory_refs.preserve_slot_keys(prev, tray)
                        merged.append(tray)
                    printer.loaded_filaments = merged
                    await session.commit()
        if self._on_state_broadcast:
            try:
                await self._on_state_broadcast("printer_state", self.get_normalized_state(printer_id))
            except Exception:
                logger.exception("Failed to broadcast after AMS change for printer %s", printer_id)

    def connect_printer(self, printer_id: int, client: AbstractPrinterClient) -> None:
        self.register_client(printer_id, client)
        loop = self._loop

        if loop is None:
            logger.warning("connect_printer called before set_loop — callbacks will be disabled")

        async def _on_state(state):
            await event_bus.publish(PrinterStateChanged(printer_id=printer_id, state=state))

        async def _on_complete(state):
            await event_bus.publish(PrintCompleted(printer_id=printer_id, state=state))

        async def _on_ams(trays):
            await event_bus.publish(AmsChanged(printer_id=printer_id, trays=trays))

        # Assign async functions directly — clients call run_coroutine_threadsafe on them
        client._on_state_change = _on_state
        client._on_print_complete = _on_complete
        if hasattr(client, "_on_ams_change"):
            client._on_ams_change = _on_ams
        client.connect(loop=loop)

    def disconnect_printer(self, printer_id: int) -> None:
        client = self._clients.pop(printer_id, None)
        if client:
            client.disconnect()
        from .camera_hub import hub as _camera_hub
        _camera_hub.forget(printer_id)               # end its shared camera stream and drop cached frames
        from .alarms import tracker
        tracker.forget(printer_id)


printer_manager = PrinterManager()
