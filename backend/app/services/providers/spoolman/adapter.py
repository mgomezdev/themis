"""Spoolman adapter for FilamentInventoryProvider. Owns URL, API key, error mapping and the
`extra.orca_profiles` double-JSON encoding."""
from __future__ import annotations

import json

import httpx

from ... import spoolman_service
from ..filament_inventory import (
    Filament,
    FilamentInventoryProvider,
    InventoryProviderError,
    Spool,
)


def _map_error(e: Exception) -> InventoryProviderError:
    if isinstance(e, InventoryProviderError):
        return e
    if isinstance(e, httpx.HTTPStatusError):
        return InventoryProviderError(str(e), code=str(e.response.status_code), status=e.response.status_code)
    return InventoryProviderError(str(e), code=type(e).__name__)


def decode_bindings(raw_extra) -> dict[str, list[str]]:
    """`extra.orca_profiles` is a JSON string of a JSON string; anything malformed reads as no bindings."""
    if not raw_extra:
        return {}
    try:
        data = json.loads(json.loads(raw_extra))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: list(v) for k, v in data.items() if isinstance(v, list)}


def _filament(raw: dict) -> Filament:
    vendor = raw.get("vendor") or {}
    return Filament(
        ref=str(raw["id"]),
        name=raw.get("name") or "",
        vendor=vendor.get("name") if isinstance(vendor, dict) else None,
        material=raw.get("material"),
        color_hex=raw.get("color_hex"),
        profile_bindings=decode_bindings((raw.get("extra") or {}).get("orca_profiles")),
        raw=raw,
    )


def _spool(raw: dict) -> Spool:
    fil = raw.get("filament") or {}
    return Spool(
        ref=str(raw["id"]),
        filament_ref=str(fil["id"]) if fil.get("id") is not None else None,
        filament_name=fil.get("name") or "",
        filament_vendor=(fil.get("vendor") or {}).get("name") if isinstance(fil.get("vendor"), dict) else None,
        filament_material=fil.get("material"),
        location=raw.get("location"),
        remaining_weight=raw.get("remaining_weight"),
        archived=bool(raw.get("archived")),
        raw=raw,
    )


class SpoolmanInventoryProvider(FilamentInventoryProvider):
    TRACKS_WEIGHT = True
    RECORDS_USAGE = True
    PROFILE_BINDINGS = True

    def __init__(self, url: str, api_key: str | None = None) -> None:
        self._url = url
        self._api_key = api_key

    async def test_connection(self) -> dict:
        try:
            return await spoolman_service.test_connection(self._url, self._api_key)
        except Exception as e:
            raise _map_error(e) from e

    async def list_filaments(self) -> list[Filament]:
        try:
            return [_filament(f) for f in await spoolman_service.fetch_filaments(self._url, self._api_key)]
        except Exception as e:
            raise _map_error(e) from e

    async def get_filament(self, ref: str) -> Filament:
        try:
            return _filament(await spoolman_service.fetch_filament(self._url, self._api_key, int(ref)))
        except Exception as e:
            raise _map_error(e) from e

    async def list_spools(self) -> list[Spool]:
        try:
            return [_spool(s) for s in await spoolman_service.fetch_spools(self._url, self._api_key)]
        except Exception as e:
            raise _map_error(e) from e

    async def record_usage(self, spool_ref: str, grams: float) -> None:
        try:
            await spoolman_service.record_spool_use(self._url, self._api_key, int(spool_ref), grams)
        except Exception as e:
            raise _map_error(e) from e

    async def get_profile_bindings(self, filament_ref: str) -> dict[str, list[str]]:
        return (await self.get_filament(filament_ref)).profile_bindings

    async def set_profile_bindings(self, filament_ref: str, bindings: dict[str, list[str]]) -> Filament:
        try:
            raw = await spoolman_service.patch_filament(self._url, self._api_key, int(filament_ref), bindings)
        except Exception as e:
            raise _map_error(e) from e
        return _filament(raw)
