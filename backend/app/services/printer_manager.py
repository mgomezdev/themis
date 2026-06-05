from __future__ import annotations
import asyncio
import logging
from dataclasses import asdict
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .abstract_printer_client import AbstractPrinterClient
from .printer_client_factory import create_client

logger = logging.getLogger(__name__)


def _serialize_bambu(state, printer_id: int) -> dict:
    return {
        "printer_type": "bambu",
        "id": printer_id,
        "connected": state.connected,
        "state": getattr(state, "state", "unknown"),
        "current_print": getattr(state, "current_print", None),
        "progress": getattr(state, "progress", 0.0),
        "remaining_time": getattr(state, "remaining_time", 0),
        "layer_num": getattr(state, "layer_num", 0),
        "total_layers": getattr(state, "total_layers", 0),
        "temperatures": getattr(state, "temperatures", {}),
        "fan_model": getattr(state, "fan_model", 0),
        "fan_aux": getattr(state, "fan_aux", 0),
        "fan_box": getattr(state, "fan_box", 0),
        "speed_factor": 1.0,
        "klippy_state": "ready" if state.connected else "disconnected",
        "cover_url": None,
    }


def _serialize_elegoo(state, printer_id: int) -> dict:
    total_ticks = getattr(state, "total_ticks", 0)
    current_ticks = getattr(state, "current_ticks", 0)
    if total_ticks > 0 and current_ticks < total_ticks:
        remaining_time = int((total_ticks - current_ticks) / 60)
    else:
        remaining_time = getattr(state, "remaining_time", 0) or 0
    return {
        "printer_type": "elegoo_centauri",
        "id": printer_id,
        "connected": state.connected,
        "state": getattr(state, "state", "unknown"),
        "current_print": getattr(state, "filename", None) or getattr(state, "current_print", None),
        "progress": getattr(state, "progress", 0.0),
        "remaining_time": remaining_time,
        "layer_num": getattr(state, "layer_num", None),
        "total_layers": getattr(state, "total_layers", None),
        "temperatures": getattr(state, "temperatures", {}),
        "fan_model": getattr(state, "fan_model", 0),
        "fan_aux": getattr(state, "fan_aux", 0),
        "fan_box": getattr(state, "fan_box", 0),
        "speed_factor": getattr(state, "print_speed_pct", 100) / 100.0,
        "klippy_state": "ready" if state.connected else "disconnected",
        "cover_url": None,
    }


def _serialize_snapmaker(state, printer_id: int) -> dict:
    conn = bool(getattr(state, "connected", False) and getattr(state, "klippy_ready", True))
    return {
        "printer_type": "snapmaker_extended",
        "id": printer_id,
        "connected": conn,
        "state": getattr(state, "state", "unknown"),
        "current_print": getattr(state, "current_print", None),
        "progress": getattr(state, "progress", 0.0),
        "remaining_time": 0,
        "layer_num": getattr(state, "layer_num", 0),
        "total_layers": getattr(state, "total_layers", 0),
        "temperatures": getattr(state, "temperatures", {}),
        "fan_model": 0,
        "fan_aux": 0,
        "fan_box": 0,
        "speed_factor": 1.0,
        "klippy_state": "ready" if conn else "disconnected",
        "cover_url": None,
    }


_STATUS_SERIALIZERS: dict[str, Callable] = {
    "bambu": _serialize_bambu,
    "elegoo_centauri": _serialize_elegoo,
    "snapmaker_extended": _serialize_snapmaker,
}


class PrinterManager:
    def __init__(self) -> None:
        self._clients: dict[int, AbstractPrinterClient] = {}
        self._awaiting_plate_clear: set[int] = set()
        self._on_state_broadcast: Callable | None = None
        self._on_job_complete: Callable | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session_factory: async_sessionmaker | None = None

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

    def is_printer_ready(self, printer_id: int) -> bool:
        client = self._clients.get(printer_id)
        if client is None:
            return False
        return client.is_idle and printer_id not in self._awaiting_plate_clear

    def set_awaiting_plate_clear(self, printer_id: int, awaiting: bool) -> None:
        if awaiting:
            self._awaiting_plate_clear.add(printer_id)
        else:
            self._awaiting_plate_clear.discard(printer_id)

    def load_awaiting_plate_clear(self, printer_ids: set[int]) -> None:
        self._awaiting_plate_clear = printer_ids.copy()

    def get_normalized_state(self, printer_id: int) -> dict:
        client = self._clients[printer_id]
        serializer = _STATUS_SERIALIZERS.get(client.printer_type)
        if serializer is None:
            return {"id": printer_id, "printer_type": client.printer_type, "connected": client.connected}
        state = serializer(client.state, printer_id)
        # Override connected with the client-level property (authoritative source)
        state["connected"] = client.connected
        state["capabilities"] = asdict(client.get_capabilities())
        state["awaiting_plate_clear"] = self.is_awaiting_plate_clear(printer_id)
        return state

    async def on_state_change(self, printer_id: int, vendor_state) -> None:
        if self._on_state_broadcast:
            normalized = self.get_normalized_state(printer_id)
            await self._on_state_broadcast("printer_state", normalized)

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
        `spoolman_spool_id`) are preserved by slot across AMS reports; slots no
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
                            tray = {
                                **tray,
                                "filament_profile": prev.get("filament_profile"),
                                "spoolman_spool_id": prev.get("spoolman_spool_id"),
                            }
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
            await self.on_state_change(printer_id, state)

        async def _on_complete(state):
            await self.on_print_complete(printer_id, state)

        async def _on_ams(trays):
            await self.on_ams_change(printer_id, trays)

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


printer_manager = PrinterManager()
