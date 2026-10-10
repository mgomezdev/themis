"""HTTP MJPEG helpers for the camera fan-out (BIZ-251). Vendor-agnostic: a client whose feed is a plain MJPEG URL uses these through
`AbstractPrinterClient.camera_stream` / `camera_snapshot`; a vendor that needs a transcode (Bambu) does it in its own plugin."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx

CHUNK_SIZE = 8192
_MAX_SNAPSHOT_BYTES = 2_000_000  # 2 MB


async def stream_mjpeg(url: str) -> AsyncGenerator[bytes, None]:
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", url) as response:
            async for chunk in response.aiter_bytes(CHUNK_SIZE):
                yield chunk


async def grab_jpeg_frame(url: str, timeout: float = 8.0) -> bytes:
    """Connect to an MJPEG URL and return the first complete JPEG frame."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", url) as response:
            buf = b""
            soi = -1
            async for chunk in response.aiter_bytes(CHUNK_SIZE):
                buf += chunk
                if soi == -1:
                    soi = buf.find(b'\xff\xd8')
                if soi != -1:
                    eoi = buf.find(b'\xff\xd9', soi + 2)
                    if eoi != -1:
                        return buf[soi:eoi + 2]
                if len(buf) > _MAX_SNAPSHOT_BYTES:
                    raise ValueError("JPEG frame exceeds size limit")
    raise ValueError("No complete JPEG frame found in stream")
