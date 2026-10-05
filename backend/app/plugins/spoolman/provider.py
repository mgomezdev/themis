"""`filament_inventory` provider backed by a Spoolman instance. Owns URL, API key, error mapping, the label format and
the `extra.orca_profiles` double-JSON encoding."""
from __future__ import annotations

import json

import httpx

from ..kinds.filament_inventory import (
    LABEL_SCAN, PROFILE_LINKS_READ, PROFILE_LINKS_WRITE, REMOTE, TRACKS_WEIGHT, WRITE_WEIGHT,
    FilamentInventoryProvider, InvMaterial, InvSpool, InventoryProviderError,
)
from . import client, labels
from .settings import SpoolmanSettings


def _map_error(e: Exception) -> InventoryProviderError:
    if isinstance(e, InventoryProviderError):
        return e
    if isinstance(e, httpx.HTTPStatusError):
        return InventoryProviderError(str(e), code=str(e.response.status_code), status=e.response.status_code)
    return InventoryProviderError(str(e), code=type(e).__name__)


def decode_links(raw_extra) -> dict[str, list[str]]:
    """`extra.orca_profiles` is a JSON string of a JSON string; anything malformed reads as no links."""
    if not raw_extra:
        return {}
    try:
        data = json.loads(json.loads(raw_extra))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: list(v) for k, v in data.items() if isinstance(v, list)}


def _hex(value) -> str | None:
    return f"#{str(value).lstrip('#').upper()}" if value else None


def to_material(raw: dict) -> InvMaterial:
    vendor = raw.get("vendor") or {}
    return InvMaterial(
        ref=str(raw["id"]), name=raw.get("name") or "",
        material=raw.get("material"), color_hex=_hex(raw.get("color_hex")),
        vendor=vendor.get("name") if isinstance(vendor, dict) else None,
        density=raw.get("density"), diameter=raw.get("diameter"),
        profile_links=decode_links((raw.get("extra") or {}).get("orca_profiles")), raw=raw,
    )


def to_spool(raw: dict) -> InvSpool:
    fil = raw.get("filament") or {}
    material = to_material(fil) if fil.get("id") is not None else None
    ref = str(raw["id"])
    label = " ".join(p for p in (material.vendor if material else None, material.name if material else None) if p)
    return InvSpool(
        ref=ref, material_ref=material.ref if material else None, material=material,
        remaining_g=raw.get("remaining_weight"), location=raw.get("location"),
        initial_g=raw.get("initial_weight") or fil.get("weight"),
        label=label or f"spool {ref}", archived=bool(raw.get("archived")), raw=raw,
    )


class SpoolmanProvider(FilamentInventoryProvider):
    capabilities = frozenset({TRACKS_WEIGHT, WRITE_WEIGHT, PROFILE_LINKS_READ, PROFILE_LINKS_WRITE, LABEL_SCAN, REMOTE})

    def __init__(self, settings: SpoolmanSettings) -> None:
        if not settings.url:
            raise ValueError("Spoolman URL is not set")
        self._url, self._api_key = settings.url, settings.api_key

    async def test_connection(self) -> dict:
        try:
            return await client.test_connection(self._url, self._api_key)
        except Exception as e:
            raise _map_error(e) from e

    async def list_materials(self) -> list[InvMaterial]:
        try:
            return [to_material(f) for f in await client.fetch_filaments(self._url, self._api_key)]
        except Exception as e:
            raise _map_error(e) from e

    async def list_spools(self) -> list[InvSpool]:
        try:
            return [to_spool(s) for s in await client.fetch_spools(self._url, self._api_key)]
        except Exception as e:
            raise _map_error(e) from e

    async def get_spool(self, spool_ref: str) -> InvSpool | None:
        if not spool_ref.isdigit():
            return None
        try:
            return to_spool(await client.fetch_spool(self._url, self._api_key, int(spool_ref)))
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise _map_error(e) from e
        except Exception as e:
            raise _map_error(e) from e

    async def set_remaining(self, spool_ref: str, remaining_g: float) -> None:
        try:
            await client.patch_spool_remaining(self._url, self._api_key, int(spool_ref), remaining_g)
        except Exception as e:
            raise _map_error(e) from e

    async def set_profile_links(self, material_ref: str, links: dict[str, list[str]]) -> InvMaterial:
        try:
            return to_material(await client.patch_filament(self._url, self._api_key, int(material_ref), links))
        except Exception as e:
            raise _map_error(e) from e

    def parse_label(self, text: str) -> str | None:
        return labels.parse_label(text)

    def spool_url(self, ref: str) -> str | None:
        return f"{self._url.rstrip('/')}/spool/show/{ref}"
