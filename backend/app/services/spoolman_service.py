from __future__ import annotations
from typing import Optional
import httpx


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
