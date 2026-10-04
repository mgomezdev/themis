"""In-memory provider fakes for tests that need to swap a provider without touching HTTP."""
from __future__ import annotations

from app.services.providers.filament_inventory import (
    Filament,
    FilamentInventoryProvider,
    InventoryProviderError,
    Spool,
)


class FakeInventoryProvider(FilamentInventoryProvider):
    TRACKS_WEIGHT = True
    RECORDS_USAGE = True
    PROFILE_BINDINGS = True

    def __init__(self, filaments: list[Filament] | None = None, spools: list[Spool] | None = None) -> None:
        self.filaments = {f.ref: f for f in (filaments or [])}
        self.spools = {s.ref: s for s in (spools or [])}
        self.usage: list[tuple[str, float]] = []
        self.calls: list[str] = []
        self.fail_with: InventoryProviderError | None = None

    def _enter(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_with is not None:
            raise self.fail_with

    async def test_connection(self) -> dict:
        self._enter("test_connection")
        return {"version": "fake"}

    async def list_filaments(self) -> list[Filament]:
        self._enter("list_filaments")
        return list(self.filaments.values())

    async def get_filament(self, ref: str) -> Filament:
        self._enter("get_filament")
        try:
            return self.filaments[ref]
        except KeyError:
            raise InventoryProviderError(f"Filament {ref} not found", code="404", status=404)

    async def list_spools(self) -> list[Spool]:
        self._enter("list_spools")
        return list(self.spools.values())

    async def record_usage(self, spool_ref: str, grams: float) -> None:
        self._enter("record_usage")
        if spool_ref not in self.spools:
            raise InventoryProviderError(f"Spool {spool_ref} not found", code="404", status=404)
        self.usage.append((spool_ref, grams))
        spool = self.spools[spool_ref]
        if spool.remaining_weight is not None:
            spool.remaining_weight = max(0.0, spool.remaining_weight - grams)

    async def get_profile_bindings(self, filament_ref: str) -> dict[str, list[str]]:
        return (await self.get_filament(filament_ref)).profile_bindings

    async def set_profile_bindings(self, filament_ref: str, bindings: dict[str, list[str]]) -> Filament:
        fil = await self.get_filament(filament_ref)
        fil.profile_bindings = {k: list(v) for k, v in bindings.items()}
        return fil
