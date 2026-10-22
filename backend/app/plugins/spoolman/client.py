"""Spoolman REST client (httpx). Owned by the Spoolman plugin; nothing outside `app/plugins/spoolman/` may import it."""
from __future__ import annotations

import json as _json
from typing import Optional

import httpx


def _headers(api_key: Optional[str]) -> dict:
    return {"X-API-Key": api_key} if api_key else {}


async def _get(url: str, api_key: Optional[str], path: str, timeout: float):
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{url.rstrip('/')}{path}", headers=_headers(api_key))
        resp.raise_for_status()
        return resp.json()


async def test_connection(url: str, api_key: Optional[str] = None) -> dict:
    return await _get(url, api_key, "/api/v1/info", 5)


async def fetch_filaments(url: str, api_key: Optional[str] = None) -> list[dict]:
    return await _get(url, api_key, "/api/v1/filament", 10)


async def fetch_filament(url: str, api_key: Optional[str] = None, filament_id: int = 0) -> dict:
    return await _get(url, api_key, f"/api/v1/filament/{filament_id}", 10)


async def fetch_spools(url: str, api_key: Optional[str] = None) -> list[dict]:
    return await _get(url, api_key, "/api/v1/spool", 10)


async def fetch_spool(url: str, api_key: Optional[str], spool_id: int) -> dict:
    return await _get(url, api_key, f"/api/v1/spool/{spool_id}", 10)


async def _patch(url: str, api_key: Optional[str], path: str, body: dict) -> dict:
    base = url.rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(f"{base}{path}", json=body, headers=_headers(api_key))
        if not resp.is_success:
            raise httpx.HTTPStatusError(f"{resp.status_code}: {resp.text}", request=httpx.Request("PATCH", f"{base}{path}"),
                                        response=resp)
        return resp.json()


async def patch_filament(url: str, api_key: Optional[str], filament_id: int, orca_profiles: dict) -> dict:
    """Writes the double-JSON-encoded `extra.orca_profiles` (how Spoolman stores the Orca preset links)."""
    return await _patch(url, api_key, f"/api/v1/filament/{filament_id}",
                        {"extra": {"orca_profiles": _json.dumps(_json.dumps(orca_profiles))}})


async def patch_spool_remaining(url: str, api_key: Optional[str], spool_id: int, remaining_g: float) -> dict:
    """`PATCH /spool/{id}` with an absolute remaining weight (verified by protocol_verification/test_spoolman_weight.py)."""
    return await _patch(url, api_key, f"/api/v1/spool/{spool_id}", {"remaining_weight": remaining_g})
