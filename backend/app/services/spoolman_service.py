from __future__ import annotations
from typing import Optional
import httpx
import json as _json


async def test_connection(url: str, api_key: Optional[str] = None) -> dict:
    headers = _headers(api_key)
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(f"{url.rstrip('/')}/api/v1/info", headers=headers)
        resp.raise_for_status()
        return resp.json()


async def fetch_filaments(url: str, api_key: Optional[str] = None) -> list[dict]:
    headers = _headers(api_key)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{url.rstrip('/')}/api/v1/filament", headers=headers)
        resp.raise_for_status()
        return resp.json()


async def fetch_spools(url: str, api_key: Optional[str] = None) -> list[dict]:
    headers = _headers(api_key)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{url.rstrip('/')}/api/v1/spool", headers=headers)
        resp.raise_for_status()
        return resp.json()


def _headers(api_key: Optional[str]) -> dict:
    return {"X-API-Key": api_key} if api_key else {}


async def patch_filament(
    url: str, api_key: Optional[str], filament_id: int, orca_profiles: dict
) -> dict:
    """Merge orca_profiles into filament's extra field and PATCH Spoolman."""
    headers = _headers(api_key)
    base = url.rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        get_resp = await client.get(f"{base}/api/v1/filament/{filament_id}", headers=headers)
        get_resp.raise_for_status()
        existing_extra: dict = get_resp.json().get("extra") or {}

        merged_extra = {**existing_extra, "orca_profiles": _json.dumps(orca_profiles)}

        patch_resp = await client.patch(
            f"{base}/api/v1/filament/{filament_id}",
            json={"extra": merged_extra},
            headers=headers,
        )
        patch_resp.raise_for_status()
        return patch_resp.json()
