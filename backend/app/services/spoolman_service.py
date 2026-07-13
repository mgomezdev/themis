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
    headers = _headers(api_key)
    base = url.rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        patch_resp = await client.patch(
            f"{base}/api/v1/filament/{filament_id}",
            json={"extra": {"orca_profiles": _json.dumps(_json.dumps(orca_profiles))}},
            headers=headers,
        )
        if not patch_resp.is_success:
            raise httpx.HTTPStatusError(
                f"{patch_resp.status_code}: {patch_resp.text}",
                request=httpx.Request("PATCH", f"{base}/api/v1/filament/{filament_id}"),
                response=patch_resp,
            )
        return patch_resp.json()


async def record_spool_use(
    url: str, api_key: Optional[str], spool_id: int, grams: float
) -> None:
    """PUT /api/v1/spool/{spool_id}/use — records filament consumption."""
    headers = _headers(api_key)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.put(
            f"{url.rstrip('/')}/api/v1/spool/{spool_id}/use",
            json={"use_weight": grams},
            headers=headers,
        )
        resp.raise_for_status()
