from __future__ import annotations
import asyncio
import logging
from dataclasses import asdict
from typing import Any, Callable

from .abstract_printer_client import AbstractPrinterClient

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
    }


def _serialize_elegoo(state, printer_id: int) -> dict:
    return {
        "printer_type": "elegoo_centauri",
        "id": printer_id,
        "connected": state.connected,
        "state": getattr(state, "state", "unknown"),
        "current_print": getattr(state, "current_print", None),
        "progress": getattr(state, "progress", 0.0),
        "remaining_time": getattr(state, "remaining_time", 0),
        "layer_num": getattr(state, "layer_num", 0),
        "total_layers": getattr(state, "total_layers", 0),
        "temperatures": getattr(state, "temperatures", {}),
    }


_STATUS_SERIALIZERS: dict[str, Callable] = {
    "bambu": _serialize_bambu,
    "elegoo_centauri": _serialize_elegoo,
}


class PrinterManager:
    def __init__(self) -> None:
        self._clients: dict[int, AbstractPrinterClient] = {}
        self._awaiting_plate_clear: set[int] = set()
        self._on_state_broadcast: Callable | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_broadcast_callback(self, cb: Callable) -> None:
        self._on_state_broadcast = cb

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

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
        if self._on_state_broadcast:
            normalized = self.get_normalized_state(printer_id)
            await self._on_state_broadcast("plate_clear_required", {"printer_id": printer_id})
            await self._on_state_broadcast("printer_state", normalized)

    def connect_printer(self, printer_id: int, client: AbstractPrinterClient) -> None:
        self.register_client(printer_id, client)
        loop = self._loop

        async def _on_state(state):
            await self.on_state_change(printer_id, state)

        async def _on_complete(state):
            await self.on_print_complete(printer_id, state)

        client._on_state_change = lambda s: (
            asyncio.run_coroutine_threadsafe(_on_state(s), loop) if loop else None
        )
        client._on_print_complete = lambda s: (
            asyncio.run_coroutine_threadsafe(_on_complete(s), loop) if loop else None
        )
        client.connect(loop=loop)

    def disconnect_printer(self, printer_id: int) -> None:
        client = self._clients.pop(printer_id, None)
        if client:
            client.disconnect()


printer_manager = PrinterManager()
